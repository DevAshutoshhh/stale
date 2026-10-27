"""`stale` CLI (SRS §8). Exit codes: 0 ok · 1 policy failure · 2 infrastructure error · 3 deferred."""

from __future__ import annotations

import json
import re
import statistics
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import typer
import yaml
from pydantic import ValidationError

from . import collectors, config, issue_format, policy, rules, state
from .fingerprint import fingerprint
from .gitlab_api import GitLab, GitLabError
from .migrate import runner
from .models import Claim, EvidenceFile, Finding, Lockfile
from .reproof import canary

EXIT_OK, EXIT_POLICY, EXIT_INFRA, EXIT_DEFER = 0, 1, 2, 3
LOCKFILE = "claims.lock.json"
DEFAULT_MIGRATIONS = Path("app/ledger-api/db/migrations")
ANALYST_MARKER = "[stale:analyst]"  # flows may not emit HTML comments
ROLLBACK_MARKER = "<!-- stale:rollback -->"
RETRY_WAIT = timedelta(minutes=10)
STALLED_MERGE = timedelta(minutes=5)  # green pipeline + auto-merge set, still open this long => merge
RELEASE_SCHEDULE = "release"
SESSION_FAILED = re.compile(r"^session \[\d+\]\(.*\) failed")
MAX_FAILED_SESSIONS = 3

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
migrate_app = typer.Typer(no_args_is_help=True, help="Plain-SQL migration runner (SRS §10).")
app.add_typer(migrate_app, name="migrate")


@dataclass
class Ctx:
    claims_dir: Path
    out: Path
    json: bool

    @property
    def stale_dir(self) -> Path:
        return self.claims_dir.parent

    @property
    def repo_root(self) -> Path:
        return self.claims_dir.resolve().parent.parent

    @property
    def lockfile(self) -> Path:
        return self.stale_dir / LOCKFILE


CTX: Ctx = Ctx(Path(".stale/claims"), Path(".stale/out"), False)


@app.callback()
def main(claims_dir: Path = typer.Option(Path(".stale/claims"), "--claims-dir"),
         out: Path = typer.Option(Path(".stale/out"), "--out"),
         as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON.")) -> None:
    global CTX
    CTX = Ctx(claims_dir, out, as_json)


def guarded(fn: Callable) -> Callable:
    """Map infrastructure errors to exit 2 with a one-line message."""

    @wraps(fn)
    def wrapper(*a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except typer.Exit:
            raise
        except (config.ConfigError, GitLabError, OSError, RuntimeError) as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(EXIT_INFRA)

    return wrapper


def emit(obj: Any, text: str | None = None) -> None:
    if CTX.json or text is None:
        typer.echo(json.dumps(obj, indent=2, default=str))
    else:
        typer.echo(text)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------------------- loading
def load_claims(enabled_only: bool = True) -> list[Claim]:
    claims = []
    for path in sorted(CTX.claims_dir.glob("*.yaml")):
        claim = Claim.model_validate(yaml.safe_load(path.read_text()))
        if claim.id != path.stem:
            raise ValueError(f"{path.name}: metadata.id {claim.id!r} must equal the file stem")
        if claim.metadata.enabled or not enabled_only:
            claims.append(claim)
    return claims


def load_lock() -> Lockfile:
    return Lockfile.model_validate_json(CTX.lockfile.read_text())


def write_json(name: str, obj: Any) -> Path:
    CTX.out.mkdir(parents=True, exist_ok=True)
    path = CTX.out / name
    path.write_text(json.dumps(obj, indent=2, default=str) + "\n")
    return path


def read_evidence(target: str, path: Optional[Path]) -> EvidenceFile:
    path = path or CTX.out / f"evidence-{target}.json"
    if not path.exists():
        raise RuntimeError(f"evidence file {path} not found; run `stale collect --target {target}` first")
    return EvidenceFile.model_validate_json(path.read_text())


@contextmanager
def maybe_db(database_url: Optional[str]) -> Iterator[Any]:
    url = database_url
    if url is None:
        try:
            url = config.database_url()
        except config.ConfigError:
            url = None
    if url is None:
        yield None
        return
    import psycopg

    try:
        conn = psycopg.connect(url, autocommit=True, connect_timeout=10)
    except psycopg.Error as e:
        raise RuntimeError(f"database connection failed: {e}") from e
    with conn:
        yield conn


def gitlab_or_none() -> GitLab | None:
    try:
        return GitLab.from_env()
    except config.ConfigError:
        return None


def open_claim_issues(gl: GitLab) -> dict[str, list[dict]]:
    by_claim: dict[str, list[dict]] = {}
    for issue in gl.list_issues(state="opened"):
        for cid in state.claim_of(issue.get("labels", [])):
            by_claim.setdefault(cid, []).append(issue)
    return by_claim


# ------------------------------------------------------------------------------------- evidence
def collect_claim(claim: Claim, target: str, *, runtime_url: str | None, runtime_file: str | None,
                  conn: Any, migrations_dir: Path) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    errors: list[str] = []
    for spec in claim.spec.evidence:
        cfg = {k: config.expand(v) if isinstance(v, str) else v for k, v in spec.config.items()}
        try:
            if spec.collector == "runtime_report":
                evidence["runtime_report"] = collectors.runtime_report(
                    url=runtime_url if target == "serving" else None,
                    file=runtime_file if target == "candidate" else None)
            elif spec.collector == "node_feed":
                ver = (evidence.get("runtime_report") or {}).get("node_version")
                evidence["node_feed"] = collectors.node_feed(cfg, ver)
            elif spec.collector == "root_feed":
                evidence["root_feed"] = collectors.root_feed(cfg)
            elif spec.collector == "pending_migrations":
                if conn is None:
                    raise RuntimeError("no database connection (set DATABASE_URL or DB_*)")
                evidence["pending_migrations"] = collectors.pending_migrations(
                    conn, migrations_dir, CTX.repo_root)
            elif spec.collector == "pg_stats":
                if conn is None:
                    raise RuntimeError("no database connection (set DATABASE_URL or DB_*)")
                evidence["pg_stats"] = collectors.pg_stats(conn)
                evidence["threshold"] = config.row_threshold()
        except Exception as e:  # recorded, not raised: §8 collect semantics
            errors.append(f"{spec.collector}: {e.__class__.__name__}: {e}")
    findings: list[Finding] = []
    if len(errors) < len(claim.spec.evidence):
        findings = rules.evaluate(claim, evidence)
    return {"evidence": evidence,
            "fingerprint": fingerprint(evidence, claim.spec.fingerprint.include),
            "findings": [f.model_dump() for f in findings], "errors": errors}


# ------------------------------------------------------------------------------------- commands
@app.command()
@guarded
def validate() -> None:
    """Validate claim files, playbooks, allowPaths globs and the lockfile."""
    problems: list[str] = []
    try:
        claims = load_claims(enabled_only=False)
    except (ValidationError, ValueError, yaml.YAMLError) as e:
        typer.echo(f"invalid claim file: {e}", err=True)
        raise typer.Exit(EXIT_POLICY)
    if not claims:
        problems.append(f"no claim files in {CTX.claims_dir}")
    for c in claims:
        if not (CTX.repo_root / c.spec.remediation.playbook).is_file():
            problems.append(f"{c.id}: playbook {c.spec.remediation.playbook} not found")
        for g in c.spec.remediation.allowPaths:
            if not policy.valid_glob(g):
                problems.append(f"{c.id}: invalid allowPaths glob {g!r}")
            elif policy.forbidden(g) or g.startswith(policy.FORBIDDEN_PREFIXES):
                problems.append(f"{c.id}: allowPaths glob {g!r} covers a protected path")
    try:
        lock = load_lock()
        for c in claims:
            if c.metadata.enabled and c.id not in lock.claims:
                problems.append(f"{c.id}: no entry in {CTX.lockfile}")
    except (OSError, ValidationError) as e:
        problems.append(f"lockfile: {e}")
    if problems:
        for p in problems:
            typer.echo(f"FAIL {p}", err=True)
        raise typer.Exit(EXIT_POLICY)
    emit({"ok": True, "claims": [c.id for c in claims]},
         f"OK: {len(claims)} claim(s) valid: {', '.join(c.id for c in claims)}")


@app.command()
@guarded
def collect(target: str = typer.Option(..., "--target", help="serving|candidate"),
            claim: Optional[str] = typer.Option(None, "--claim"),
            runtime_url: Optional[str] = typer.Option(None, "--runtime-url",
                                                      help="serving: tag URL of the stable revision"),
            runtime_file: Optional[str] = typer.Option(None, "--runtime-file",
                                                       help="candidate: candidate-runtime.json"),
            database_url: Optional[str] = typer.Option(None, "--database-url"),
            migrations_dir: Path = typer.Option(DEFAULT_MIGRATIONS, "--migrations-dir")) -> None:
    """Run collectors and write .stale/out/evidence-<target>.json."""
    if target not in ("serving", "candidate"):
        raise typer.BadParameter("--target must be serving or candidate")
    claims = [c for c in load_claims() if target in c.spec.targets and (claim is None or c.id == claim)]
    out: dict[str, Any] = {}
    with maybe_db(database_url) as conn:
        for c in claims:
            out[c.id] = collect_claim(c, target, runtime_url=runtime_url, runtime_file=runtime_file,
                                      conn=conn, migrations_dir=migrations_dir)
    ev = EvidenceFile.model_validate({"schema": "stale.evidence/v2", "target": target,
                                      "collected_at": now_iso(), "claims": out})
    path = write_json(f"evidence-{target}.json", ev.model_dump(by_alias=True))
    lines = [f"wrote {path}"]
    failed = []
    for cid, ce in ev.claims.items():
        lines.append(f"  {cid}: {len(ce.findings)} finding(s) "
                     f"[{', '.join(f.rule for f in ce.findings) or '-'}] errors={len(ce.errors)}")
        for e in ce.errors:
            lines.append(f"    ! {e}")
        if ce.errors and len(ce.errors) == len(next(c for c in claims if c.id == cid).spec.evidence):
            failed.append(cid)
    emit(ev.model_dump(by_alias=True), "\n".join(lines))
    if failed:
        typer.echo(f"error: every collector failed for {', '.join(failed)}", err=True)
        raise typer.Exit(EXIT_INFRA)


@app.command()
@guarded
def check(target: str = typer.Option("serving", "--target"),
          open_issues: bool = typer.Option(False, "--open-issues"),
          dry_run: bool = typer.Option(False, "--dry-run"),
          evidence: Optional[Path] = typer.Option(None, "--evidence")) -> None:
    """Evaluate rules on serving evidence; open claim issues for new findings. Exits 0."""
    ev = read_evidence(target, evidence)
    claims = {c.id: c for c in load_claims()}
    lock = load_lock()
    gl = GitLab.from_env() if open_issues and not dry_run else gitlab_or_none() if open_issues else None
    issues = open_claim_issues(gl) if gl else {}
    report = ["# Stale check report", "", f"Target: `{target}` · collected {ev.collected_at}", "",
              "| Claim | State | Findings | Fingerprint | Action |", "|---|---|---|---|---|"]
    infra_error = False
    for cid, c in claims.items():
        ce = ev.claims.get(cid)
        if ce is None:
            report.append(f"| {cid} | ? | not collected | - | - |")
            continue
        if ce.errors and not ce.evidence:
            infra_error = True
        findings = rules.evaluate(c, ce.evidence) if ce.evidence else []
        issue = (issues.get(cid) or [None])[0]
        st = state.derive(issue.get("labels") if issue else None, bool(findings))
        action = "-"
        if findings and issue is None:
            title, body = issue_format.render(c, findings, ce.evidence, rules.facts(c, findings),
                                              lock.claims.get(cid))
            if open_issues and not dry_run:
                created = gl.create_issue(title, body, [state.claim_label(cid), state.state_label("stale")])
                action = f"opened #{created['iid']}"
            else:
                action = f"would open: {title}"
        elif issue is not None:
            action = f"issue #{issue['iid']} open"
        rules_txt = ", ".join(f"{f.rule}({f.severity})" for f in findings) or "none"
        report.append(f"| {cid} | {st.value} | {rules_txt} | `{(ce.fingerprint or '')[:19]}…` | {action} |")
        for e in ce.errors:
            report.append(f"|  | | collector error: {e} | | |")
    text = "\n".join(report) + "\n"
    CTX.out.mkdir(parents=True, exist_ok=True)
    (CTX.out / "check-report.md").write_text(text)
    typer.echo(text)
    if infra_error:
        raise typer.Exit(EXIT_INFRA)


def _note_times(notes: list[dict], marker: str) -> list[datetime]:
    return [datetime.fromisoformat(n["created_at"].replace("Z", "+00:00"))
            for n in notes if (n.get("body") or "").lstrip().startswith(marker)]


@app.command()
@guarded
def dispatch() -> None:
    """Assign the remediator to ::stale issues; re-dispatch ::retry-pending ones. Idempotent."""
    gl = GitLab.from_env()
    remediator = int(config.require("STALE_REMEDIATOR_USER_ID")["STALE_REMEDIATOR_USER_ID"])
    actions = []
    released = False  # one release re-proves every claim, so play it at most once per run
    for cid, issues in sorted(open_claim_issues(gl).items()):
        for issue in issues:
            labels, iid = issue.get("labels", []), issue["iid"]
            values = state.state_values(labels)
            if "stale" in values:
                t = state.transition("remediating")
                n = max(state.attempt(labels), 0) + 1
                gl.update_issue(iid, assignee_ids=[remediator],
                                add_labels=t["add_labels"] + [f"{state.ATTEMPT_PREFIX}{n}"],
                                remove_labels=t["remove_labels"])
                actions.append(f"#{iid} {cid}: assigned remediator, attempt {n}")
            elif "remediating" in values:
                # Recover from a Duo session that failed before opening an MR (e.g. "failed (dropped)").
                notes = gl.issue_notes(iid)
                failed = [n for n in notes if n.get("system") and SESSION_FAILED.search(n.get("body") or "")]
                last_assign = max((n["created_at"] for n in notes if n.get("system")
                                   and (n.get("body") or "").startswith("assigned to @")), default="")
                if not failed or failed[-1]["created_at"] < last_assign:
                    continue
                if any(state.REMEDIATION_LABEL in (m.get("labels") or []) for m in gl.issue_related_mrs(iid)):
                    continue
                if len(failed) >= MAX_FAILED_SESSIONS:
                    quarantine(gl, iid, f"The remediator's Duo session failed {len(failed)} times without an MR.")
                    actions.append(f"#{iid} {cid}: quarantined after {len(failed)} failed sessions")
                    continue
                gl.update_issue(iid, assignee_ids=[])
                gl.update_issue(iid, assignee_ids=[remediator])
                gl.add_issue_note(iid, f"The remediator's Duo session failed before opening a merge request "
                                       f"({len(failed)} of {MAX_FAILED_SESSIONS}); re-dispatching.")
                actions.append(f"#{iid} {cid}: session failed, re-dispatched ({len(failed)}/{MAX_FAILED_SESSIONS})")
            elif "reproving" in values:
                # GitLab's auto-merge can stall when the target branch moves while it merges (two remediation
                # MRs finishing together). A green head pipeline means verify-mr passed for that exact sha.
                for m in gl.issue_related_mrs(iid):
                    if m.get("state") != "opened" or state.REMEDIATION_LABEL not in (m.get("labels") or []):
                        continue
                    mr = gl.get_mr(m["iid"])
                    hp = mr.get("head_pipeline") or {}
                    done = hp.get("finished_at")
                    if not (mr.get("merge_when_pipeline_succeeds") and hp.get("status") == "success" and done
                            and mr.get("detailed_merge_status") == "mergeable"):
                        continue
                    if datetime.now(timezone.utc) - datetime.fromisoformat(done.replace("Z", "+00:00")) < STALLED_MERGE:
                        continue
                    gl.merge_mr(m["iid"], mr["sha"])
                    gl.add_mr_note(m["iid"], f"Auto-merge stalled after a green pipeline ({hp.get('id')}); "
                                             f"merged by `stale dispatch` at the same sha.")
                    actions.append(f"#{iid} {cid}: auto-merge of !{m['iid']} stalled, merged")
            elif "retry-pending" in values:
                notes = gl.issue_notes(iid)
                rolled = max(_note_times(notes, ROLLBACK_MARKER), default=None)
                analyst = [t for t in _note_times(notes, ANALYST_MARKER) if rolled is None or t > rolled]
                waited = rolled is not None and datetime.now(timezone.utc) - rolled >= RETRY_WAIT
                if not (analyst or waited or rolled is None):
                    actions.append(f"#{iid} {cid}: retry pending (waiting for analyst note or 10 min)")
                    continue
                merged = [m for m in gl.issue_related_mrs(iid)
                          if m.get("state") == "merged" and state.REMEDIATION_LABEL in (m.get("labels") or [])]
                if merged:
                    # The fix is already on main; only its rollout failed. Retry = release again.
                    t = state.transition("reproving")
                    if not released:
                        gl.play_schedule(RELEASE_SCHEDULE)
                        released = True
                    gl.update_issue(iid, add_labels=t["add_labels"], remove_labels=t["remove_labels"])
                    gl.add_issue_note(iid, f"Retry {state.attempt(labels)}: the remediation "
                                      f"({', '.join('!' + str(m['iid']) for m in merged)}) is already on main, "
                                      f"so the retry is a new release (schedule `{RELEASE_SCHEDULE}`).")
                    actions.append(f"#{iid} {cid}: retry {state.attempt(labels)} = re-release")
                    continue
                t = state.transition("remediating")
                gl.update_issue(iid, assignee_ids=[])
                gl.update_issue(iid, assignee_ids=[remediator], add_labels=t["add_labels"],
                                remove_labels=t["remove_labels"])
                actions.append(f"#{iid} {cid}: re-dispatched attempt {state.attempt(labels)}")
    emit({"actions": actions}, "\n".join(actions) or "nothing to dispatch")


def gate_decision(claims: list[Claim], ev: EvidenceFile, issues: dict[str, list[dict]]
                  ) -> tuple[str, dict[str, Any]]:
    per: dict[str, Any] = {}
    deferred = blocked = False
    for c in claims:
        ce = ev.claims.get(c.id)
        findings = rules.evaluate(c, ce.evidence) if ce and ce.evidence else []
        if ce is None or (ce.errors and not ce.evidence):
            per[c.id] = {"result": "block", "reason": "no candidate evidence"}
            blocked = True
            continue
        issue = (issues.get(c.id) or [None])[0]
        st = state.derive(issue.get("labels") if issue else None, bool(findings))
        entry = {"state": st.value, "findings": [f.model_dump() for f in findings]}
        if st == state.State.QUARANTINED:
            entry["result"] = "block"
            blocked = True
        elif not findings:
            entry["result"] = "pass"
        elif st in (state.State.REMEDIATING, state.State.REPROVING):
            entry["result"] = "defer"
            deferred = True
        else:
            entry["result"] = "block"
            blocked = True
        per[c.id] = entry
    result = "block" if blocked else "defer" if deferred else "pass"
    return result, per


@app.command()
@guarded
def gate(target: str = typer.Option("candidate", "--target"),
         evidence: Optional[Path] = typer.Option(None, "--evidence")) -> None:
    """pass (0) / defer (3) / block (1) on candidate evidence (SRS §7.3). Writes gate.json."""
    ev = read_evidence(target, evidence)
    gl = GitLab.from_env()
    result, per = gate_decision(load_claims(), ev, open_claim_issues(gl))
    write_json("gate.json", {"result": result, "claims": per, "decided_at": now_iso()})
    emit({"result": result, "claims": per},
         f"gate: {result}\n" + "\n".join(f"  {k}: {v['result']} ({v.get('state', '-')})" for k, v in per.items()))
    raise typer.Exit({"pass": EXIT_OK, "defer": EXIT_DEFER, "block": EXIT_POLICY}[result])


def quarantine(gl: GitLab, issue_iid: int, reason: str) -> None:
    human = config.get("STALE_HUMAN_ESCALATION_USER_ID")
    t = state.transition("quarantined")
    gl.update_issue(issue_iid, assignee_ids=[int(human)] if human else [],
                    add_labels=t["add_labels"], remove_labels=t["remove_labels"])
    gl.add_issue_note(issue_iid, "<!-- stale:quarantine -->\n**Quarantined.** A human must review.\n\n" + reason)


@app.command("verify-mr")
@guarded
def verify_mr(mr_iid: int = typer.Option(..., "--mr-iid")) -> None:
    """Policy-check a remediation MR, then enable auto-merge (SRS §15.4). Exit 1 quarantines."""
    gl = GitLab.from_env()
    username = config.require("STALE_REMEDIATOR_USERNAME")["STALE_REMEDIATOR_USERNAME"]
    claims = {c.id: c for c in load_claims()}
    issues = open_claim_issues(gl)
    mr = gl.get_mr(mr_iid)
    labelled = state.claim_of(mr.get("labels") or [])
    notes = [n for cid in labelled for i in issues.get(cid, []) for n in gl.issue_notes(i["iid"])]
    claim_id, violations = policy.verify(
        mr, gl.mr_diffs(mr_iid), remediator_username=username,
        allow_paths_by_claim={k: c.spec.remediation.allowPaths for k, c in claims.items()},
        open_issue_iids_by_claim={k: [i["iid"] for i in v] for k, v in issues.items()},
        commits=gl.mr_commits(mr_iid), claim_issue_notes=notes)
    if violations:
        text = "\n".join(f"- {v}" for v in violations)
        gl.add_mr_note(mr_iid, f"**stale verify-mr: REJECTED**\n\n{text}")
        for issue in issues.get(claim_id or "", []):
            quarantine(gl, issue["iid"], f"verify-mr rejected !{mr_iid}:\n\n{text}")
        emit({"ok": False, "violations": violations}, "REJECTED\n" + text)
        raise typer.Exit(EXIT_POLICY)
    gl.set_auto_merge(mr_iid, mr.get("sha"))
    gl.add_mr_note(mr_iid, "**stale verify-mr: OK** — paths within allowPaths; auto-merge enabled "
                           "(merges only if the whole pipeline succeeds).")
    emit({"ok": True, "claim": claim_id}, f"OK: !{mr_iid} ({claim_id}) — auto-merge enabled")


def _median_job_seconds(gl: GitLab | None, job_name: str) -> float:
    if gl is None:
        return 0.0
    durations = [j["duration"] for j in gl.list_jobs("success")
                 if j.get("name") == job_name and j.get("duration")][:5]
    return float(statistics.median(durations)) if durations else 0.0


@app.command()
@guarded
def reprove(claim: Optional[str] = typer.Option(None, "--claim"),
            stage: str = typer.Option(..., "--stage", help="pre-deploy|canary"),
            force: bool = typer.Option(False, "--force"),
            canary_url: Optional[str] = typer.Option(None, "--canary-url"),
            expect_revision: Optional[str] = typer.Option(None, "--expect-revision"),
            evidence: Optional[Path] = typer.Option(None, "--evidence"),
            database_url: Optional[str] = typer.Option(None, "--database-url",
                                                       help="shadow database (postgres:18 service)"),
            migrations_dir: Path = typer.Option(DEFAULT_MIGRATIONS, "--migrations-dir")) -> None:
    """Pre-deploy proofs for one claim, or canary probes against the canary tag URL (SRS §12)."""
    claims = {c.id: c for c in load_claims()}
    if stage == "pre-deploy":
        if not claim or claim not in claims:
            raise typer.BadParameter("--claim must name an enabled claim")
        c = claims[claim]
        ev = read_evidence("candidate", evidence)
        ce = ev.claims.get(claim)
        if ce is None:
            raise RuntimeError(f"no candidate evidence for {claim}")
        minted = load_lock().claims.get(claim)
        if not force and minted and minted.fingerprint and ce.fingerprint == minted.fingerprint:
            job = f"reprove:{claim}"
            secs = _median_job_seconds(gitlab_or_none(), job)
            write_json("sci-skip.json", {"job": job, "claim": claim, "seconds_avoided": secs})
            typer.echo(f"[SKIP] {claim}: candidate fingerprint equals minted {minted.fingerprint}")
            return
        failed: list[str] = []
        for proof in c.spec.reproof.preDeploy:
            if proof == "image_runtime_report":
                findings = rules.evaluate(c, ce.evidence)
                for f in findings:
                    typer.echo(f"  FAIL {f.rule} ({f.severity}): {json.dumps(f.detail)[:300]}")
                if findings:
                    failed.append(proof)
                else:
                    typer.echo(f"  PASS {proof}: rules {', '.join(c.spec.rules)} pass on candidate evidence")
            elif proof == "shadow_migration":
                from .reproof import shadow_migration

                url = database_url or config.get("DATABASE_URL")
                if not url:
                    raise config.ConfigError("shadow_migration needs --database-url or DATABASE_URL")
                pending = {m["version"] for m in ce.evidence.get("pending_migrations") or []}
                applied = {f.version for f in runner.load_dir(migrations_dir)} - pending
                rep = shadow_migration.run(url, migrations_dir, threshold=config.row_threshold(),
                                           applied_versions=applied, out_dir=CTX.out, log=typer.echo)
                typer.echo(f"  {'PASS' if rep['passed'] else 'FAIL'} shadow_migration: "
                           f"{'; '.join(rep['failures']) or 'no blocking lock, writer p99 ok, no invalid index'}")
                if not rep["passed"]:
                    failed.append(proof)
            else:
                raise RuntimeError(f"unknown preDeploy proof {proof!r}")
        if failed:
            typer.echo(f"reprove {claim}: FAILED ({', '.join(failed)})")
            raise typer.Exit(EXIT_POLICY)
        typer.echo(f"reprove {claim}: PASSED")
        return

    if stage != "canary":
        raise typer.BadParameter("--stage must be pre-deploy or canary")
    if not canary_url:
        raise typer.BadParameter("--canary-url is required for --stage canary")
    gl = gitlab_or_none()
    open_ids = set(open_claim_issues(gl)) if gl else set()
    if claim:
        open_ids.add(claim)
    probes = ["runtime_report_matches"]
    for cid in sorted(open_ids & set(claims)):
        probes += claims[cid].spec.reproof.canary
    anchor = claims.get("anchor-trusted")

    def rules_check(report: dict) -> list[Finding]:
        if anchor is None:
            return []
        ev = {"runtime_report": report}
        for spec in anchor.spec.evidence:
            cfg = {k: config.expand(v) if isinstance(v, str) else v for k, v in spec.config.items()}
            if spec.collector == "node_feed":
                ev["node_feed"] = collectors.node_feed(cfg, report.get("node_version"))
            elif spec.collector == "root_feed":
                ev["root_feed"] = collectors.root_feed(cfg)
        return rules.evaluate(anchor, ev)

    files = runner.load_dir(migrations_dir) if migrations_dir.exists() else []
    results = canary.run(canary_url, probes, expect_revision=expect_revision, rules_check=rules_check,
                         min_schema_version=files[-1].version if files else None)
    write_json("canary-report.json", {"url": canary_url, "results": [r.to_dict() for r in results]})
    for r in results:
        typer.echo(f"  {'PASS' if r.passed else 'FAIL'} {r.probe}: {json.dumps(r.detail)[:300]}")
    if not all(r.passed for r in results):
        raise typer.Exit(EXIT_POLICY)


@migrate_app.command("plan")
@guarded
def migrate_plan(database_url: str = typer.Option(..., "--database-url"),
                 dir: Path = typer.Option(DEFAULT_MIGRATIONS, "--dir")) -> None:
    """Print pending files and M-rule findings using live statistics. Executes nothing."""
    claim = next((c for c in load_claims() if any(r.startswith("M") for r in c.spec.rules)), None)
    with maybe_db(database_url) as conn:
        evidence = {"pending_migrations": collectors.pending_migrations(conn, dir, CTX.repo_root),
                    "pg_stats": collectors.pg_stats(conn), "threshold": config.row_threshold()}
    findings = rules.evaluate(claim, evidence) if claim else []
    lines = [f"pending: {len(evidence['pending_migrations'])} file(s)"]
    for m in evidence["pending_migrations"]:
        lines.append(f"  {m['file']} ({'no-transaction' if m['no_transaction'] else 'transaction'})")
        for s in m["statements"]:
            lines.append(f"    {s['lock_mode'] or '-':24} {s['sql_normalized']}")
    lines.append(f"findings: {', '.join(f.rule for f in findings) or 'none'}")
    for f in findings:
        lines.append(f"  {f.rule}: {f.detail.get('sql') or f.detail}")
    emit({"evidence": evidence, "findings": [f.model_dump() for f in findings]}, "\n".join(lines))


@migrate_app.command("apply")
@guarded
def migrate_apply(database_url: str = typer.Option(..., "--database-url"),
                  dir: Path = typer.Option(DEFAULT_MIGRATIONS, "--dir")) -> None:
    """Apply pending migrations under an advisory lock (SRS §10). Exit 1 on failure/INVALID index."""
    import psycopg

    try:
        applied = runner.apply(database_url, dir, log=typer.echo)
    except runner.MigrationError as e:
        typer.echo(f"migration failed: {e}", err=True)
        raise typer.Exit(EXIT_POLICY)
    except psycopg.OperationalError as e:
        raise RuntimeError(f"database unavailable: {e}") from e
    emit({"applied": applied}, f"done: {len(applied)} migration(s) applied")


def _commit_lockfile(gl: GitLab, mutate: Callable[[Lockfile], None], message: str) -> str:
    branch = config.get("CI_DEFAULT_BRANCH") or "main"
    path = f".stale/{LOCKFILE}"
    for attempt in range(1, 4):
        content, last = gl.get_file(path, branch)
        lock = Lockfile.model_validate_json(content)
        mutate(lock)
        body = json.dumps(lock.model_dump(by_alias=True), indent=2) + "\n"
        try:
            res = gl.commit_files(branch, message, [{"action": "update", "file_path": path,
                                                     "content": body, "last_commit_id": last}])
            CTX.lockfile.write_text(body)
            return res["id"]
        except GitLabError as e:
            if e.status not in (400, 409) or attempt == 3:
                raise
    raise AssertionError("unreachable")


@app.command()
@guarded
def mint(revision: str = typer.Option(..., "--revision"),
         runtime_url: Optional[str] = typer.Option(None, "--runtime-url",
                                                   help="stable tag URL (now serving REVISION)"),
         database_url: Optional[str] = typer.Option(None, "--database-url"),
         migrations_dir: Path = typer.Option(DEFAULT_MIGRATIONS, "--migrations-dir"),
         sci_dir: Optional[Path] = typer.Option(None, "--sci-dir",
                                                help="directory of sci-skip.json artifacts")) -> None:
    """Re-collect serving evidence; mint passing claims, close their issues, commit the lockfile."""
    gl = GitLab.from_env()
    claims = load_claims()
    issues = open_claim_issues(gl)
    minted: dict[str, Any] = {}
    still: dict[str, list[str]] = {}
    with maybe_db(database_url) as conn:
        for c in claims:
            if "serving" not in c.spec.targets:
                continue
            ce = collect_claim(c, "serving", runtime_url=runtime_url, runtime_file=None, conn=conn,
                               migrations_dir=migrations_dir)
            if ce["errors"]:
                still[c.id] = ce["errors"]
                continue
            if ce["findings"]:
                still[c.id] = [f["rule"] for f in ce["findings"]]
                continue
            issue = (issues.get(c.id) or [None])[0]
            mrs = [m["iid"] for m in gl.issue_related_mrs(issue["iid"])] if issue else []
            minted[c.id] = {"fingerprint": ce["fingerprint"], "minted_at": now_iso(),
                            "pipeline_id": int(config.get("CI_PIPELINE_ID") or 0) or None,
                            "job_id": int(config.get("CI_JOB_ID") or 0) or None, "revision": revision,
                            "commit_sha": config.get("CI_COMMIT_SHA"),
                            "issue_iid": issue["iid"] if issue else None, "mr_iids": mrs}
    skips = [json.loads(p.read_text()) for p in sorted(sci_dir.rglob("sci-skip.json"))] if sci_dir else []

    def mutate(lock: Lockfile) -> None:
        for cid, entry in minted.items():
            lock.claims[cid] = lock.claims[cid].model_validate(entry)
        lock.sci.reproofs_skipped += len(skips)
        lock.sci.runner_seconds_avoided += sum(float(s.get("seconds_avoided") or 0) for s in skips)

    sha = None
    if minted or skips:
        sha = _commit_lockfile(gl, mutate, f"chore(stale): mint {', '.join(sorted(minted)) or 'sci'} "
                                           f"on {revision} [skip ci]")
    for cid, entry in minted.items():
        if entry["issue_iid"]:
            iid = entry["issue_iid"]
            gl.add_issue_note(iid, "\n".join([
                "<!-- stale:mint -->", f"## Re-minted: `{cid}` is VALID again", "",
                f"- Serving revision: `{revision}` (100% traffic, canary probes passed)",
                f"- Fingerprint: `{entry['fingerprint']}`",
                f"- Merge requests: {', '.join(f'!{m}' for m in entry['mr_iids']) or '-'}",
                f"- Pipeline: {entry['pipeline_id'] or '-'} · lockfile commit: `{sha or '-'}`",
                "", "Closed by `stale mint`. No human action was needed."]))
            gl.update_issue(iid, state_event="close", assignee_ids=[],
                            remove_labels=[state.state_label(v) for v in state.STATE_VALUES])
    emit({"minted": minted, "not_minted": still, "commit": sha},
         "\n".join([f"minted {k} on {revision}" for k in minted] +
                   [f"not minted {k}: {v}" for k, v in still.items()] + [f"lockfile commit: {sha}"]))


@app.command("rollback-transition")
@guarded
def rollback_transition(reason: Path = typer.Option(..., "--reason")) -> None:
    """Retry or quarantine every claim whose issue is ::reproving (SRS §6.4)."""
    gl = GitLab.from_env()
    claims = {c.id: c for c in load_claims()}
    why = reason.read_text() if reason.exists() else "{}"
    actions = []
    for cid, issues in open_claim_issues(gl).items():
        for issue in issues:
            labels = issue.get("labels", [])
            if "reproving" not in state.state_values(labels):
                continue
            n = state.attempt(labels)
            limit = claims[cid].spec.remediation.maxAttemptsPer24h if cid in claims else 1
            if n < limit:
                t = state.transition("retry-pending")
                gl.update_issue(issue["iid"], add_labels=t["add_labels"] + [f"{state.ATTEMPT_PREFIX}{n + 1}"],
                                remove_labels=t["remove_labels"])
                gl.add_issue_note(issue["iid"], f"{ROLLBACK_MARKER}\n**Canary failed; traffic restored to "
                                  f"the stable revision.** Retry {n + 1}/{limit} pending.\n\n```json\n{why}\n```")
                actions.append(f"#{issue['iid']} {cid}: retry-pending (attempt {n + 1})")
            else:
                quarantine(gl, issue["iid"], f"Attempts exhausted ({n}/{limit}).\n\n```json\n{why}\n```")
                actions.append(f"#{issue['iid']} {cid}: quarantined")
    emit({"actions": actions}, "\n".join(actions) or "no claim issue in ::reproving")


@app.command("mark-reproving")
@guarded
def mark_reproving(mr_iid: int = typer.Option(..., "--mr-iid")) -> None:
    """MR pipeline started for a remediation MR: move its claim issue to ::reproving (idempotent)."""
    gl = GitLab.from_env()
    mr = gl.get_mr(mr_iid)
    labels = mr.get("labels") or []
    claims = state.claim_of(labels)
    if state.REMEDIATION_LABEL not in labels or len(claims) != 1:
        typer.echo(f"!{mr_iid} is not a single-claim remediation MR; nothing to do")
        return
    moved = []
    for issue in open_claim_issues(gl).get(claims[0], []):
        values = state.state_values(issue.get("labels", []))
        if values & {"remediating", "retry-pending"} and "quarantined" not in values:
            t = state.transition("reproving")
            gl.update_issue(issue["iid"], add_labels=t["add_labels"], remove_labels=t["remove_labels"])
            moved.append(issue["iid"])
    typer.echo(f"{claims[0]}: " + (", ".join(f"#{i} -> reproving" for i in moved) or "already reproving or not open"))


def _plan_yaml(job: str, lines: list[str], fail: bool = False) -> str:
    script = [f"echo {json.dumps(line)}" for line in lines] + (["exit 1"] if fail else [])
    return yaml.safe_dump({"stages": ["release"], job: {"stage": "release", "image": "alpine:3.20",
                                                        "variables": {"GIT_STRATEGY": "none"},
                                                        "script": script}}, sort_keys=False)


@app.command("plan-release")
@guarded
def plan_release(sha: Optional[str] = typer.Option(None, "--sha", help="default: CI_COMMIT_SHA"),
                 out: Path = typer.Option(Path("release-plan.yml"), "--out"),
                 template: Path = typer.Option(Path("ci/release.yml"), "--template"),
                 force: bool = typer.Option(False, "--force", help="manual release: skip the MR-label check"),
                 evidence: Optional[Path] = typer.Option(None, "--evidence")) -> None:
    """Write the release child pipeline: release / defer / block, or a no-op (SRS §15.3)."""
    gl = GitLab.from_env()
    sha = sha or config.require("CI_COMMIT_SHA")["CI_COMMIT_SHA"]
    if not force:
        merged = [m for m in gl.commit_merge_requests(sha)
                  if m.get("state") == "merged" and state.REMEDIATION_LABEL in (m.get("labels") or [])]
        if not merged:
            out.write_text(_plan_yaml("release-noop", [
                f"No merged {state.REMEDIATION_LABEL} merge request for {sha[:8]}: nothing to release.",
                "Main deploys only verified remediations. Manual release: run a pipeline with STALE_PIPELINE=release."]))
            typer.echo("plan: noop")
            return
        typer.echo("remediation MR(s): " + ", ".join(f"!{m['iid']}" for m in merged))
    ev = read_evidence("candidate", evidence)
    result, per = gate_decision(load_claims(), ev, open_claim_issues(gl))
    write_json("gate.json", {"result": result, "claims": per, "decided_at": now_iso()})
    summary = [f"{k}: {v['result']} ({v.get('state', '-')}; findings: "
               f"{', '.join(f['rule'] for f in v.get('findings', [])) or 'none'})" for k, v in per.items()]
    if result == "pass":
        out.write_text(template.read_text())
    elif result == "defer":
        out.write_text(_plan_yaml("release-deferred", [
            "Gate: DEFER. Another claim's fix is still in flight; its merge will ship both.", *summary]))
    else:
        out.write_text(_plan_yaml("release-blocked", ["Gate: BLOCK.", *summary], fail=True))
    typer.echo(f"plan: {result}\n  " + "\n  ".join(summary))


@app.command("security-gate")
@guarded
def security_gate(reports: list[Path] = typer.Argument(..., help="gl-*-report.json files"),
                  baseline: Path = typer.Option(Path(".stale/security-baseline.json"), "--baseline")) -> None:
    """Fail on any Critical/High finding not listed in the security baseline."""
    allowed: set[str] = set()
    if baseline.exists():
        allowed = {a["id"] for a in json.loads(baseline.read_text()).get("allowed", [])}
    new, seen, missing = [], 0, []
    for path in reports:
        if not path.exists():
            missing.append(str(path))
            continue
        for v in json.loads(path.read_text()).get("vulnerabilities", []):
            if str(v.get("severity", "")).lower() not in ("critical", "high"):
                continue
            seen += 1
            ids = {i.get("value") for i in v.get("identifiers", []) if i.get("value")} | {v.get("id"), v.get("cve")}
            if not ids & allowed:
                loc = v.get("location") or {}
                where = (loc.get("dependency") or {}).get("package", {}).get("name") or loc.get("file") or "?"
                title = v.get("name") or v.get("message") or (v.get("description") or "")[:70] or "?"
                new.append(f"{v.get('severity')}: {title} "
                           f"[{', '.join(sorted(i for i in ids if i))[:120]}] in {where} ({path.name})")
    for m in missing:
        typer.echo(f"note: report {m} not found (scanner produced no report)")
    typer.echo(f"{seen} Critical/High finding(s); {len(new)} not in {baseline}")
    for n in new:
        typer.echo(f"  NEW {n}")
    if new:
        raise typer.Exit(EXIT_POLICY)


@app.command()
@guarded
def report(evidence: Optional[Path] = typer.Option(None, "--evidence")) -> None:
    """Markdown summary of claim states, fingerprints and SCI totals."""
    claims = load_claims()
    lock = load_lock()
    ev = None
    try:
        ev = read_evidence("serving", evidence)
    except RuntimeError:
        pass
    gl = gitlab_or_none()
    issues = open_claim_issues(gl) if gl else {}
    lines = ["# Stale report", "", "| Claim | State | Minted fingerprint | Minted at | Revision |",
             "|---|---|---|---|---|"]
    for c in claims:
        ce = ev.claims.get(c.id) if ev else None
        findings = rules.evaluate(c, ce.evidence) if ce and ce.evidence else []
        issue = (issues.get(c.id) or [None])[0]
        st = state.derive(issue.get("labels") if issue else None, bool(findings))
        le = lock.claims.get(c.id)
        ref = f" (#{issue['iid']})" if issue else ""
        lines.append(f"| {c.id} | {st.value}{ref} | `{(le.fingerprint or 'never')[:23]}` | "
                     f"{le.minted_at or '-'} | {le.revision or '-'} |")
    lines += ["", f"SCI: {lock.sci.reproofs_skipped} re-proof(s) skipped, "
                  f"{lock.sci.runner_seconds_avoided:.0f} runner-seconds avoided."]
    text = "\n".join(lines) + "\n"
    CTX.out.mkdir(parents=True, exist_ok=True)
    (CTX.out / "report.md").write_text(text)
    typer.echo(text)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())

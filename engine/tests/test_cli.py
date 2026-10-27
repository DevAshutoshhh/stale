"""CLI behaviour with offline feeds and a mocked GitLab API."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stale_engine.cli import app

from .conftest import REPO, ec_x, random_ec_x

API = "https://gl.test/api/v4"
P = f"{API}/projects/42"
ISSUES = re.compile(rf"{re.escape(P)}/issues\?.*state=opened.*")
G3 = "3eab3fba" + "1" * 56
TR = "c" * 64
runner = CliRunner()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    shutil.copytree(REPO / ".stale", tmp_path / ".stale")
    (tmp_path / ".stale" / "out").mkdir(exist_ok=True)
    feeds = tmp_path / "feeds"
    feeds.mkdir()
    (feeds / "node-releases.json").write_text(json.dumps([
        {"version": "v24.22.1", "date": "2026-10-21", "lts": "Krypton", "security": True},
        {"version": "v24.22.0", "date": "2026-10-01", "lts": "Krypton", "security": False},
        {"version": "v22.23.4", "date": "2026-10-21", "lts": "Jod", "security": True}]))
    (feeds / "node-schedule.json").write_text(json.dumps({"v24": {"start": "2025-05-06", "end": "2028-04-30"}}))
    (feeds / "root-distrust.json").write_text(json.dumps({
        "schema": "stale.rootfeed/v1", "updated_at": "2026-10-24T00:00:00Z",
        "distrusted": [{"spki_sha256": G3, "subject": "CN=Demo Root G3", "reason": "demo", "source": "demo"}]}))
    monkeypatch.setenv("STALE_FEEDS_BASE_URL", str(feeds))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def gl_env(monkeypatch):
    monkeypatch.setenv("CI_API_V4_URL", API)
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    monkeypatch.setenv("STALE_BOT_TOKEN", "test-token")
    monkeypatch.setenv("STALE_REMEDIATOR_USER_ID", "900")
    monkeypatch.setenv("STALE_REMEDIATOR_USERNAME", "duo-stale-remediator")
    monkeypatch.setenv("STALE_HUMAN_ESCALATION_USER_ID", "11")


def report(node="v24.22.1", with_g3=False, revision="ledger-api-00002-abc"):
    spki = [TR] + ([G3] if with_g3 else [])
    keys = {TR: {"type": "ec", "curve": "prime256v1", "x": random_ec_x()}}
    if with_g3:
        keys[G3] = {"type": "ec", "curve": "prime256v1", "x": ec_x(4)}
    return {"node_version": node, "revision": revision,
            "trust_policy": {"loaded": True, "excluded_spki_count": 0, "removed": 0},
            "effective_ca_spki": spki, "effective_ca_subjects": {TR: "CN=Demo Trusted Root", G3: "CN=Demo Root G3"},
            "effective_ca_keys": keys}


def invoke(*args):
    return runner.invoke(app, list(args), catch_exceptions=False)


def collect_candidate(repo, **kw):
    f = repo / "candidate-runtime.json"
    f.write_text(json.dumps(report(**kw)))
    return invoke("collect", "--target", "candidate", "--claim", "anchor-trusted", "--runtime-file", str(f))


def issue(iid, claim, st, extra=()):
    return {"iid": iid, "labels": [f"stale-claim::{claim}", f"stale-state::{st}", *extra]}


# ------------------------------------------------------------------------------------- validate
def test_validate_repo():
    r = runner.invoke(app, ["--claims-dir", str(REPO / ".stale" / "claims"), "validate"])
    assert r.exit_code == 0, r.output
    assert "OK: 2 claim(s) valid: anchor-trusted, migration-safe" in r.output


def test_validate_missing_playbook(repo):
    (repo / ".stale/playbooks/anchor-trusted.md").unlink()
    r = invoke("validate")
    assert r.exit_code == 1 and "playbook" in r.output


def test_validate_bad_claim(repo):
    p = repo / ".stale/claims/anchor-trusted.yaml"
    p.write_text(p.read_text().replace("enabled: true", "enabled: true\n  extra: 1"))
    assert invoke("validate").exit_code == 1


def test_validate_protected_allow_path(repo):
    p = repo / ".stale/claims/anchor-trusted.yaml"
    p.write_text(p.read_text().replace("- app/ledger-api/Dockerfile", "- .stale/**"))
    r = invoke("validate")
    assert r.exit_code == 1 and "protected" in r.output


# -------------------------------------------------------------------------------- collect/check
def test_collect_candidate_scenario_a(repo):
    r = collect_candidate(repo, node="v24.22.0", with_g3=True)
    assert r.exit_code == 0, r.output
    ev = json.loads((repo / ".stale/out/evidence-candidate.json").read_text())
    ce = ev["claims"]["anchor-trusted"]
    assert [f["rule"] for f in ce["findings"]] == ["R1", "R3", "R4"]
    assert ce["evidence"]["node_feed"]["line_security_floor"] == "v24.22.1"
    assert ce["fingerprint"].startswith("sha256:") and ce["errors"] == []


def test_collect_records_collector_errors(repo, monkeypatch):
    monkeypatch.setenv("STALE_FEEDS_BASE_URL", str(repo / "missing"))
    r = collect_candidate(repo)
    assert r.exit_code == 0  # runtime_report succeeded, so not every collector failed
    ce = json.loads((repo / ".stale/out/evidence-candidate.json").read_text())["claims"]["anchor-trusted"]
    assert len(ce["errors"]) == 2


def test_collect_all_collectors_failed_is_exit_2(repo):
    r = invoke("collect", "--target", "candidate", "--claim", "migration-safe")
    assert r.exit_code == 2 and "every collector failed" in r.output


def test_check_dry_run_writes_report(repo):
    collect_candidate(repo, node="v24.22.0", with_g3=True)
    r = invoke("check", "--target", "candidate", "--dry-run", "--open-issues")
    assert r.exit_code == 0, r.output
    text = (repo / ".stale/out/check-report.md").read_text()
    assert "would open: [stale] anchor-trusted: R1" in text


def test_check_opens_issue(repo, gl_env, httpx_mock):
    collect_candidate(repo, node="v24.22.0", with_g3=True)
    httpx_mock.add_response(url=ISSUES, json=[])
    httpx_mock.add_response(url=f"{P}/issues", method="POST", json={"iid": 12})
    r = invoke("check", "--target", "candidate", "--open-issues")
    assert r.exit_code == 0, r.output
    body = json.loads(httpx_mock.get_requests(method="POST")[0].content)
    assert body["labels"] == "stale-claim::anchor-trusted,stale-state::stale"
    assert "<!-- stale:machine:begin -->" in body["description"]
    assert "opened #12" in r.output


def test_check_does_not_duplicate_open_issue(repo, gl_env, httpx_mock):
    collect_candidate(repo, node="v24.22.0")
    httpx_mock.add_response(url=ISSUES, json=[issue(3, "anchor-trusted", "remediating")])
    r = invoke("check", "--target", "candidate", "--open-issues")
    assert "issue #3 open" in r.output and "REMEDIATING" in r.output


def test_check_without_evidence_is_exit_2(repo):
    assert invoke("check").exit_code == 2


# ---------------------------------------------------------------------------------------- gate
@pytest.mark.parametrize("issues,node,code,result", [
    ([], "v24.22.1", 0, "pass"),
    ([issue(3, "anchor-trusted", "remediating")], "v24.22.0", 3, "defer"),
    ([issue(3, "anchor-trusted", "reproving")], "v24.22.0", 3, "defer"),
    ([], "v24.22.0", 1, "block"),
    ([issue(3, "anchor-trusted", "quarantined")], "v24.22.1", 1, "block"),
])
def test_gate(repo, gl_env, httpx_mock, issues, node, code, result):
    collect_candidate(repo, node=node)
    p = repo / ".stale/claims/migration-safe.yaml"
    p.write_text(p.read_text().replace("enabled: true", "enabled: false"))
    httpx_mock.add_response(url=ISSUES, json=issues)
    r = invoke("gate", "--target", "candidate")
    assert r.exit_code == code, r.output
    assert json.loads((repo / ".stale/out/gate.json").read_text())["result"] == result


# ------------------------------------------------------------------------------------ verify-mr
def mr_mocks(httpx_mock, paths, description="Related to #7"):
    httpx_mock.add_response(url=ISSUES, json=[issue(7, "anchor-trusted", "reproving")])
    httpx_mock.add_response(url=f"{P}/merge_requests/5", json={
        "iid": 5, "sha": "abc", "author": {"username": "duo-stale-remediator"},
        "labels": ["stale-remediation", "stale-claim::anchor-trusted"],
        "title": "fix(stale): remediate anchor-trusted (attempt 1)", "description": description})
    httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/merge_requests/5/diffs\?.*"),
                            json=[{"old_path": p, "new_path": p} for p in paths])
    httpx_mock.add_response(url=f"{P}/merge_requests/5/notes", method="POST", json={"id": 1})
    httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/merge_requests/5/commits\?.*"), json=[
        {"short_id": "abc", "author_email": "service_account_group_1_x@noreply.gitlab.com"}], is_optional=True)
    httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/issues/7/notes\?.*"), json=[], is_optional=True)


def test_verify_mr_ok_sets_auto_merge(repo, gl_env, httpx_mock):
    mr_mocks(httpx_mock, ["app/ledger-api/Dockerfile", "app/ledger-api/deploy/trust/excluded-spki.txt"])
    httpx_mock.add_response(url=f"{P}/merge_requests/5/merge", method="PUT", json={},
                            match_json={"auto_merge": True, "should_remove_source_branch": True, "sha": "abc"})
    r = invoke("verify-mr", "--mr-iid", "5")
    assert r.exit_code == 0, r.output
    assert "auto-merge enabled" in r.output


def test_verify_mr_rejects_and_quarantines(repo, gl_env, httpx_mock):
    mr_mocks(httpx_mock, ["app/ledger-api/Dockerfile", ".stale/claims.lock.json"])
    httpx_mock.add_response(url=f"{P}/issues/7", method="PUT", json={})
    httpx_mock.add_response(url=f"{P}/issues/7/notes", method="POST", json={})
    r = invoke("verify-mr", "--mr-iid", "5")
    assert r.exit_code == 1
    put = json.loads(httpx_mock.get_requests(method="PUT")[0].content)
    assert put["add_labels"] == "stale-state::quarantined" and put["assignee_ids"] == [11]


def test_verify_mr_rejects_closing_keyword(repo, gl_env, httpx_mock):
    mr_mocks(httpx_mock, ["app/ledger-api/Dockerfile"], description="Related to #7\nCloses #7")
    httpx_mock.add_response(url=f"{P}/issues/7", method="PUT", json={})
    httpx_mock.add_response(url=f"{P}/issues/7/notes", method="POST", json={})
    r = invoke("verify-mr", "--mr-iid", "5")
    assert r.exit_code == 1 and "closing keyword" in r.output


# ------------------------------------------------------------------------- dispatch / rollback
def test_dispatch_assigns_remediator(repo, gl_env, httpx_mock):
    httpx_mock.add_response(url=ISSUES, json=[issue(7, "anchor-trusted", "stale"),
                                              issue(8, "migration-safe", "remediating")])
    httpx_mock.add_response(url=f"{P}/issues/7", method="PUT", json={})
    httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/issues/8/notes\?.*"), json=[])  # session healthy
    r = invoke("dispatch")
    assert r.exit_code == 0, r.output
    put = json.loads(httpx_mock.get_requests(method="PUT")[0].content)
    assert put["assignee_ids"] == [900]
    assert put["add_labels"] == "stale-state::remediating,stale-attempt::1"


def test_dispatch_retry_waits_then_redispatches(repo, gl_env, httpx_mock):
    labels = ("stale-attempt::1", "stale-attempt::2")
    httpx_mock.add_response(url=ISSUES, json=[issue(7, "anchor-trusted", "retry-pending", labels)])
    httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/issues/7/notes\?.*"), json=[
        {"body": "<!-- stale:rollback -->\nfailed", "created_at": "2026-10-25T10:00:00Z"},
        {"body": "[stale:analyst]\ncause", "created_at": "2026-10-25T10:01:00Z"}])
    httpx_mock.add_response(url=f"{P}/issues/7/related_merge_requests", json=[])
    httpx_mock.add_response(url=f"{P}/issues/7", method="PUT", json={}, is_reusable=True)
    r = invoke("dispatch")
    assert "re-dispatched attempt 2" in r.output
    first, second = [json.loads(x.content) for x in httpx_mock.get_requests(method="PUT")]
    assert first == {"assignee_ids": []} and second["assignee_ids"] == [900]


def _session_notes(failed_at):
    return [{"system": True, "body": "assigned to @duo-stale-remediator", "created_at": "2026-10-26T18:05:37Z"},
            {"system": True, "body": "started session [1](https://x/1) triggered by @bot", "created_at": "2026-10-26T18:05:38Z"}] + \
           [{"system": True, "body": "session [1](https://x/1) failed (dropped)", "created_at": t} for t in failed_at]


def test_dispatch_recovers_failed_session(repo, gl_env, httpx_mock):
    httpx_mock.add_response(url=ISSUES, json=[issue(8, "anchor-trusted", "remediating", ("stale-attempt::1",))])
    httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/issues/8/notes\?.*"),
                            json=_session_notes(["2026-10-26T18:06:33Z"]))
    httpx_mock.add_response(url=f"{P}/issues/8/related_merge_requests", json=[])
    httpx_mock.add_response(url=f"{P}/issues/8", method="PUT", json={}, is_reusable=True)
    httpx_mock.add_response(url=f"{P}/issues/8/notes", method="POST", json={})
    r = invoke("dispatch")
    assert "session failed, re-dispatched (1/3)" in r.output
    first, second = [json.loads(x.content) for x in httpx_mock.get_requests(method="PUT")]
    assert first == {"assignee_ids": []} and second == {"assignee_ids": [900]}


def test_dispatch_leaves_running_or_productive_sessions_alone(repo, gl_env, httpx_mock):
    # Session still running (no failure note) -> nothing to do.
    httpx_mock.add_response(url=ISSUES, json=[issue(8, "anchor-trusted", "remediating")])
    httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/issues/8/notes\?.*"), json=_session_notes([]))
    assert "nothing to dispatch" in invoke("dispatch").output


def test_dispatch_quarantines_after_three_failed_sessions(repo, gl_env, httpx_mock):
    httpx_mock.add_response(url=ISSUES, json=[issue(8, "anchor-trusted", "remediating")])
    httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/issues/8/notes\?.*"), json=_session_notes(
        ["2026-10-26T18:06:33Z", "2026-10-26T18:20:00Z", "2026-10-26T18:40:00Z"]))
    httpx_mock.add_response(url=f"{P}/issues/8/related_merge_requests", json=[])
    httpx_mock.add_response(url=f"{P}/issues/8", method="PUT", json={})
    httpx_mock.add_response(url=f"{P}/issues/8/notes", method="POST", json={})
    r = invoke("dispatch")
    assert "quarantined after 3 failed sessions" in r.output


def test_dispatch_retry_with_merged_fix_re_releases(repo, gl_env, httpx_mock):
    labels = ("stale-attempt::1", "stale-attempt::2")
    httpx_mock.add_response(url=ISSUES, json=[issue(7, "anchor-trusted", "retry-pending", labels)])
    httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/issues/7/notes\?.*"), json=[
        {"body": "<!-- stale:rollback -->\nfailed", "created_at": "2026-10-25T10:00:00Z"}])
    httpx_mock.add_response(url=f"{P}/issues/7/related_merge_requests",
                            json=[{"iid": 5, "state": "merged", "labels": ["stale-remediation"]}])
    httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/pipeline_schedules\?.*"),
                            json=[{"id": 3, "description": "control-loop"}, {"id": 9, "description": "release"}])
    httpx_mock.add_response(url=f"{P}/pipeline_schedules/9/play", method="POST", status_code=201, json={})
    httpx_mock.add_response(url=f"{P}/issues/7", method="PUT", json={})
    httpx_mock.add_response(url=f"{P}/issues/7/notes", method="POST", json={})
    r = invoke("dispatch")
    assert r.exit_code == 0, r.output
    assert "retry 2 = re-release" in r.output
    put = json.loads(httpx_mock.get_requests(method="PUT")[0].content)
    assert put["add_labels"] == "stale-state::reproving" and "assignee_ids" not in put


def test_dispatch_two_retries_play_one_release(repo, gl_env, httpx_mock):
    labels = ("stale-attempt::1", "stale-attempt::2")
    httpx_mock.add_response(url=ISSUES, json=[issue(7, "anchor-trusted", "retry-pending", labels),
                                              issue(8, "migration-safe", "retry-pending", labels)])
    for iid, mr in ((7, 5), (8, 6)):
        httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/issues/{iid}/notes\?.*"), json=[
            {"body": "<!-- stale:rollback -->\nfailed", "created_at": "2026-10-25T10:00:00Z"}])
        httpx_mock.add_response(url=f"{P}/issues/{iid}/related_merge_requests",
                                json=[{"iid": mr, "state": "merged", "labels": ["stale-remediation"]}])
        httpx_mock.add_response(url=f"{P}/issues/{iid}", method="PUT", json={})
        httpx_mock.add_response(url=f"{P}/issues/{iid}/notes", method="POST", json={})
    httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/pipeline_schedules\?.*"),
                            json=[{"id": 9, "description": "release"}])
    httpx_mock.add_response(url=f"{P}/pipeline_schedules/9/play", method="POST", status_code=201, json={})
    r = invoke("dispatch")
    assert r.exit_code == 0, r.output
    assert r.output.count("= re-release") == 2
    assert len([q for q in httpx_mock.get_requests(method="POST") if q.url.path.endswith("/play")]) == 1


@pytest.mark.parametrize("attempts,expect", [(("stale-attempt::1",), "retry-pending"),
                                             (("stale-attempt::1", "stale-attempt::2"), "quarantined")])
def test_rollback_transition(repo, gl_env, httpx_mock, attempts, expect):
    (repo / "reason.json").write_text('{"failed_jobs":["canary-ring"]}')
    httpx_mock.add_response(url=ISSUES, json=[issue(7, "anchor-trusted", "reproving", attempts)])
    httpx_mock.add_response(url=f"{P}/issues/7", method="PUT", json={})
    httpx_mock.add_response(url=f"{P}/issues/7/notes", method="POST", json={})
    r = invoke("rollback-transition", "--reason", "reason.json")
    assert r.exit_code == 0 and expect in r.output
    put = json.loads(httpx_mock.get_requests(method="PUT")[0].content)
    assert put["add_labels"].startswith(f"stale-state::{expect}")


# -------------------------------------------------------------------------------------- reprove
def test_reprove_pre_deploy_pass_and_fail(repo):
    collect_candidate(repo)
    r = invoke("reprove", "--claim", "anchor-trusted", "--stage", "pre-deploy")
    assert r.exit_code == 0 and "PASSED" in r.output
    collect_candidate(repo, node="v24.22.0")
    r = invoke("reprove", "--claim", "anchor-trusted", "--stage", "pre-deploy")
    assert r.exit_code == 1 and "FAIL R1" in r.output


def test_reprove_skips_on_minted_fingerprint(repo):
    collect_candidate(repo)
    fp = json.loads((repo / ".stale/out/evidence-candidate.json").read_text())["claims"]["anchor-trusted"]["fingerprint"]
    lock = json.loads((repo / ".stale/claims.lock.json").read_text())
    lock["claims"]["anchor-trusted"]["fingerprint"] = fp
    (repo / ".stale/claims.lock.json").write_text(json.dumps(lock))
    r = invoke("reprove", "--claim", "anchor-trusted", "--stage", "pre-deploy")
    assert r.exit_code == 0 and "[SKIP]" in r.output
    assert json.loads((repo / ".stale/out/sci-skip.json").read_text())["job"] == "reprove:anchor-trusted"
    r = invoke("reprove", "--claim", "anchor-trusted", "--stage", "pre-deploy", "--force")
    assert "PASSED" in r.output


def test_reprove_canary(repo, httpx_mock):
    url = "https://c-1---ledger-api.run.app"
    httpx_mock.add_response(url=f"{url}/internal/runtime", json=report(revision="rev-2"))
    httpx_mock.add_response(url=f"{url}/api/v1/webhooks/deliver", method="POST", match_json={"target": "trusted"},
                            json={"delivered": True})
    httpx_mock.add_response(url=f"{url}/api/v1/webhooks/deliver", method="POST", match_json={"target": "distrusted"},
                            status_code=502, json={"delivered": False, "tls_error": "SELF_SIGNED_CERT_IN_CHAIN"})
    r = invoke("reprove", "--stage", "canary", "--canary-url", url, "--claim", "anchor-trusted",
               "--expect-revision", "rev-2", "--migrations-dir", str(repo / "none"))
    assert r.exit_code == 0, r.output
    res = json.loads((repo / ".stale/out/canary-report.json").read_text())["results"]
    assert [x["probe"] for x in res] == ["runtime_report_matches", "partner_trusted_ok",
                                         "partner_distrusted_rejected"]


def test_reprove_canary_fails_when_distrusted_accepted(repo, httpx_mock):
    url = "https://c-1---ledger-api.run.app"
    httpx_mock.add_response(url=f"{url}/internal/runtime", json=report(with_g3=True))
    httpx_mock.add_response(url=f"{url}/api/v1/webhooks/deliver", method="POST", json={"delivered": True},
                            is_reusable=True)
    r = invoke("reprove", "--stage", "canary", "--canary-url", url, "--claim", "anchor-trusted",
               "--migrations-dir", str(repo / "none"))
    assert r.exit_code == 1
    assert "FAIL runtime_report_matches" in r.output and "FAIL partner_distrusted_rejected" in r.output


# -------------------------------------------------------------------------------- report / mint
def test_report_offline(repo):
    r = invoke("report")
    assert r.exit_code == 0 and "| anchor-trusted | VALID |" in r.output


def test_mint_closes_issue_and_commits_lockfile(repo, gl_env, httpx_mock, monkeypatch):
    monkeypatch.setenv("CI_PIPELINE_ID", "555")
    url = "https://stable---ledger-api.run.app"
    p = repo / ".stale/claims/migration-safe.yaml"
    p.write_text(p.read_text().replace("enabled: true", "enabled: false"))
    lock = (repo / ".stale/claims.lock.json").read_text()
    httpx_mock.add_response(url=ISSUES, json=[issue(7, "anchor-trusted", "reproving")])
    httpx_mock.add_response(url=f"{url}/internal/runtime", json=report())
    httpx_mock.add_response(url=f"{P}/issues/7/related_merge_requests", json=[{"iid": 5}])
    httpx_mock.add_response(url=re.compile(rf"{re.escape(P)}/repository/files/.*"), json={
        "content": __import__("base64").b64encode(lock.encode()).decode(), "last_commit_id": "c0"})
    httpx_mock.add_response(url=f"{P}/repository/commits", method="POST", json={"id": "deadbeef"})
    httpx_mock.add_response(url=f"{P}/issues/7/notes", method="POST", json={})
    httpx_mock.add_response(url=f"{P}/issues/7", method="PUT", json={})
    r = invoke("mint", "--revision", "ledger-api-00003-new", "--runtime-url", url)
    assert r.exit_code == 0, r.output
    commit = json.loads(httpx_mock.get_requests(method="POST", url=f"{P}/repository/commits")[0].content)
    assert commit["commit_message"].endswith("[skip ci]")
    minted = json.loads(commit["actions"][0]["content"])["claims"]["anchor-trusted"]
    assert minted["revision"] == "ledger-api-00003-new" and minted["mr_iids"] == [5]
    assert minted["pipeline_id"] == 555 and minted["issue_iid"] == 7
    close = json.loads(httpx_mock.get_requests(method="PUT")[0].content)
    assert close["state_event"] == "close"


def _stalled_mr(finished_at, mwps=True, status="success", merge_status="mergeable"):
    return {"iid": 13, "sha": "abc123", "merge_when_pipeline_succeeds": mwps, "detailed_merge_status": merge_status,
            "head_pipeline": {"id": 77, "status": status, "finished_at": finished_at}}


@pytest.mark.parametrize("mr,merged", [
    (_stalled_mr("2026-10-27T03:12:00Z"), True),
    (_stalled_mr("2026-10-27T03:12:00Z", mwps=False), False),          # verify-mr never enabled auto-merge
    (_stalled_mr("2026-10-27T03:12:00Z", status="failed"), False),
    (_stalled_mr("2026-10-27T03:12:00Z", merge_status="conflict"), False),
    (_stalled_mr("2999-01-01T00:00:00Z"), False),                       # still inside the grace period
])
def test_dispatch_merges_stalled_auto_merge(repo, gl_env, httpx_mock, mr, merged):
    httpx_mock.add_response(url=ISSUES, json=[issue(15, "anchor-trusted", "reproving")])
    httpx_mock.add_response(url=f"{P}/issues/15/related_merge_requests",
                            json=[{"iid": 13, "state": "opened", "labels": ["stale-remediation"]}])
    httpx_mock.add_response(url=f"{P}/merge_requests/13", json=mr)
    if merged:
        httpx_mock.add_response(url=f"{P}/merge_requests/13/merge", method="PUT", json={"state": "merged"})
        httpx_mock.add_response(url=f"{P}/merge_requests/13/notes", method="POST", json={})
    r = invoke("dispatch")
    assert r.exit_code == 0, r.output
    assert ("auto-merge of !13 stalled, merged" in r.output) is merged
    if merged:
        put = json.loads(httpx_mock.get_requests(method="PUT")[0].content)
        assert put["sha"] == "abc123"

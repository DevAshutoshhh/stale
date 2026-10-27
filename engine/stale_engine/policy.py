"""`verify-mr` policy checks (SRS §15.4, §6.6). Pure functions; the CLI does the API calls."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable

from . import state

FORBIDDEN_PREFIXES = (".stale/", "ci/", "duo/")
FORBIDDEN_FILES = (".gitlab-ci.yml",)

# GitLab closing keywords followed by an issue reference (#12, group/proj#12 or an issue URL).
CLOSING_RE = re.compile(
    r"\b(?:clos(?:e|es|ed|ing)|fix(?:|es|ed|ing)|resolv(?:e|es|ed|ing)|implement(?:|s|ed|ing))\b"
    r":?\s+(?:issues?\s+)?(?:[\w./-]*#\d+|https?://\S+/(?:-/)?(?:issues|work_items)/\d+)",
    re.I,
)
RELATED_RE = re.compile(r"Related to #(\d+)")


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        out.append({"*": "[^/]*", "?": "[^/]"}.get(c, re.escape(c)))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(path: str, pattern: str) -> bool:
    return bool(_glob_regex(pattern).match(path))


def valid_glob(pattern: str) -> bool:
    return bool(pattern) and not pattern.startswith("/") and ".." not in pattern.split("/") \
        and "\\" not in pattern


def forbidden(path: str) -> bool:
    return path in FORBIDDEN_FILES or path.startswith(FORBIDDEN_PREFIXES)


def path_violations(paths: Iterable[str], allow: list[str]) -> list[str]:
    out = []
    for p in sorted(set(paths)):
        if forbidden(p):
            out.append(f"{p}: protected path (.stale/, ci/, .gitlab-ci.yml, duo/ are never editable)")
        elif not any(glob_match(p, a) for a in allow):
            out.append(f"{p}: outside allowPaths {allow}")
    return out


def has_closing_keyword(text: str) -> bool:
    return bool(CLOSING_RE.search(text or ""))


def changed_paths(diffs: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for d in diffs:
        paths.update(p for p in (d.get("old_path"), d.get("new_path")) if p)
    return paths


SERVICE_ACCOUNT_EMAIL = re.compile(r"^service_account_[\w.-]+@noreply\.gitlab\.com$")


def provenance(mr: dict[str, Any], commits: list[dict[str, Any]] | None,
               remediator_notes: list[dict[str, Any]] | None, remediator_username: str) -> str | None:
    """Return None when the MR provably comes from the remediator flow, else the reason it does not.

    Duo flows use a composite identity: the MR author is the user who triggered the flow (the bot
    token owner), while commits carry the flow's service account. So either the author is the
    remediator, or every commit is by a service account AND the remediator account itself posted a
    note on the claim issue linking this MR.
    """
    author = (mr.get("author") or {}).get("username")
    if author == remediator_username:
        return None
    if not commits:
        return f"author {author!r} is not the remediator and the MR has no commits"
    bad = [c.get("short_id") for c in commits if not SERVICE_ACCOUNT_EMAIL.match(c.get("author_email") or "")]
    if bad:
        return f"commits {bad} are not authored by a flow service account"
    link = re.compile(rf"/merge_requests/{mr.get('iid')}(?!\d)")
    if not any((n.get("author") or {}).get("username") == remediator_username and link.search(n.get("body") or "")
               for n in remediator_notes or []):
        return f"no note by {remediator_username!r} on the claim issue links !{mr.get('iid')}"
    return None


def verify(mr: dict[str, Any], diffs: list[dict[str, Any]], *, remediator_username: str,
           allow_paths_by_claim: dict[str, list[str]], open_issue_iids_by_claim: dict[str, list[int]],
           commits: list[dict[str, Any]] | None = None, claim_issue_notes: list[dict[str, Any]] | None = None
           ) -> tuple[str | None, list[str]]:
    """Return (claim_id, violations). An empty violation list means the MR may auto-merge."""
    v: list[str] = []
    why = provenance(mr, commits, claim_issue_notes, remediator_username)
    if why:
        v.append(why)
    labels = mr.get("labels") or []
    if state.REMEDIATION_LABEL not in labels:
        v.append(f"missing label {state.REMEDIATION_LABEL}")
    claims = state.claim_of(labels)
    claim = claims[0] if len(claims) == 1 else None
    if len(claims) != 1:
        v.append(f"expected exactly one stale-claim:: label, found {claims}")
    elif claim not in allow_paths_by_claim:
        v.append(f"unknown claim {claim!r}")
    elif not open_issue_iids_by_claim.get(claim):
        v.append(f"no open issue for claim {claim!r}")
    desc = mr.get("description") or ""
    if has_closing_keyword(desc) or has_closing_keyword(mr.get("title") or ""):
        v.append("description or title contains a closing keyword next to an issue reference")
    related = RELATED_RE.findall(desc)
    if claim and open_issue_iids_by_claim.get(claim) and not (
            set(map(int, related)) & set(open_issue_iids_by_claim[claim])):
        v.append("description must contain 'Related to #<claim issue iid>'")
    allow = allow_paths_by_claim.get(claim or "", [])
    paths = changed_paths(diffs)
    if not paths:
        v.append("merge request changes no files")
    v.extend(path_violations(paths, allow))
    return claim, v

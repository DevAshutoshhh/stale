import pytest

from stale_engine import policy

ALLOW = {"anchor-trusted": ["app/ledger-api/Dockerfile", "app/ledger-api/deploy/trust/**"],
         "migration-safe": ["app/ledger-api/db/migrations/**"]}
OPEN = {"anchor-trusted": [7], "migration-safe": [8]}
BOT = "duo-stale-remediator"


def mr(**over):
    base = {"author": {"username": BOT}, "labels": ["stale-remediation", "stale-claim::anchor-trusted"],
            "title": "fix(stale): remediate anchor-trusted (attempt 1)",
            "description": "Related to #7\n\n- Dockerfile: bump Node\n- excluded-spki.txt: drop Demo Root G3"}
    base.update(over)
    return base


def diffs(*paths):
    return [{"old_path": p, "new_path": p} for p in paths]


def run(m, d):
    return policy.verify(m, d, remediator_username=BOT, allow_paths_by_claim=ALLOW,
                         open_issue_iids_by_claim=OPEN)


def test_allowed_paths_pass():
    claim, v = run(mr(), diffs("app/ledger-api/Dockerfile", "app/ledger-api/deploy/trust/excluded-spki.txt"))
    assert claim == "anchor-trusted" and v == []


def test_migration_claim_paths_pass():
    m = mr(labels=["stale-remediation", "stale-claim::migration-safe"], description="Related to #8")
    assert run(m, diffs("app/ledger-api/db/migrations/20261021090000_add_ledger_entries_merchant_idx.sql"))[1] == []


@pytest.mark.parametrize("path", [".stale/claims.lock.json", ".stale/claims/anchor-trusted.yaml",
                                  "ci/release.yml", ".gitlab-ci.yml", "duo/flows/stale-remediator.yaml"])
def test_protected_paths_rejected(path):
    _, v = run(mr(), diffs("app/ledger-api/Dockerfile", path))
    assert len(v) == 1 and "protected path" in v[0]


def test_outside_allow_paths_rejected():
    _, v = run(mr(), diffs("app/ledger-api/src/index.js"))
    assert v and "outside allowPaths" in v[0]


def test_rename_checks_both_paths():
    _, v = run(mr(), [{"old_path": ".stale/x", "new_path": "app/ledger-api/Dockerfile"}])
    assert any("protected" in x for x in v)


@pytest.mark.parametrize("desc", ["Closes #7", "Related to #7. fixes #7", "resolves: #7",
                                  "Fixed group/proj#7", "closing https://gitlab.com/g/p/-/issues/7",
                                  "Related to #7\nImplements #7"])
def test_closing_keyword_rejected(desc):
    _, v = run(mr(description=desc), diffs("app/ledger-api/Dockerfile"))
    assert any("closing keyword" in x for x in v)


@pytest.mark.parametrize("text", ["Related to #7", "fix(stale): remediate anchor-trusted (attempt 1)",
                                  "this fixes the Dockerfile", "close the gap in #7's evidence"])
def test_not_closing(text):
    assert not policy.has_closing_keyword(text)


def test_wrong_author_and_labels():
    _, v = run(mr(author={"username": "mallory"}, labels=["stale-claim::anchor-trusted"]),
               diffs("app/ledger-api/Dockerfile"))
    assert any("not the remediator" in x for x in v)
    assert any("missing label stale-remediation" in x for x in v)


SA = "service_account_group_121124982_d582@noreply.gitlab.com"
LINK_NOTE = {"author": {"username": BOT}, "body": "Opened merge request: https://gitlab.com/g/p/-/merge_requests/4"}


def composite(**over):
    """Duo composite identity: MR author is the human/bot token owner, commits are the flow's."""
    return mr(author={"username": "ZAPcodes2"}, iid=4, **over)


def run_c(m, commits, notes):
    return policy.verify(m, diffs("app/ledger-api/Dockerfile"), remediator_username=BOT,
                         allow_paths_by_claim=ALLOW, open_issue_iids_by_claim=OPEN,
                         commits=commits, claim_issue_notes=notes)


def test_composite_identity_accepted_with_service_account_commits_and_link_note():
    assert run_c(composite(), [{"short_id": "a1", "author_email": SA}], [LINK_NOTE])[1] == []


def test_composite_identity_rejected_when_a_commit_is_human():
    _, v = run_c(composite(), [{"short_id": "a1", "author_email": SA},
                               {"short_id": "b2", "author_email": "someone@example.com"}], [LINK_NOTE])
    assert any("not authored by a flow service account" in x for x in v)


def test_composite_identity_rejected_without_remediator_note():
    other = {"author": {"username": "mallory"}, "body": LINK_NOTE["body"]}
    _, v = run_c(composite(), [{"short_id": "a1", "author_email": SA}], [other])
    assert any("links !4" in x for x in v)
    _, v = run_c(composite(), [{"short_id": "a1", "author_email": SA}],
                 [{"author": {"username": BOT}, "body": "see https://gitlab.com/g/p/-/merge_requests/40"}])
    assert any("links !4" in x for x in v)


def test_multiple_claim_labels_and_closed_issue():
    _, v = run(mr(labels=["stale-remediation", "stale-claim::anchor-trusted", "stale-claim::migration-safe"]),
               diffs("app/ledger-api/Dockerfile"))
    assert any("exactly one" in x for x in v)
    _, v = policy.verify(mr(), diffs("app/ledger-api/Dockerfile"), remediator_username=BOT,
                         allow_paths_by_claim=ALLOW, open_issue_iids_by_claim={})
    assert any("no open issue" in x for x in v)


def test_missing_related_reference():
    _, v = run(mr(description="- bump"), diffs("app/ledger-api/Dockerfile"))
    assert any("Related to" in x for x in v)


def test_empty_diff_rejected():
    _, v = run(mr(), [])
    assert any("no files" in x for x in v)


@pytest.mark.parametrize("path,pattern,ok", [
    ("app/ledger-api/deploy/trust/excluded-spki.txt", "app/ledger-api/deploy/trust/**", True),
    ("app/ledger-api/deploy/trust/sub/x", "app/ledger-api/deploy/trust/**", True),
    ("app/ledger-api/deploy/other.txt", "app/ledger-api/deploy/trust/**", False),
    ("app/ledger-api/Dockerfile", "app/ledger-api/Dockerfile", True),
    ("app/ledger-api/Dockerfile.bak", "app/ledger-api/Dockerfile", False),
    ("a/b/c.sql", "a/*/c.sql", True), ("a/b/x/c.sql", "a/*/c.sql", False),
    ("a/c.sql", "a/**/c.sql", True), ("a/x/y/c.sql", "a/**/c.sql", True), ("a/b.sql", "a/?.sql", True),
])
def test_glob(path, pattern, ok):
    assert policy.glob_match(path, pattern) is ok


def test_valid_glob():
    assert policy.valid_glob("app/**")
    assert not policy.valid_glob("/etc/passwd")
    assert not policy.valid_glob("app/../.stale/x")
    assert not policy.valid_glob("")

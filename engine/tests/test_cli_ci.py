"""CI-facing commands: plan-release, security-gate, mark-reproving."""

import json
import re

import pytest
import yaml

from .test_cli import ISSUES, P, collect_candidate, gl_env, invoke, issue, repo  # noqa: F401

SHA = "a" * 40


@pytest.fixture
def template(repo):
    t = repo / "release.yml"
    t.write_text("stages: [release]\nrecord-stable: {stage: release, script: [echo real]}\n")
    return t


def mrs(httpx_mock, merged_labels):
    httpx_mock.add_response(url=f"{P}/repository/commits/{SHA}/merge_requests",
                            json=[{"iid": 9, "state": "merged", "labels": merged_labels}])


def test_plan_noop_without_remediation(repo, gl_env, httpx_mock, template):
    mrs(httpx_mock, ["docs"])
    r = invoke("plan-release", "--sha", SHA, "--template", str(template))
    assert r.exit_code == 0 and "plan: noop" in r.output
    plan = yaml.safe_load((repo / "release-plan.yml").read_text())
    assert "release-noop" in plan and "exit 1" not in plan["release-noop"]["script"]


def disable_migration(repo):
    p = repo / ".stale/claims/migration-safe.yaml"
    p.write_text(p.read_text().replace("enabled: true", "enabled: false"))


@pytest.mark.parametrize("node,issues,result,job", [
    ("v24.22.1", [], "pass", "record-stable"),
    ("v24.22.0", [issue(3, "anchor-trusted", "reproving")], "defer", "release-deferred"),
    ("v24.22.0", [], "block", "release-blocked"),
])
def test_plan_with_remediation(repo, gl_env, httpx_mock, template, node, issues, result, job):
    disable_migration(repo)
    collect_candidate(repo, node=node)
    mrs(httpx_mock, ["stale-remediation", "stale-claim::anchor-trusted"])
    httpx_mock.add_response(url=ISSUES, json=issues)
    r = invoke("plan-release", "--sha", SHA, "--template", str(template))
    assert r.exit_code == 0, r.output
    assert f"plan: {result}" in r.output
    plan = yaml.safe_load((repo / "release-plan.yml").read_text())
    assert job in plan
    if result == "block":
        assert plan[job]["script"][-1] == "exit 1"


def test_plan_force_skips_mr_lookup(repo, gl_env, httpx_mock, template):
    disable_migration(repo)
    collect_candidate(repo)
    httpx_mock.add_response(url=ISSUES, json=[])
    r = invoke("plan-release", "--sha", SHA, "--template", str(template), "--force")
    assert r.exit_code == 0 and "plan: pass" in r.output


def test_security_gate(repo):
    rep = repo / "gl-container-scanning-report.json"
    rep.write_text(json.dumps({"vulnerabilities": [
        {"id": "x1", "severity": "High", "name": "old", "identifiers": [{"value": "CVE-2020-1"}],
         "location": {"dependency": {"package": {"name": "libfoo"}}}},
        {"id": "x2", "severity": "Low", "name": "low", "identifiers": [{"value": "CVE-2020-2"}]},
        {"id": "x3", "severity": "Critical", "name": "fresh", "identifiers": [{"value": "CVE-2026-9"}],
         "location": {"dependency": {"package": {"name": "libbar"}}}}]}))
    base = repo / "baseline.json"
    base.write_text(json.dumps({"allowed": [{"id": "CVE-2020-1"}]}))
    r = invoke("security-gate", str(rep), str(repo / "missing.json"), "--baseline", str(base))
    assert r.exit_code == 1
    assert "NEW Critical: fresh [CVE-2026-9, x3] in libbar" in r.output and "not found" in r.output
    base.write_text(json.dumps({"allowed": [{"id": "CVE-2020-1"}, {"id": "CVE-2026-9"}]}))
    assert invoke("security-gate", str(rep), "--baseline", str(base)).exit_code == 0


def test_mark_reproving(repo, gl_env, httpx_mock):
    httpx_mock.add_response(url=f"{P}/merge_requests/5", json={
        "iid": 5, "labels": ["stale-remediation", "stale-claim::anchor-trusted"]})
    httpx_mock.add_response(url=ISSUES, json=[issue(7, "anchor-trusted", "remediating")])
    httpx_mock.add_response(url=f"{P}/issues/7", method="PUT", json={})
    r = invoke("mark-reproving", "--mr-iid", "5")
    assert r.exit_code == 0 and "#7 -> reproving" in r.output
    put = json.loads(httpx_mock.get_requests(method="PUT")[0].content)
    assert put["add_labels"] == "stale-state::reproving"


def test_mark_reproving_ignores_other_mrs(repo, gl_env, httpx_mock):
    httpx_mock.add_response(url=f"{P}/merge_requests/6", json={"iid": 6, "labels": ["docs"]})
    r = invoke("mark-reproving", "--mr-iid", "6")
    assert r.exit_code == 0 and "nothing to do" in r.output

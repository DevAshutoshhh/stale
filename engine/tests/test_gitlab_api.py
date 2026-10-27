import json
import re

import httpx
import pytest

from stale_engine.gitlab_api import GitLab, GitLabError

API = "https://gl.test/api/v4"


@pytest.fixture
def gl():
    return GitLab(API, "tok", "group/proj", backoff=0)


def test_project_path_is_encoded_and_token_sent(gl, httpx_mock):
    httpx_mock.add_response(url=f"{API}/projects/group%2Fproj/merge_requests/3", json={"iid": 3},
                            match_headers={"Authorization": "Bearer tok"})
    assert gl.get_mr(3) == {"iid": 3}


def test_pagination_follows_x_next_page(gl, httpx_mock):
    base = f"{API}/projects/group%2Fproj/merge_requests/9/diffs"
    httpx_mock.add_response(url=f"{base}?per_page=100&page=1", json=[{"new_path": "a"}],
                            headers={"X-Next-Page": "2"})
    httpx_mock.add_response(url=f"{base}?per_page=100&page=2", json=[{"new_path": "b"}],
                            headers={"X-Next-Page": ""})
    assert [d["new_path"] for d in gl.mr_diffs(9)] == ["a", "b"]


def test_retries_on_5xx_then_succeeds(gl, httpx_mock):
    url = f"{API}/projects/group%2Fproj/issues/1/notes"
    httpx_mock.add_response(url=url, method="POST", status_code=502)
    httpx_mock.add_response(url=url, method="POST", status_code=429, headers={"Retry-After": "0"})
    httpx_mock.add_response(url=url, method="POST", json={"id": 1})
    assert gl.add_issue_note(1, "hi") == {"id": 1}


def test_retries_on_transport_error(gl, httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    httpx_mock.add_response(json={"id": 7})
    assert gl.current_user() == {"id": 7}


def test_gives_up_after_attempts(gl, httpx_mock):
    for _ in range(4):
        httpx_mock.add_response(status_code=503)
    with pytest.raises(GitLabError) as e:
        gl.current_user()
    assert e.value.status == 503


def test_4xx_is_not_retried(gl, httpx_mock):
    httpx_mock.add_response(status_code=404, text="not found")
    with pytest.raises(GitLabError):
        gl.get_mr(1)


def test_update_issue_joins_label_lists(gl, httpx_mock):
    httpx_mock.add_response(method="PUT", json={"iid": 2},
                            match_json={"add_labels": "a,b", "remove_labels": "c", "assignee_ids": [5]})
    gl.update_issue(2, add_labels=["a", "b"], remove_labels=["c"], assignee_ids=[5])


def test_auto_merge_falls_back_to_old_parameter(gl, httpx_mock):
    url = f"{API}/projects/group%2Fproj/merge_requests/4/merge"
    httpx_mock.add_response(url=url, method="PUT", status_code=400,
                            match_json={"auto_merge": True, "should_remove_source_branch": True, "sha": "abc"})
    httpx_mock.add_response(url=url, method="PUT", json={"state": "opened"},
                            match_json={"merge_when_pipeline_succeeds": True,
                                        "should_remove_source_branch": True, "sha": "abc"})
    assert gl.set_auto_merge(4, "abc") == {"state": "opened"}


def test_get_file_decodes_base64(gl, httpx_mock):
    httpx_mock.add_response(url=re.compile(r".*/repository/files/\.stale%2Fclaims\.lock\.json\?ref=main"),
                            json={"content": "eyJhIjoxfQ==", "last_commit_id": "c1"})
    assert gl.get_file(".stale/claims.lock.json", "main") == ('{"a":1}', "c1")


def test_list_jobs_limit(gl, httpx_mock):
    httpx_mock.add_response(json=[{"id": i} for i in range(5)], headers={"X-Next-Page": "2"})
    assert len(gl.list_jobs(limit=3)) == 3


def test_from_env(monkeypatch):
    monkeypatch.setenv("STALE_BOT_TOKEN", "t")
    monkeypatch.setenv("CI_PROJECT_ID", "42")
    g = GitLab.from_env()
    assert g.base == "https://gitlab.com/api/v4" and g.project == "42"


def test_create_issue_body(gl, httpx_mock):
    httpx_mock.add_response(method="POST", json={"iid": 1})
    gl.create_issue("t", "b", ["x", "y"])
    assert json.loads(httpx_mock.get_requests()[0].content) == {"title": "t", "description": "b", "labels": "x,y"}

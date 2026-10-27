"""Thin GitLab REST v4 client (httpx) with retries and pagination."""

from __future__ import annotations

import base64
import time
from typing import Any, Iterator
from urllib.parse import quote

import httpx

from . import config

RETRY_STATUS = {429, 500, 502, 503, 504}


class GitLabError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitLab API {status}: {message}")
        self.status = status


class GitLab:
    def __init__(self, base_url: str, token: str, project_id: str | int, *,
                 client: httpx.Client | None = None, attempts: int = 4, backoff: float = 0.5):
        self.base = base_url.rstrip("/")
        self.project = quote(str(project_id), safe="")
        self.attempts, self.backoff = attempts, backoff
        self.http = client or httpx.Client(timeout=30.0)
        # Bearer works for personal, project and OAuth tokens alike.
        self.http.headers["Authorization"] = f"Bearer {token}"

    @classmethod
    def from_env(cls) -> "GitLab":
        v = config.require("STALE_BOT_TOKEN", "CI_PROJECT_ID")
        return cls(config.get("CI_API_V4_URL") or "https://gitlab.com/api/v4", v["STALE_BOT_TOKEN"],
                   v["CI_PROJECT_ID"])

    # -- transport -----------------------------------------------------------------------------
    def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.base}{path}"
        for i in range(self.attempts):
            try:
                r = self.http.request(method, url, **kw)
            except httpx.TransportError as e:
                if i + 1 == self.attempts:
                    raise GitLabError(0, str(e)) from e
            else:
                if r.status_code not in RETRY_STATUS or i + 1 == self.attempts:
                    if r.status_code >= 400:
                        raise GitLabError(r.status_code, r.text[:500])
                    return r
                wait = float(r.headers.get("Retry-After") or self.backoff * 2**i)
                time.sleep(min(wait, 30))
                continue
            time.sleep(self.backoff * 2**i)
        raise AssertionError("unreachable")

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params or None).json()

    def paginate(self, path: str, **params: Any) -> Iterator[Any]:
        params = {"per_page": 100, **params}
        page: str | None = "1"
        while page:
            r = self.request("GET", path, params={**params, "page": page})
            yield from r.json()
            page = r.headers.get("X-Next-Page") or None

    def p(self, suffix: str) -> str:
        return f"/projects/{self.project}{suffix}"

    # -- issues --------------------------------------------------------------------------------
    def list_issues(self, *, labels: list[str] | None = None, state: str = "opened") -> list[dict]:
        params: dict[str, Any] = {"state": state}
        if labels:
            params["labels"] = ",".join(labels)
        return list(self.paginate(self.p("/issues"), **params))

    def create_issue(self, title: str, description: str, labels: list[str]) -> dict:
        return self.request("POST", self.p("/issues"), json={
            "title": title, "description": description, "labels": ",".join(labels)}).json()

    def update_issue(self, iid: int, **fields: Any) -> dict:
        for k in ("add_labels", "remove_labels", "labels"):
            if isinstance(fields.get(k), list):
                fields[k] = ",".join(fields[k])
        return self.request("PUT", self.p(f"/issues/{iid}"), json=fields).json()

    def issue_notes(self, iid: int) -> list[dict]:
        return list(self.paginate(self.p(f"/issues/{iid}/notes"), sort="asc"))

    def issue_related_mrs(self, iid: int) -> list[dict]:
        return self.get(self.p(f"/issues/{iid}/related_merge_requests"))

    def add_issue_note(self, iid: int, body: str) -> dict:
        return self.request("POST", self.p(f"/issues/{iid}/notes"), json={"body": body}).json()

    # -- merge requests ------------------------------------------------------------------------
    def get_mr(self, iid: int) -> dict:
        return self.get(self.p(f"/merge_requests/{iid}"))

    def mr_diffs(self, iid: int) -> list[dict]:
        return list(self.paginate(self.p(f"/merge_requests/{iid}/diffs")))

    def mr_commits(self, iid: int) -> list[dict]:
        return list(self.paginate(self.p(f"/merge_requests/{iid}/commits")))

    def update_mr(self, iid: int, **fields: Any) -> dict:
        for k in ("add_labels", "remove_labels"):
            if isinstance(fields.get(k), list):
                fields[k] = ",".join(fields[k])
        return self.request("PUT", self.p(f"/merge_requests/{iid}"), json=fields).json()

    def add_mr_note(self, iid: int, body: str) -> dict:
        return self.request("POST", self.p(f"/merge_requests/{iid}/notes"), json={"body": body}).json()

    def set_auto_merge(self, iid: int, sha: str | None = None) -> dict:
        """Merge when the pipeline succeeds. Tries `auto_merge`, falls back to the older name."""
        body: dict[str, Any] = {"auto_merge": True, "should_remove_source_branch": True}
        if sha:
            body["sha"] = sha
        try:
            return self.request("PUT", self.p(f"/merge_requests/{iid}/merge"), json=body).json()
        except GitLabError as e:
            if e.status not in (400, 405, 422):
                raise
            body.pop("auto_merge")
            body["merge_when_pipeline_succeeds"] = True
            return self.request("PUT", self.p(f"/merge_requests/{iid}/merge"), json=body).json()

    def merge_mr(self, iid: int, sha: str) -> dict:
        """Merge now; GitLab refuses if the MR head is no longer `sha`."""
        body = {"sha": sha, "should_remove_source_branch": True}
        return self.request("PUT", self.p(f"/merge_requests/{iid}/merge"), json=body).json()

    def commit_merge_requests(self, sha: str) -> list[dict]:
        return self.get(self.p(f"/repository/commits/{sha}/merge_requests"))

    # -- repository ----------------------------------------------------------------------------
    def get_file(self, path: str, ref: str) -> tuple[str, str]:
        """Return (content, blob last_commit_id)."""
        data = self.get(self.p(f"/repository/files/{quote(path, safe='')}"), ref=ref)
        return base64.b64decode(data["content"]).decode("utf-8"), data["last_commit_id"]

    def commit_files(self, branch: str, message: str, actions: list[dict]) -> dict:
        return self.request("POST", self.p("/repository/commits"), json={
            "branch": branch, "commit_message": message, "actions": actions}).json()

    # -- jobs ----------------------------------------------------------------------------------
    def list_jobs(self, scope: str = "success", limit: int = 200) -> list[dict]:
        out: list[dict] = []
        for job in self.paginate(self.p("/jobs"), scope=scope):
            out.append(job)
            if len(out) >= limit:
                break
        return out

    # -- schedules -----------------------------------------------------------------------------
    def play_schedule(self, description: str) -> int:
        """Run the pipeline schedule with this description now. Returns its id."""
        sched = next((s for s in self.paginate(self.p("/pipeline_schedules")) if s.get("description") == description),
                     None)
        if sched is None:
            raise GitLabError(404, f"no pipeline schedule named {description!r}")
        self.request("POST", self.p(f"/pipeline_schedules/{sched['id']}/play"))
        return sched["id"]

    def current_user(self) -> dict:
        return self.get("/user")

"""Canary probes against the canary tag URL only (SRS §12.2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import httpx

TLS_REJECT_CODES = {"SELF_SIGNED_CERT_IN_CHAIN", "UNABLE_TO_GET_ISSUER_CERT_LOCALLY",
                    "UNABLE_TO_VERIFY_LEAF_SIGNATURE", "CERT_UNTRUSTED"}


@dataclass
class ProbeResult:
    probe: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _deliver(client: httpx.Client, url: str, target: str) -> tuple[int, dict]:
    r = client.post(f"{url}/api/v1/webhooks/deliver", json={"target": target})
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:200]}
    return r.status_code, body


def partner_trusted_ok(client: httpx.Client, url: str, **_: Any) -> ProbeResult:
    status, body = _deliver(client, url, "trusted")
    return ProbeResult("partner_trusted_ok", status == 200 and body.get("delivered") is True,
                       {"status": status, "body": body})


def partner_distrusted_rejected(client: httpx.Client, url: str, **_: Any) -> ProbeResult:
    status, body = _deliver(client, url, "distrusted")
    ok = status == 502 and body.get("delivered") is False and body.get("tls_error") in TLS_REJECT_CODES
    return ProbeResult("partner_distrusted_rejected", ok, {"status": status, "body": body})


def runtime_report_matches(client: httpx.Client, url: str, *, expect_revision: str | None = None,
                           rules_check: Callable[[dict], list] | None = None, **_: Any) -> ProbeResult:
    r = client.get(f"{url}/internal/runtime")
    report = r.json() if r.status_code == 200 else {}
    detail: dict[str, Any] = {"status": r.status_code, "revision": report.get("revision"),
                              "node_version": report.get("node_version")}
    ok = r.status_code == 200
    if expect_revision is not None:
        ok &= report.get("revision") == expect_revision
        detail["expected_revision"] = expect_revision
    if rules_check is not None and report:
        findings = rules_check(report)
        detail["findings"] = [f.model_dump() for f in findings]
        ok &= not findings
    return ProbeResult("runtime_report_matches", ok, detail)


def migration_applied(client: httpx.Client, url: str, *, min_schema_version: str | None = None,
                      **_: Any) -> ProbeResult:
    r = client.get(f"{url}/status.json")
    st = r.json() if r.status_code == 200 else {}
    schema = st.get("schema_version")
    invalid = st.get("invalid_indexes", [])
    ok = r.status_code == 200 and not invalid and (
        min_schema_version is None or (schema is not None and str(schema) >= min_schema_version))
    return ProbeResult("migration_applied", ok, {"status": r.status_code, "schema_version": schema,
                                                 "min_schema_version": min_schema_version,
                                                 "invalid_indexes": invalid})


PROBES: dict[str, Callable[..., ProbeResult]] = {
    "partner_trusted_ok": partner_trusted_ok,
    "partner_distrusted_rejected": partner_distrusted_rejected,
    "runtime_report_matches": runtime_report_matches,
    "migration_applied": migration_applied,
}


def run(url: str, probes: list[str], *, client: httpx.Client | None = None, **kw: Any) -> list[ProbeResult]:
    own = client is None
    client = client or httpx.Client(timeout=15.0)
    try:
        results = []
        for name in dict.fromkeys(probes):  # de-duplicate, keep order
            try:
                results.append(PROBES[name](client, url.rstrip("/"), **kw))
            except (httpx.HTTPError, ValueError) as e:
                results.append(ProbeResult(name, False, {"error": f"{e.__class__.__name__}: {e}"}))
        return results
    finally:
        if own:
            client.close()

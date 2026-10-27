"""Pydantic v2 models for the data contracts in SRS §6. Unknown keys are errors."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CLAIM_ID_RE = re.compile(r"^[a-z0-9-]+$")
KNOWN_RULES = {"R0", "R1", "R2", "R3", "R4", "M1", "M3", "M4", "M5"}
KNOWN_COLLECTORS = {"runtime_report", "node_feed", "root_feed", "pending_migrations", "pg_stats"}

Severity = Literal["low", "medium", "high", "critical"]
Target = Literal["serving", "candidate"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Metadata(Strict):
    id: str
    owner: str
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not CLAIM_ID_RE.match(v):
            raise ValueError(f"claim id {v!r} must match ^[a-z0-9-]+$")
        return v


class EvidenceSpec(Strict):
    collector: str
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("collector")
    @classmethod
    def _collector(cls, v: str) -> str:
        if v not in KNOWN_COLLECTORS:
            raise ValueError(f"unknown collector {v!r}")
        return v


class FingerprintSpec(Strict):
    include: list[str]


class Remediation(Strict):
    flow: str
    playbook: str
    allowPaths: list[str] = Field(min_length=1)
    maxAttemptsPer24h: int = Field(ge=1, le=5)


class Reproof(Strict):
    preDeploy: list[str] = Field(default_factory=list)
    canary: list[str] = Field(default_factory=list)


class Rollout(Strict):
    soakSeconds: int = Field(ge=0)


class ClaimSpec(Strict):
    assertion: str
    targets: list[Target]
    evidence: list[EvidenceSpec]
    fingerprint: FingerprintSpec
    rules: list[str]
    remediation: Remediation
    reproof: Reproof
    rollout: Rollout

    @field_validator("rules")
    @classmethod
    def _rules(cls, v: list[str]) -> list[str]:
        unknown = [r for r in v if r not in KNOWN_RULES]
        if unknown:
            raise ValueError(f"unknown rules {unknown}")
        return v


class Claim(Strict):
    apiVersion: Literal["stale/v2"]
    kind: Literal["Claim"]
    metadata: Metadata
    spec: ClaimSpec

    @property
    def id(self) -> str:
        return self.metadata.id


class Finding(Strict):
    rule: str
    severity: Severity
    detail: dict[str, Any] = Field(default_factory=dict)


class ClaimEvidence(Strict):
    evidence: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class EvidenceFile(Strict):
    schema_: Literal["stale.evidence/v2"] = Field("stale.evidence/v2", alias="schema")
    target: Target
    collected_at: str
    claims: dict[str, ClaimEvidence]

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class LockEntry(Strict):
    fingerprint: str | None = None
    minted_at: str | None = None
    pipeline_id: int | None = None
    job_id: int | None = None
    revision: str | None = None
    commit_sha: str | None = None
    issue_iid: int | None = None
    mr_iids: list[int] = Field(default_factory=list)


class SciTotals(Strict):
    reproofs_skipped: int = 0
    runner_seconds_avoided: float = 0
    gco2eq_avoided: float = 0.0


class Lockfile(Strict):
    schema_: Literal["stale.lock/v2"] = Field("stale.lock/v2", alias="schema")
    claims: dict[str, LockEntry]
    sci: SciTotals = Field(default_factory=SciTotals)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

"""Derived claim state (SRS §6.4). Live state is never stored; it comes from issue labels + rules."""

from __future__ import annotations

import re
from enum import Enum
from typing import Iterable

CLAIM_PREFIX = "stale-claim::"
STATE_PREFIX = "stale-state::"
ATTEMPT_PREFIX = "stale-attempt::"
REMEDIATION_LABEL = "stale-remediation"
RETRY_LABEL = "stale-retry"
STATE_VALUES = ("stale", "remediating", "reproving", "retry-pending", "quarantined")
ATTEMPT_RE = re.compile(r"^stale-attempt::(\d+)$")


class State(str, Enum):
    VALID = "VALID"
    STALE = "STALE"
    REMEDIATING = "REMEDIATING"
    REPROVING = "REPROVING"
    QUARANTINED = "QUARANTINED"


def claim_label(claim_id: str) -> str:
    return CLAIM_PREFIX + claim_id


def state_label(value: str) -> str:
    if value not in STATE_VALUES:
        raise ValueError(f"unknown state label value {value!r}")
    return STATE_PREFIX + value


def state_values(labels: Iterable[str]) -> set[str]:
    return {lbl[len(STATE_PREFIX):] for lbl in labels if lbl.startswith(STATE_PREFIX)}


def claim_of(labels: Iterable[str]) -> list[str]:
    return sorted(lbl[len(CLAIM_PREFIX):] for lbl in labels if lbl.startswith(CLAIM_PREFIX))


def attempt(labels: Iterable[str]) -> int:
    nums = [int(m.group(1)) for lbl in labels if (m := ATTEMPT_RE.match(lbl))]
    return max(nums, default=0)


def derive(issue_labels: Iterable[str] | None, rules_fire: bool) -> State:
    """Evaluate the §6.4 table in order. `issue_labels` is None when no claim issue is open."""
    if issue_labels is not None:
        values = state_values(issue_labels)
        if "quarantined" in values:
            return State.QUARANTINED
        if "reproving" in values:
            return State.REPROVING
        if values & {"remediating", "retry-pending"}:
            return State.REMEDIATING
        if "stale" in values:
            return State.STALE
    return State.STALE if rules_fire else State.VALID


def transition(new_value: str) -> dict[str, list[str]]:
    """add/remove label lists that leave exactly one stale-state:: label (works without scoped labels)."""
    target = state_label(new_value)
    return {"add_labels": [target],
            "remove_labels": [state_label(v) for v in STATE_VALUES if v != new_value]}

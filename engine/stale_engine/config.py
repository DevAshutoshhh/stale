"""Environment-variable registry (SRS §4). Every variable the engine reads is listed here."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import quote


class ConfigError(RuntimeError):
    """A required variable is missing. The CLI maps this to exit code 2."""


@dataclass(frozen=True)
class Var:
    name: str
    default: str | None = None
    secret: bool = False


REGISTRY: dict[str, Var] = {
    v.name: v
    for v in [
        # §4.1 GitLab CI/CD variables
        Var("STALE_BOT_TOKEN", secret=True),
        Var("STALE_REMEDIATOR_USER_ID"),
        Var("STALE_ANALYST_USER_ID"),
        Var("STALE_REMEDIATOR_USERNAME"),
        Var("STALE_HUMAN_ESCALATION_USER_ID"),
        Var("GCP_PROJECT_ID"),
        Var("GCP_PROJECT_NUMBER"),
        Var("GCP_REGION", "us-central1"),
        Var("WIF_POOL"),
        Var("WIF_PROVIDER"),
        Var("GCP_DEPLOYER_SA"),
        Var("AR_REPO", "stale"),
        Var("CLOUDSQL_INSTANCE"),
        Var("DB_NAME", "ledger"),
        Var("DB_USER", "stale_migrator"),
        Var("DB_PASSWORD", secret=True),
        Var("STALE_FEEDS_BASE_URL"),
        Var("STALE_ROW_THRESHOLD", "500000"),
        Var("STALE_GRID_INTENSITY_GCO2_KWH", "440"),
        Var("STALE_PIPELINE"),
        # §4.2 runtime (also used by local tooling)
        Var("DATABASE_URL"),
        # Predefined GitLab CI variables the engine reads
        Var("CI_API_V4_URL", "https://gitlab.com/api/v4"),
        Var("CI_PROJECT_ID"),
        Var("CI_PIPELINE_ID"),
        Var("CI_JOB_ID"),
        Var("CI_COMMIT_SHA"),
        Var("CI_DEFAULT_BRANCH", "main"),
        Var("CI_PROJECT_URL"),
    ]
}


def get(name: str) -> str | None:
    if name not in REGISTRY:
        raise KeyError(f"{name} is not in the config registry (SRS §4)")
    val = os.environ.get(name)
    if val is None or val == "":
        return REGISTRY[name].default
    return val


def require(*names: str) -> dict[str, str]:
    """Return the values for `names`, or raise ConfigError naming every missing one."""
    values = {n: get(n) for n in names}
    missing = sorted(n for n, v in values.items() if v is None)
    if missing:
        raise ConfigError("missing required environment variables: " + ", ".join(missing))
    return values  # type: ignore[return-value]


def row_threshold() -> int:
    return int(get("STALE_ROW_THRESHOLD") or 500000)


def database_url() -> str:
    """DATABASE_URL if set, else the Cloud SQL proxy on 127.0.0.1:5432 with DB_* credentials."""
    url = get("DATABASE_URL")
    if url:
        return url
    v = require("DB_USER", "DB_PASSWORD", "DB_NAME")
    return f"postgresql://{quote(v['DB_USER'])}:{quote(v['DB_PASSWORD'], safe='')}@127.0.0.1:5432/{v['DB_NAME']}"


_VAR_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def expand(value: str) -> str:
    """Expand ${VAR} references in claim config. Unknown or unset variables raise ConfigError."""

    def sub(m: re.Match[str]) -> str:
        return require(m.group(1))[m.group(1)]

    return _VAR_RE.sub(sub, value)

#!/usr/bin/env bash
# Create the Stale labels (SRS §4.3). Idempotent. Usage: infra/gitlab-labels.sh <project id or path>
# On tiers without scoped labels, the same `a::b` names are created as plain labels; the engine
# enforces "exactly one stale-state::" itself (state.transition removes the others).
set -euo pipefail
PROJECT="${1:?project id or url-encoded path}"
label() { # name color description
  if glab api -X POST "projects/${PROJECT}/labels" -f name="$1" -f color="$2" -f description="$3" >/dev/null 2>&1; then
    echo "created $1"
  else
    glab api -X PUT "projects/${PROJECT}/labels/$(jq -rn --arg n "$1" '$n|@uri')" -f color="$2" -f description="$3" >/dev/null
    echo "exists  $1"
  fi
}
for c in anchor-trusted migration-safe; do
  label "stale-claim::$c" "#6699cc" "Issue/MR belongs to claim $c"
done
label "stale-state::stale"         "#d9534f" "Rules fire on serving evidence; no fix in flight"
label "stale-state::remediating"   "#f0ad4e" "Remediator flow assigned"
label "stale-state::reproving"     "#5bc0de" "Remediation MR pipeline is re-proving the claim"
label "stale-state::retry-pending" "#ad8d43" "Canary failed and rolled back; retry pending"
label "stale-state::quarantined"   "#8e44ad" "Policy violation or attempts exhausted; a human must act"
label "stale-attempt::1" "#cccccc" "Remediation attempt 1 in the current 24 h window"
label "stale-attempt::2" "#999999" "Remediation attempt 2 in the current 24 h window"
label "stale-remediation" "#2e7d32" "MR authored by the stale-remediator flow"
label "stale-retry"       "#795548" "MR is a retry after a failed canary"

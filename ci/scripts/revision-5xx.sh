#!/usr/bin/env bash
# Per-revision 5xx check (SRS §12.3): fail if the canary's 5xx ratio exceeds the stable revision's + 1%.
# Usage: revision-5xx.sh CANARY_REVISION STABLE_REVISION START END   (RFC 3339 UTC)
# Cloud Monitoring ingests Cloud Run metrics with ~1-3 min delay; we wait, and report "no data" honestly.
set -euo pipefail
CANARY="$1" STABLE="$2" START="$3"
sleep "${STALE_METRICS_WAIT_SECONDS:-60}"
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
DUR=$(( $(date -d "$END" +%s) - $(date -d "$START" +%s) )); [ "$DUR" -ge 60 ] || DUR=60
TOKEN=$(gcloud auth print-access-token)
count() { # revision extra-filter
  local f='metric.type="run.googleapis.com/request_count" AND resource.type="cloud_run_revision" AND resource.labels.service_name="ledger-api"'
  f="$f AND resource.labels.revision_name=\"$1\"$2"
  curl -sf -G -H "Authorization: Bearer $TOKEN" \
    "https://monitoring.googleapis.com/v3/projects/${GCP_PROJECT_ID}/timeSeries" \
    --data-urlencode "filter=$f" --data-urlencode "interval.startTime=$START" --data-urlencode "interval.endTime=$END" \
    --data-urlencode "aggregation.alignmentPeriod=${DUR}s" --data-urlencode "aggregation.perSeriesAligner=ALIGN_SUM" \
    --data-urlencode "aggregation.crossSeriesReducer=REDUCE_SUM" |
    jq '[.timeSeries[]?.points[]?.value.int64Value | tonumber] | add // 0'
}
FIVE=' AND metric.labels.response_code_class="5xx"'
c_all=$(count "$CANARY" ""); c_5xx=$(count "$CANARY" "$FIVE")
s_all=0; s_5xx=0
if [ -n "$STABLE" ]; then s_all=$(count "$STABLE" ""); s_5xx=$(count "$STABLE" "$FIVE"); fi
echo "5xx window $START..$END: canary $CANARY ${c_5xx}/${c_all}; stable ${STABLE:-none} ${s_5xx}/${s_all}"
if [ "$c_all" -eq 0 ]; then
  echo "no Cloud Monitoring data for the canary yet (ingestion delay); k6 thresholds remain the gate for this ring"
  exit 0
fi
python3 - "$c_5xx" "$c_all" "$s_5xx" "$s_all" <<'EOF'
import sys
c5, ca, s5, sa = map(float, sys.argv[1:])
canary, base = c5 / ca, (s5 / sa if sa else 0.0)
print(f"canary 5xx ratio {canary:.4f} vs stable {base:.4f} (+0.01 allowed)")
sys.exit(1 if canary > base + 0.01 else 0)
EOF

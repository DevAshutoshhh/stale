#!/usr/bin/env bash
# Release-lane steps for the Cloud Run service (SRS §15.3). Usage: ci/scripts/release.sh <step>
# Steps: record-stable | deploy-canary | canary-ring | promote | rollback | stable-url
set -euo pipefail
SERVICE=ledger-api
REGION="${GCP_REGION:?}"
run() { gcloud run services "$@" --region "$REGION" --quiet; }
describe() { gcloud run services describe "$SERVICE" --region "$REGION" --format=json; }

case "${1:?step}" in
record-stable)
  STABLE=""
  if describe > /tmp/svc.json 2>/dev/null; then
    STABLE=$(jq -r '[.status.traffic[]? | select(.percent==100) | .revisionName][0] // empty' /tmp/svc.json)
    if [ -z "$STABLE" ]; then
      echo "traffic is split; refusing to release until it is 100% on one revision:"
      jq -c '.status.traffic' /tmp/svc.json; exit 1
    fi
  fi
  echo "STABLE_REVISION=${STABLE}" > record-stable.env
  echo "stable revision: ${STABLE:-<none: first deploy>}"
  ;;

deploy-canary)
  export NEW_REVISION="${SERVICE}-p${CI_PIPELINE_ID}" CANARY_TAG="c-${CI_PIPELINE_ID}"
  export RUNTIME_SA="stale-runtime@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
  export LEDGER_IMAGE="${AR_IMAGE_BASE}/ledger-api:${CI_COMMIT_SHA}" PARTNER_IMAGE="${AR_IMAGE_BASE}/partner-sim:${CI_COMMIT_SHA}"
  export STALE_LOCKFILE_URL="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/repository/files/.stale%2Fclaims.lock.json/raw?ref=${CI_DEFAULT_BRANCH}"
  if [ -n "${STABLE_REVISION:-}" ]; then
    TRAFFIC_BLOCK=$(printf '  - revisionName: %s\n    percent: 100\n    tag: stable\n  - revisionName: %s\n    percent: 0\n    tag: %s' \
      "$STABLE_REVISION" "$NEW_REVISION" "$CANARY_TAG")
  else
    TRAFFIC_BLOCK=$(printf '  - revisionName: %s\n    percent: 100\n    tag: %s' "$NEW_REVISION" "$CANARY_TAG")
  fi
  export TRAFFIC_BLOCK
  envsubst '$GCP_REGION $NEW_REVISION $RUNTIME_SA $LEDGER_IMAGE $PARTNER_IMAGE $CLOUDSQL_INSTANCE $STALE_LOCKFILE_URL $CI_PIPELINE_ID $CI_COMMIT_SHA $STALE_FEEDS_BASE_URL $STALE_ROW_THRESHOLD $TRAFFIC_BLOCK' \
    < infra/service.ledger-api.yaml > service.rendered.yaml
  run replace service.rendered.yaml
  gcloud run services add-iam-policy-binding "$SERVICE" --region "$REGION" --member=allUsers \
    --role=roles/run.invoker --quiet > /dev/null
  describe > /tmp/svc.json
  CANARY_URL=$(jq -r --arg t "$CANARY_TAG" '.status.traffic[] | select(.tag==$t) | .url' /tmp/svc.json)
  SERVICE_URL=$(jq -r '.status.url' /tmp/svc.json)
  [ -n "$CANARY_URL" ] || { echo "no URL for tag $CANARY_TAG"; jq -c .status.traffic /tmp/svc.json; exit 1; }
  printf 'CANARY_REVISION=%s\nCANARY_TAG=%s\nCANARY_URL=%s\nSERVICE_URL=%s\n' \
    "$NEW_REVISION" "$CANARY_TAG" "$CANARY_URL" "$SERVICE_URL" > deploy-canary.env
  cat deploy-canary.env
  jq -c '[.status.traffic[] | {revisionName, percent, tag}]' /tmp/svc.json
  ;;

canary-ring)
  RINGS="${STALE_RINGS:-50 100}"
  [ -n "${STABLE_REVISION:-}" ] || RINGS="100"   # first deploy: the canary already has 100%
  for R in $RINGS; do
    echo "== ring ${R}% -> ${CANARY_REVISION}"
    run update-traffic "$SERVICE" --to-tags "${CANARY_TAG}=${R}" > /dev/null
    describe | jq -c '[.status.traffic[] | {revisionName, percent, tag}]'
    if [ "${STALE_DEMO_BREAK_CANARY:-0}" = "1" ]; then
      echo "STALE_DEMO_BREAK_CANARY=1: demo switch forces ring ${R}% to fail"; exit 1
    fi
    START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    k6 run --quiet -e BASE_URL="$SERVICE_URL" -e SOAK_SECONDS="${SOAK_SECONDS:-120}" loadtest/ring_slo.js
    END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    bash ci/scripts/revision-5xx.sh "$CANARY_REVISION" "${STABLE_REVISION:-}" "$START" "$END"
  done
  ;;

promote)
  run update-traffic "$SERVICE" --to-revisions "${CANARY_REVISION}=100" > /dev/null
  run update-traffic "$SERVICE" --update-tags "stable=${CANARY_REVISION}" > /dev/null
  run update-traffic "$SERVICE" --remove-tags "${CANARY_TAG}" > /dev/null
  describe > /tmp/svc.json
  STABLE_URL=$(jq -r '.status.traffic[] | select(.tag=="stable") | .url' /tmp/svc.json)
  printf 'STABLE_URL=%s\n' "$STABLE_URL" > promote.env
  jq -c '[.status.traffic[] | {revisionName, percent, tag}]' /tmp/svc.json
  echo "promoted ${CANARY_REVISION}; stable tag URL ${STABLE_URL}"
  ;;

rollback)
  if [ -n "${STABLE_REVISION:-}" ]; then
    run update-traffic "$SERVICE" --to-revisions "${STABLE_REVISION}=100" > /dev/null
    run update-traffic "$SERVICE" --update-tags "stable=${STABLE_REVISION}" > /dev/null || true
    [ -z "${CANARY_TAG:-}" ] || run update-traffic "$SERVICE" --remove-tags "${CANARY_TAG}" > /dev/null || true
    echo "rolled back: 100% -> ${STABLE_REVISION}"
  else
    echo "first deploy failed: there is no stable revision to restore"
  fi
  describe > /tmp/svc.json 2>/dev/null || echo '{}' > /tmp/svc.json
  FAILED='[]'
  if [ -n "${STALE_BOT_TOKEN:-}" ]; then
    FAILED=$(curl -sf -H "PRIVATE-TOKEN: ${STALE_BOT_TOKEN}" \
      "${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/pipelines/${CI_PIPELINE_ID}/jobs?scope[]=failed&per_page=100" |
      jq '[.[] | {name, stage, failure_reason, web_url}]' || echo '[]')
  fi
  CANARY_REPORT=$(cat .stale/out/canary-report.json 2>/dev/null || echo 'null')
  jq -n --arg pipeline "$CI_PIPELINE_URL" --arg stable "${STABLE_REVISION:-}" --arg canary "${CANARY_REVISION:-}" \
    --arg brk "${STALE_DEMO_BREAK_CANARY:-0}" --argjson failed "$FAILED" --argjson canary_report "$CANARY_REPORT" \
    --argjson traffic "$(jq -c '[.status.traffic[]? | {revisionName, percent, tag}]' /tmp/svc.json)" \
    '{schema:"stale.rollback/v1", pipeline:$pipeline, restored_revision:$stable, failed_revision:$canary,
      demo_break_canary:($brk=="1"), failed_jobs:$failed, traffic_after:$traffic, canary_report:$canary_report}' \
    > rollback-reason.json
  cat rollback-reason.json
  ;;

stable-url)
  describe | jq -r '(.status.traffic[] | select(.tag=="stable") | .url) // .status.url'
  ;;
*) echo "unknown step $1"; exit 2 ;;
esac

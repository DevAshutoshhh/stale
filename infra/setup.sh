#!/usr/bin/env bash
# Idempotent Google Cloud setup for Stale (SRS §16, build-prompt Phase 3). Safe to re-run.
#
#   GITLAB_PROJECT_PATH=group/project [GITLAB_PROJECT_ID=123] infra/setup.sh
#
# Creates: APIs, Artifact Registry repo, Cloud SQL Postgres (db `ledger`, users stale_app and
# stale_migrator), the public feeds bucket, service accounts stale-runtime / stale-deployer,
# and the GitLab Workload Identity Federation pool + provider restricted to GITLAB_PROJECT_PATH.
#
# Secrets: DB passwords are generated here and stored ONLY in Secret Manager and (when
# GITLAB_PROJECT_ID is set and `glab` is logged in) as masked+protected GitLab CI variables.
# They are never printed. No service-account key is ever created.
set -euo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

PROJECT="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GCP_REGION:-us-central1}"
GITLAB_PROJECT_PATH="${GITLAB_PROJECT_PATH:?set GITLAB_PROJECT_PATH, e.g. DevAshutoshhh/stale-dev}"
GITLAB_PROJECT_ID="${GITLAB_PROJECT_ID:-}"
INSTANCE="${CLOUDSQL_INSTANCE_NAME:-stale-pg}"
PG_VERSION="${PG_VERSION:-POSTGRES_18}"
AR_REPO="${AR_REPO:-stale}"
BUCKET="${PROJECT}-stale-world"
POOL="${WIF_POOL:-gitlab-pool}"
PROVIDER="${WIF_PROVIDER:-gitlab-provider}"
RUNTIME_SA="stale-runtime@${PROJECT}.iam.gserviceaccount.com"
DEPLOYER_SA="stale-deployer@${PROJECT}.iam.gserviceaccount.com"
PROXY_VERSION="v2.26.3"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

log() { printf '\n== %s\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }
retry() { # newly enabled APIs take a minute or two to propagate
  local i; for i in 1 2 3 4 5 6; do "$@" && return 0; echo "  retrying in 20s ($i/6)"; sleep 20; done; return 1; }
gen_password() { python3 -c 'import secrets,string; a=string.ascii_letters+string.digits; print("".join(secrets.choice(a) for _ in range(32)))'; }

NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
log "project $PROJECT ($NUMBER), region $REGION, GitLab project $GITLAB_PROJECT_PATH"

log "APIs"
gcloud services enable run.googleapis.com sqladmin.googleapis.com artifactregistry.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com monitoring.googleapis.com \
  storage.googleapis.com secretmanager.googleapis.com cloudresourcemanager.googleapis.com \
  --project "$PROJECT"

log "Artifact Registry repo $AR_REPO"
exists gcloud artifacts repositories describe "$AR_REPO" --location "$REGION" --project "$PROJECT" ||
  retry gcloud artifacts repositories create "$AR_REPO" --repository-format=docker --location "$REGION" \
    --description="Stale images" --project "$PROJECT"
# Keep storage inside the free tier: delete untagged/old candidate images after 3 days, keep 10 newest.
cat > /tmp/stale-ar-cleanup.json <<'JSON'
[{"name":"keep-recent","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":10}},
 {"name":"delete-old","action":{"type":"Delete"},"condition":{"olderThan":"259200s"}}]
JSON
gcloud artifacts repositories set-cleanup-policies "$AR_REPO" --location "$REGION" --project "$PROJECT" \
  --policy=/tmp/stale-ar-cleanup.json --no-dry-run >/dev/null

log "Cloud SQL instance $INSTANCE ($PG_VERSION, db-f1-micro)"
if ! exists gcloud sql instances describe "$INSTANCE" --project "$PROJECT"; then
  retry gcloud sql instances create "$INSTANCE" --project "$PROJECT" --region "$REGION" \
    --database-version="$PG_VERSION" --edition=ENTERPRISE --tier=db-f1-micro \
    --storage-type=SSD --storage-size=10 --no-storage-auto-increase --availability-type=zonal \
    --assign-ip --no-backup --deletion-protection
fi
CONN="$(gcloud sql instances describe "$INSTANCE" --project "$PROJECT" --format='value(connectionName)')"
exists gcloud sql databases describe ledger --instance "$INSTANCE" --project "$PROJECT" ||
  gcloud sql databases create ledger --instance "$INSTANCE" --project "$PROJECT"

secret_value() { gcloud secrets versions access latest --secret "$1" --project "$PROJECT" 2>/dev/null || true; }
put_secret() { # name value(stdin)
  if exists gcloud secrets describe "$1" --project "$PROJECT"; then
    gcloud secrets versions add "$1" --data-file=- --project "$PROJECT" >/dev/null
  else
    gcloud secrets create "$1" --replication-policy=automatic --data-file=- --project "$PROJECT" >/dev/null
  fi
}
ensure_db_user() { # user secret-name -> password on stdout (never echoed to terminal by callers)
  local user="$1" secret="$2" pw
  pw="$(secret_value "$secret")"
  if [ -z "$pw" ]; then
    pw="$(gen_password)"
    printf '%s' "$pw" | put_secret "$secret"
    if exists gcloud sql users describe "$user" --instance "$INSTANCE" --project "$PROJECT"; then
      gcloud sql users set-password "$user" --instance "$INSTANCE" --password="$pw" --project "$PROJECT" >/dev/null
    else
      gcloud sql users create "$user" --instance "$INSTANCE" --password="$pw" --project "$PROJECT" >/dev/null
    fi
  elif ! exists gcloud sql users describe "$user" --instance "$INSTANCE" --project "$PROJECT"; then
    gcloud sql users create "$user" --instance "$INSTANCE" --password="$pw" --project "$PROJECT" >/dev/null
  fi
  printf '%s' "$pw"
}
log "database users (passwords -> Secret Manager only)"
MIGRATOR_PW="$(ensure_db_user stale_migrator stale-migrator-password)"
APP_PW="$(ensure_db_user stale_app stale-app-password)"
printf 'postgresql://stale_app:%s@/ledger?host=/cloudsql/%s' "$APP_PW" "$CONN" | {
  current="$(secret_value stale-app-database-url)"; new="$(cat)"
  [ "$current" = "$new" ] || printf '%s' "$new" | put_secret stale-app-database-url; }
echo "secrets: stale-migrator-password, stale-app-password, stale-app-database-url"

log "grants: stale_app gets DML on tables stale_migrator creates"
if ! command -v cloud-sql-proxy >/dev/null; then
  curl -fsSL -o /tmp/cloud-sql-proxy \
    "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/${PROXY_VERSION}/cloud-sql-proxy.linux.amd64"
  chmod +x /tmp/cloud-sql-proxy; PROXY=/tmp/cloud-sql-proxy
else PROXY=cloud-sql-proxy; fi
"$PROXY" --gcloud-auth --port 15432 "$CONN" >/tmp/stale-proxy.log 2>&1 & PROXY_PID=$!
trap 'kill $PROXY_PID 2>/dev/null || true' EXIT
for _ in $(seq 30); do (exec 3<>/dev/tcp/127.0.0.1/15432) 2>/dev/null && break; sleep 1; done
PGPASSWORD="$MIGRATOR_PW" psql -q -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 15432 -U stale_migrator -d ledger <<'SQL'
ALTER DEFAULT PRIVILEGES FOR ROLE stale_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO stale_app;
ALTER DEFAULT PRIVILEGES FOR ROLE stale_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO stale_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO stale_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO stale_app;
SQL
kill $PROXY_PID 2>/dev/null || true; trap - EXIT
echo "grants applied"

log "feeds bucket gs://$BUCKET (public read; feeds only)"
exists gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" ||
  gcloud storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access --project "$PROJECT"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member=allUsers \
  --role=roles/storage.objectViewer --project "$PROJECT" >/dev/null
gcloud storage cp "$REPO_ROOT"/demo/feeds/baseline/*.json "gs://$BUCKET/feeds/" \
  --cache-control="no-store" --content-type=application/json --project "$PROJECT"

log "service accounts"
exists gcloud iam service-accounts describe "$RUNTIME_SA" --project "$PROJECT" ||
  gcloud iam service-accounts create stale-runtime --display-name="Stale ledger-api runtime" --project "$PROJECT"
exists gcloud iam service-accounts describe "$DEPLOYER_SA" --project "$PROJECT" ||
  gcloud iam service-accounts create stale-deployer --display-name="Stale CI deployer (WIF only)" --project "$PROJECT"
bind() { gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$1" --role="$2" \
           --condition=None >/dev/null && echo "  $1 -> $2"; }
bind "$RUNTIME_SA" roles/cloudsql.client
for role in roles/run.admin roles/artifactregistry.writer roles/cloudsql.client roles/monitoring.viewer; do
  bind "$DEPLOYER_SA" "$role"
done
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" --member="serviceAccount:$DEPLOYER_SA" \
  --role=roles/iam.serviceAccountUser --project "$PROJECT" >/dev/null && echo "  deployer actAs runtime"
gcloud secrets add-iam-policy-binding stale-app-database-url --member="serviceAccount:$RUNTIME_SA" \
  --role=roles/secretmanager.secretAccessor --project "$PROJECT" >/dev/null && echo "  runtime reads stale-app-database-url"

log "Workload Identity Federation: $POOL / $PROVIDER (issuer https://gitlab.com)"
exists gcloud iam workload-identity-pools describe "$POOL" --location=global --project "$PROJECT" ||
  gcloud iam workload-identity-pools create "$POOL" --location=global --display-name="GitLab" --project "$PROJECT"
CONDITION="assertion.project_path == \"${GITLAB_PROJECT_PATH}\""
MAPPING="google.subject=assertion.sub,attribute.project_path=assertion.project_path,attribute.ref=assertion.ref,attribute.ref_protected=assertion.ref_protected"
if exists gcloud iam workload-identity-pools providers describe "$PROVIDER" --workload-identity-pool="$POOL" --location=global --project "$PROJECT"; then
  gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" --workload-identity-pool="$POOL" \
    --location=global --attribute-mapping="$MAPPING" --attribute-condition="$CONDITION" --project "$PROJECT" >/dev/null
else
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --workload-identity-pool="$POOL" \
    --location=global --issuer-uri="https://gitlab.com" --attribute-mapping="$MAPPING" \
    --attribute-condition="$CONDITION" --project "$PROJECT"
fi
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.project_path/${GITLAB_PROJECT_PATH}" \
  --project "$PROJECT" >/dev/null && echo "  $GITLAB_PROJECT_PATH may impersonate $DEPLOYER_SA"

declare -A VARS=(
  [GCP_PROJECT_ID]="$PROJECT" [GCP_PROJECT_NUMBER]="$NUMBER" [GCP_REGION]="$REGION"
  [WIF_POOL]="$POOL" [WIF_PROVIDER]="$PROVIDER" [GCP_DEPLOYER_SA]="$DEPLOYER_SA" [AR_REPO]="$AR_REPO"
  [CLOUDSQL_INSTANCE]="$CONN" [DB_NAME]="ledger" [DB_USER]="stale_migrator"
  [STALE_FEEDS_BASE_URL]="https://storage.googleapis.com/${BUCKET}/feeds"
  [STALE_ROW_THRESHOLD]="500000" [STALE_GRID_INTENSITY_GCO2_KWH]="440"
)
log "CI/CD variables (non-secret)"
for k in $(printf '%s\n' "${!VARS[@]}" | sort); do printf '  %-30s %s\n' "$k" "${VARS[$k]}"; done

if [ -n "$GITLAB_PROJECT_ID" ] && command -v glab >/dev/null; then
  log "writing CI/CD variables to GitLab project $GITLAB_PROJECT_ID"
  setvar() { # key value masked protected  (value via stdin-free form-data; never printed)
    local key="$1" val="$2" masked="$3" protected="$4" api="projects/${GITLAB_PROJECT_ID}/variables"
    if glab api "$api/$key" >/dev/null 2>&1; then
      glab api -X PUT "$api/$key" -f value="$val" -F masked="$masked" -F protected="$protected" >/dev/null
    else
      glab api -X POST "$api" -f key="$key" -f value="$val" -F masked="$masked" -F protected="$protected" >/dev/null
    fi
    echo "  set $key (masked=$masked protected=$protected)"
  }
  for k in $(printf '%s\n' "${!VARS[@]}" | sort); do setvar "$k" "${VARS[$k]}" false false; done
  setvar DB_PASSWORD "$MIGRATOR_PW" true true
fi
log "done"

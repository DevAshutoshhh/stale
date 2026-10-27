# Sourced by CI jobs. Exchanges the job's GitLab OIDC token for short-lived Google credentials
# via Workload Identity Federation (SRS §2.3). No service-account key exists anywhere.
: "${GITLAB_OIDC_TOKEN:?id_tokens GITLAB_OIDC_TOKEN is missing (extend .gcp_auth)}"
printf '%s' "$GITLAB_OIDC_TOKEN" > /tmp/oidc
gcloud iam workload-identity-pools create-cred-config \
  "projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}" \
  --service-account="${GCP_DEPLOYER_SA}" --credential-source-file=/tmp/oidc \
  --output-file=/tmp/gcp.json --quiet
export GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp.json CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE=/tmp/gcp.json
export CLOUDSDK_CORE_PROJECT="$GCP_PROJECT_ID" CLOUDSDK_RUN_REGION="$GCP_REGION" CLOUDSDK_CORE_DISABLE_PROMPTS=1
echo "GCP: credential type $(jq -r .type /tmp/gcp.json), impersonating ${GCP_DEPLOYER_SA}"

# CI image for Stale jobs: Python 3.12 + engine deps, gcloud, k6, cloud-sql-proxy, crane, psql, jq.
# Build context: repository root.  docker build -f ci/images/stale-ci/Dockerfile .
FROM python:3.12-slim-bookworm

ARG CLOUD_SQL_PROXY_VERSION=v2.26.3
ARG CRANE_VERSION=v0.22.1

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg jq gettext-base git postgresql-client; \
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg; \
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
      > /etc/apt/sources.list.d/google-cloud-sdk.list; \
    curl -fsSL https://dl.k6.io/key.gpg | gpg --dearmor -o /usr/share/keyrings/k6-archive-keyring.gpg; \
    echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
      > /etc/apt/sources.list.d/k6.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends google-cloud-cli k6; \
    rm -rf /var/lib/apt/lists/*; \
    curl -fsSLo /usr/local/bin/cloud-sql-proxy \
      "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/${CLOUD_SQL_PROXY_VERSION}/cloud-sql-proxy.linux.amd64"; \
    chmod +x /usr/local/bin/cloud-sql-proxy; \
    curl -fsSL "https://github.com/google/go-containerregistry/releases/download/${CRANE_VERSION}/go-containerregistry_Linux_x86_64.tar.gz" \
      | tar -xz -C /usr/local/bin crane

# Engine dependencies (jobs then `pip install --no-deps ./engine` for the current code).
COPY engine/pyproject.toml /tmp/engine/pyproject.toml
RUN python - <<'EOF' > /tmp/reqs.txt
import tomllib
p = tomllib.load(open("/tmp/engine/pyproject.toml", "rb"))["project"]
print("\n".join(p["dependencies"] + p["optional-dependencies"]["dev"]))
EOF
RUN pip install --no-cache-dir -r /tmp/reqs.txt && rm -rf /tmp/engine /tmp/reqs.txt

RUN gcloud --version | head -1 && k6 version && cloud-sql-proxy --version && crane version && psql --version

# Sourced after gcp-login.sh. Starts the Cloud SQL Auth Proxy on 127.0.0.1:5432 with the WIF
# credentials and exports DATABASE_URL for the migrator user.
cloud-sql-proxy --credentials-file /tmp/gcp.json --port 5432 "$CLOUDSQL_INSTANCE" > /tmp/cloud-sql-proxy.log 2>&1 &
for _ in $(seq 60); do (exec 3<>/dev/tcp/127.0.0.1/5432) 2>/dev/null && break; sleep 0.5; done
(exec 3<>/dev/tcp/127.0.0.1/5432) 2>/dev/null || { cat /tmp/cloud-sql-proxy.log; echo "cloud-sql-proxy did not start"; exit 2; }
export DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}"
echo "Cloud SQL: proxy up for ${CLOUDSQL_INSTANCE}"

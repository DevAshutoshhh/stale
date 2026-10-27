#!/usr/bin/env bash
# Phase 2 local acceptance (a)-(d) against docker compose. Prints real output; exits non-zero on failure.
# Usage: demo/local/acceptance.sh   (from the repo root, with the engine venv's `stale` on PATH)
set -euo pipefail
cd "$(dirname "$0")/../.."
API=http://127.0.0.1:8080
DB=postgresql://stale@127.0.0.1:5432/ledger
SHADOW=postgresql://stale@127.0.0.1:5432/shadow
EXCL=app/ledger-api/deploy/trust/excluded-spki.txt
G3=$(python3 - <<'EOF'
import hashlib, re
from cryptography import x509
from cryptography.hazmat.primitives import serialization
pem = open("app/partner-sim/certs/demo-root-g3.pem", "rb").read()
k = x509.load_pem_x509_certificate(pem).public_key()
print(hashlib.sha256(k.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest())
EOF
)
fail=0
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; fail=1; fi; }
deliver() { curl -s -o /tmp/deliver.json -w '%{http_code}' -X POST "$API/api/v1/webhooks/deliver" \
              -H 'content-type: application/json' -d "{\"target\":\"$1\"}"; }
wait_up() { for _ in $(seq 60); do curl -sf "$API/healthz" >/dev/null && return 0; sleep 1; done; return 1; }
restore() { : > "$EXCL"; }
trap restore EXIT

echo "== build + start (baseline: excluded-spki.txt empty)"
restore
docker compose up -d --build --wait postgres >/dev/null
docker compose up -d --build partner-sim ledger-api 2>&1 | tail -2
wait_up
stale migrate apply --database-url "$DB"
psql -q "$DB" -f demo/local/seed.sql

echo "== (a) baseline: both partners deliver"
code=$(deliver trusted);    echo "trusted    -> $code $(cat /tmp/deliver.json)"; check "a1 trusted 200 at baseline" '[ "$code" = 200 ]'
code=$(deliver distrusted); echo "distrusted -> $code $(cat /tmp/deliver.json)"; check "a2 distrusted 200 at baseline" '[ "$code" = 200 ]'
curl -s "$API/internal/runtime" | jq -c '{node_version, trust_policy, g3_present: (.effective_ca_spki | index("'"$G3"'") != null)}'

echo "== add Demo Root G3 SPKI ($G3) to $EXCL and rebuild"
echo "$G3" > "$EXCL"
docker compose up -d --build ledger-api 2>&1 | tail -1
sleep 2; wait_up
code=$(deliver trusted);    echo "trusted    -> $code $(cat /tmp/deliver.json)"; check "a3 trusted still 200" '[ "$code" = 200 ]'
code=$(deliver distrusted); echo "distrusted -> $code $(cat /tmp/deliver.json)"
check "a4 distrusted 502 with certificate error" \
  '[ "$code" = 502 ] && jq -e ".tls_error | IN(\"SELF_SIGNED_CERT_IN_CHAIN\",\"UNABLE_TO_GET_ISSUER_CERT_LOCALLY\",\"UNABLE_TO_VERIFY_LEAF_SIGNATURE\",\"CERT_UNTRUSTED\")" /tmp/deliver.json >/dev/null'

echo "== (b) /internal/runtime after exclusion"
curl -s "$API/internal/runtime" > /tmp/runtime.json
jq -c '{node_version, revision, trust_policy, effective_ca_count, g3_present: (.effective_ca_spki | index("'"$G3"'") != null)}' /tmp/runtime.json
check "b1 removed == 1" '[ "$(jq .trust_policy.removed /tmp/runtime.json)" = 1 ]'
check "b2 G3 absent from effective_ca_spki" '[ "$(jq "[.effective_ca_spki[] | select(. == \"$G3\")] | length" /tmp/runtime.json)" = 0 ]'

echo "== (c) /api/v1/balance isolation"
for u in alpha beta; do
  curl -s -D /tmp/h.txt "$API/api/v1/balance" -H "authorization: Bearer tok-$u" > /tmp/b.json
  echo "$u -> $(cat /tmp/b.json) | $(grep -i '^cache-control' /tmp/h.txt | tr -d '\r')"
  check "c $u sees only own balance" '[ "$(jq -r .user_id /tmp/b.json)" = "$u" ]'
  check "c $u Cache-Control private, no-store" 'grep -qi "^cache-control: private, no-store" /tmp/h.txt'
done
check "c alpha=10000, beta=25000" '[ "$(curl -s $API/api/v1/balance -H "authorization: Bearer tok-alpha" | jq .balance_cents)" = 10000 ] && [ "$(curl -s $API/api/v1/balance -H "authorization: Bearer tok-beta" | jq .balance_cents)" = 25000 ]'
code=$(curl -s -o /dev/null -w '%{http_code}' "$API/api/v1/balance"); echo "no token -> $code"
check "c missing token 401" '[ "$code" = 401 ]'

echo "== (d) shadow migration at 600k rows (threshold 500k)"
set +e
python3 -m stale_engine.reproof.shadow_migration --database-url "$SHADOW" --applied 20261020090000 --out /tmp/shadow-plain
plain=$?
tmpd=$(mktemp -d); cp app/ledger-api/db/migrations/20261020090000_init_ledger.sql "$tmpd/"
cat > "$tmpd/20261021090000_add_ledger_entries_merchant_idx.sql" <<'SQL'
-- stale:no-transaction
-- migrate:up
SET lock_timeout = '1500ms';
DROP INDEX CONCURRENTLY IF EXISTS ledger_entries_merchant_idx;
CREATE INDEX CONCURRENTLY ledger_entries_merchant_idx ON ledger_entries(merchant_id);
-- migrate:down
DROP INDEX CONCURRENTLY IF EXISTS ledger_entries_merchant_idx;
SQL
python3 -m stale_engine.reproof.shadow_migration --database-url "$SHADOW" --dir "$tmpd" --applied 20261020090000 --out /tmp/shadow-cic
cic=$?
set -e
check "d1 plain CREATE INDEX fails the shadow (ShareLock seen)" '[ $plain = 1 ] && jq -e ".blocking_spans_ms.ShareLock > 100" /tmp/shadow-plain/shadow-report.json >/dev/null'
check "d2 CONCURRENTLY rewrite passes" '[ $cic = 0 ]'

echo
[ $fail = 0 ] && echo "ALL LOCAL ACCEPTANCE CHECKS PASSED" || { echo "SOME CHECKS FAILED"; exit 1; }

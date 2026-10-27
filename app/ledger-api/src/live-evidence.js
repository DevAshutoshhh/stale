'use strict';
// Live evidence for the /status page: the same public feeds the control loop reads, checked against
// this revision's runtime and the database. Display only — Stale's engine and pipelines decide.

const TTL_MS = 60_000;

function parseVer(v) {
  const m = /^v?(\d+)\.(\d+)\.(\d+)/.exec(v || '');
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}
const cmp = (a, b) => (a[0] - b[0]) || (a[1] - b[1]) || (a[2] - b[2]);

function securityFloor(releases, nodeVersion) {
  const cur = parseVer(nodeVersion);
  if (!cur || !Array.isArray(releases)) return null;
  const sec = releases.filter(r => r.security && parseVer(r.version) && parseVer(r.version)[0] === cur[0]);
  if (!sec.length) return null;
  return sec.map(r => r.version).sort((a, b) => cmp(parseVer(a), parseVer(b))).pop();
}

function anchorEvidence(rr, feeds) {
  const floor = feeds.releases ? securityFloor(feeds.releases, rr.node_version) : null;
  const distrusted = new Set(((feeds.roots && feeds.roots.distrusted) || []).map(d => String(d.spki_sha256).toLowerCase()));
  const hits = rr.effective_ca_spki.filter(s => distrusted.has(s));
  return [
    { label: 'Node release', value: rr.node_version,
      detail: floor ? `security floor for v${parseVer(rr.node_version)[0]}.x is ${floor}` : 'security feed unavailable',
      ok: floor ? cmp(parseVer(rr.node_version), parseVer(floor)) >= 0 : null },
    { label: 'Distrusted roots in effective CA set', value: hits.length ? hits.map(h => rr.effective_ca_subjects[h] || h.slice(0, 12)).join(', ') : 'none',
      detail: `${distrusted.size} distrusted in the root feed; trust policy removed ${rr.trust_policy.removed}`,
      ok: feeds.roots ? hits.length === 0 : null },
  ];
}

// "What changed in the world": the outside facts the control loop reads, and what each means for this revision.
function worldFacts(rr, feeds, rowsEvidence) {
  const out = [];
  const floor = feeds.releases ? securityFloor(feeds.releases, rr.node_version) : null;
  if (feeds.releases) {
    const rel = floor && feeds.releases.find(r => r.version === floor);
    out.push({ source: 'Node.js release feed', fact: floor ? `${floor} is the newest security release for v${parseVer(floor)[0]}.x${rel && rel.date ? ` (${rel.date})` : ''}` : 'no security release for this line',
      effect: !floor ? 'nothing to apply' : cmp(parseVer(rr.node_version), parseVer(floor)) >= 0 ? `serving ${rr.node_version} includes it` : `serving ${rr.node_version} is below it`,
      ok: floor ? cmp(parseVer(rr.node_version), parseVer(floor)) >= 0 : true });
  }
  if (feeds.roots) {
    const d = feeds.roots.distrusted || [];
    const trusted = new Set(rr.effective_ca_spki);
    const live = d.filter(x => trusted.has(String(x.spki_sha256).toLowerCase()));
    out.push({ source: 'Root-program distrust feed', fact: d.length ? `${d.map(x => x.subject).join(', ')} distrusted${feeds.roots.updated_at ? ` (${String(feeds.roots.updated_at).slice(0, 10)})` : ''}` : 'no distrusted roots',
      effect: !d.length ? 'nothing to remove' : live.length ? 'still in this revision\'s trust store' : 'excluded by this revision\'s trust policy',
      ok: live.length === 0 });
  }
  const rows = rowsEvidence.find(e => 'large' in e);
  if (rows) {
    out.push({ source: 'Live database statistics', fact: `ledger_entries ≈ ${rows.value} rows`, effect: rows.large ? 'large: blocking DDL is unsafe' : 'small: blocking DDL is still safe', ok: null });
  }
  return out;
}

async function migrationEvidence(query, threshold) {
  try {
    const rows = await query("SELECT reltuples::bigint AS n FROM pg_class WHERE relname = 'ledger_entries'");
    const idx = await query("SELECT i.indisvalid AS valid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = 'ledger_entries_merchant_idx'");
    const n = rows.rows.length ? Number(rows.rows[0].n) : null;
    const large = n !== null && n >= threshold;
    const index = idx.rows.length ? (idx.rows[0].valid ? 'present, valid' : 'present, INVALID') : 'not built yet';
    return [
      { label: 'ledger_entries rows (estimate)', value: n === null ? 'unknown' : n.toLocaleString('en-US'),
        detail: `blocking DDL is unsafe at ${threshold.toLocaleString('en-US')}+ rows`, ok: null, large },
      { label: 'Merchant index', value: index,
        detail: large ? 'must be built CONCURRENTLY, outside a transaction, with lock_timeout' : 'table still small',
        ok: index === 'present, INVALID' ? false : (index === 'present, valid' ? true : null) },
    ];
  } catch (err) {
    return [{ label: 'Database', value: 'unavailable', detail: err.message, ok: null }];
  }
}

function createLiveEvidence({ baseUrl = process.env.STALE_FEEDS_BASE_URL, fetchImpl = globalThis.fetch,
                              now = () => Date.now() } = {}) {
  let cache = null;
  const get = async name => {
    const r = await fetchImpl(`${baseUrl}/${name}`, { signal: AbortSignal.timeout(4000) });
    if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
    return r.json();
  };
  return async function feeds() {
    if (!baseUrl) return { releases: null, roots: null, error: 'STALE_FEEDS_BASE_URL is not set' };
    if (cache && now() - cache.at < TTL_MS) return cache.value;
    const value = { releases: null, roots: null, error: null };
    try { value.releases = await get('node-releases.json'); } catch (e) { value.error = e.message; }
    try { value.roots = await get('root-distrust.json'); } catch (e) { value.error = e.message; }
    cache = { at: now(), value };
    return value;
  };
}

module.exports = { createLiveEvidence, anchorEvidence, migrationEvidence, securityFloor, worldFacts };

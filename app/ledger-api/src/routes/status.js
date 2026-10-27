'use strict';
const { Router } = require('express');
const { report } = require('../runtime-report.cjs');
const { anchorEvidence, migrationEvidence, worldFacts } = require('../live-evidence');

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const link = (url, text) => (url ? `<a href="${esc(url)}">${esc(text)}</a>` : '');

async function schemaInfo(query) {
  try {
    const v = await query('SELECT max(version) AS v FROM schema_migrations');
    const bad = await query('SELECT indexrelid::regclass::text AS name FROM pg_index WHERE NOT indisvalid ORDER BY 1');
    return { schema_version: v.rows[0].v, invalid_indexes: bad.rows.map(r => r.name), db_error: null };
  } catch (err) {
    return { schema_version: null, invalid_indexes: [], db_error: err.message };
  }
}

async function statusData({ query, claimsStatus, liveFeeds, threshold }) {
  const rr = report();
  const [db, claims, feeds, migration] = await Promise.all([
    schemaInfo(query), claimsStatus(), liveFeeds(), migrationEvidence(query, threshold)]);
  const evidence = { 'anchor-trusted': anchorEvidence(rr, feeds), 'migration-safe': migration };
  return {
    generated_at: new Date().toISOString(),
    revision: rr.revision,
    node_version: rr.node_version,
    commit: process.env.STALE_COMMIT_SHA || null,
    trust_policy: { ...rr.trust_policy, effective_ca_count: rr.effective_ca_count,
                    extra_ca_file: rr.ca_source_flags.extra_ca_file,
                    demo_root_g3_present: Object.values(rr.effective_ca_subjects).some(s => s.includes('Demo Root G3')) },
    ...db,
    claims: claims.claims.map(c => ({ ...c, evidence: evidence[c.id] || [] })),
    claims_source: claims.source,
    claims_error: claims.error,
    feeds_error: feeds.error,
    project_url: claims.project_url || null,
    world: worldFacts(rr, feeds, migration),
    activity: claims.activity || [],
    remediation_mrs: claims.remediation_mrs || [],
    gate: claims.gate || null,
    rollout: claims.rollout || null,
    control_loop: claims.control_loop || null,
    sci: claims.sci,
  };
}

const COLORS = { VALID: 'var(--ok)', STALE: 'var(--bad)', REMEDIATING: 'var(--warn)', REPROVING: 'var(--info)',
                 QUARANTINED: 'var(--q)', 'NOT MINTED': 'var(--muted)' };
const mark = ok => (ok === true ? '<span class="ok" aria-label="pass">✓</span>'
  : ok === false ? '<span class="bad" aria-label="fail">✗</span>' : '<span class="na" aria-label="info">·</span>');

const ASSERTIONS = {
  'anchor-trusted': 'The serving Node is patched for its line, and no distrusted or weak root is in its effective trust store.',
  'migration-safe': 'No pending migration takes a blocking lock on a large table; concurrent index builds are retry-safe.',
};

// issue → Duo fix MR → release → mint, for the open claim issue or the last cycle that minted the claim.
function history(c, d) {
  // A plain re-release re-mints without an issue, so fall back to the claim's latest closed issue.
  const lastClosed = (d.activity || []).find(a => a.claim === c.id && a.state === 'closed');
  const iid = c.open_issue_iid || c.issue_iid || (lastClosed && lastClosed.iid);
  if (!iid) return '';
  const act = (d.activity || []).find(a => a.iid === iid);
  const open = Boolean(c.open_issue_iid);
  const stage = open ? (act && act.stage) || 'stale' : null;
  const related = (d.remediation_mrs || []).filter(x => x.related === iid && (open || x.state === 'merged'));
  const mrUrls = !open && c.issue_iid === iid && c.mr_urls.length ? c.mr_urls : related.map(m => m.web_url);
  const mr = mrUrls.map(u => link(u, `!${u.split('/').pop()}`)).join(' ');
  const cur = !open ? 4 : stage === 'reproving' ? 2 : 1;
  const release = open ? (cur === 2 ? 'in CI' : '—')
    : c.issue_iid === iid && c.pipeline_url ? link(c.pipeline_url, 'canary → 100%') : 'shipped';
  const steps = [
    ['Issue', link(c.open_issue_url || c.issue_url || (act && act.web_url), `#${iid}`), act ? hhmm(act.created_at) : ''],
    ['Duo fix', mr || (cur > 1 ? 'merged' : 'waiting'), open ? '' : 'auto-merged by CI'],
    ['Release', release, ''],
    ['Minted', !open && c.minted_at ? esc(hhmm(c.minted_at)) : '—', ''],
  ];
  const cls = i => (i < cur ? 'done' : i === cur ? (stage === 'quarantined' ? 'fail' : 'now') : 'todo');
  return `
      <ol class="hist" aria-label="${open ? 'current' : 'last'} remediation">${steps.map(([t, v, s], i) => `<li class="${cls(i)}"><b>${t}</b><span>${v}</span>${s ? `<small>${esc(s)}</small>` : ''}</li>`).join('')}</ol>`;
}

function card(c, d) {
  const rows = c.evidence.map(e => `
        <li>${mark(e.ok)}<div><b>${esc(e.label)}</b>: <code>${esc(e.value)}</code><small>${esc(e.detail)}</small></div></li>`).join('');
  const links = [link(c.open_issue_url, 'open issue'), link(c.issue_url, 'last claim issue'),
    ...c.mr_urls.map((u, i) => link(u, c.mr_urls.length > 1 ? `fix MR ${i + 1}` : 'fix MR')),
    link(c.pipeline_url, 'release that minted it')].filter(Boolean).join(' · ');
  return `
    <section class="card">
      <header><h3>${esc(c.id)}</h3><span class="badge" style="background:${COLORS[c.state] || 'var(--muted)'}">${esc(c.state)}</span></header>
      <p class="assert">${esc(ASSERTIONS[c.id] || '')}</p>${history(c, d)}
      <ul class="ev">${rows || '<li><span class="na">·</span><div>No live evidence.</div></li>'}</ul>
      <dl>
        <dt>Last minted</dt><dd>${esc(c.minted_at ? c.minted_at.replace('T', ' ').replace('Z', ' UTC') : 'never')} on <code>${esc(c.revision || '-')}</code></dd>
        <dt>Fingerprint</dt><dd><code>${esc((c.fingerprint || '-').slice(0, 23))}</code></dd>
      </dl>
      <p class="links">${links || ''}</p>
    </section>`;
}

function ago(iso, nowMs) {
  if (!iso) return '';
  const m = Math.max(0, Math.round((nowMs - Date.parse(iso)) / 60000));
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 48 * 60 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
}
const hhmm = iso => (iso ? iso.slice(5, 16).replace('T', ' ') + ' UTC' : '');
function duration(a, b) {
  const m = Math.round((Date.parse(b) - Date.parse(a)) / 60000);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

function activity(d) {
  const nowMs = Date.parse(d.generated_at);
  const cl = d.control_loop;
  const loop = cl ? `<p class="loopline"><span class="dot" style="background:${cl.status === 'success' ? 'var(--ok)' : cl.status === 'failed' ? 'var(--bad)' : 'var(--info)'}"></span>
   Control loop last ran <b>${esc(ago(cl.created_at, nowMs))}</b> — ${link(cl.web_url, cl.status)}. It runs every 15 minutes on a schedule; no commit or human is needed to start it.</p>` : '';
  const rows = (d.activity || []).map(a => `
    <tr><td>${link(a.web_url, '#' + a.iid)}</td><td><code>${esc(a.claim)}</code></td><td>${esc(hhmm(a.created_at))}</td>
    <td>${a.state === 'closed' ? `closed by <code>mint</code> ${esc(hhmm(a.closed_at))}` : `<span class="badge" style="background:var(--warn)">${esc((a.stage || 'open').toUpperCase())}</span>`}</td>
    <td>${a.state === 'closed' ? esc(duration(a.created_at, a.closed_at)) : 'in progress'}</td></tr>`).join('');
  return `
<h2>Live loop activity</h2>
<section class="card">${loop}
${rows ? `<div class="tw"><table><thead><tr><th>Claim issue</th><th>Claim</th><th>Opened by the control loop</th><th>Outcome</th><th>Time to fix</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p>No claim issues yet.</p>'}
</section>`;
}

function world(d) {
  if (!d.world || !d.world.length) return '';
  const rows = d.world.map(w => `
    <tr><td>${esc(w.source)}</td><td>${esc(w.fact)}</td><td>${mark(w.ok)} ${esc(w.effect)}</td></tr>`).join('');
  return `
<h2>What changed in the world</h2>
<section class="card"><p class="assert">Facts outside the repository, read live. None of them is a commit.</p>
<div class="tw"><table><thead><tr><th>Source</th><th>Fact</th><th>For this revision</th></tr></thead><tbody>${rows}</tbody></table></div>
</section>`;
}

const RING = [['deploy-canary', 'Canary 0%', 'own tag URL, no traffic'], ['canary-reprove', 'Probes', 'claims re-proved on the canary'],
  ['canary-ring', '50% → 100%', 'k6 SLO + 5xx per ring'], ['promote', 'Promote', 'stable tag moves'], ['mint', 'Mint', 'proof committed, issue closed']];
const JOB_CLASS = { success: 'done', failed: 'fail', running: 'now', pending: 'now' };
const GATE_COLORS = { release: 'var(--ok)', defer: 'var(--warn)', block: 'var(--bad)', 'no-op': 'var(--muted)', unknown: 'var(--muted)' };

function rollout(d) {
  const r = d.rollout, g = d.gate;
  if (!r && !g) return '';
  const gate = g ? `<p class="loopline">Last release gate: <span class="badge" style="background:${GATE_COLORS[g.decision]}">${esc(g.decision.toUpperCase())}</span> ${esc(hhmm(g.created_at))} · ${link(g.web_url, 'child pipeline')}</p><p class="assert">Release when every claim is proven; defer while another claim's fix is in flight; block if a claim is stale with no fix.</p>` : '';
  if (!r) return `<h2>Rollout</h2><section class="card">${gate}</section>`;
  const st = n => (r.jobs[n] || {}).status || 'none';
  const back = ['success', 'running'].includes(st('rollback'));
  let pct = st('promote') === 'success' || st('canary-ring') === 'success' ? 100 : st('canary-ring') === 'running' ? 50 : 0;
  if (back) pct = 0;
  const steps = RING.map(([n, t, s]) => `<li class="${JOB_CLASS[st(n)] || 'todo'}"><b>${(r.jobs[n] || {}).web_url ? link(r.jobs[n].web_url, t) : esc(t)}</b><small>${esc(s)}</small></li>`).join('');
  return `
<h2>Rollout</h2>
<section class="card">${gate}
 <p class="loopline">Latest release: ${link(r.web_url, `pipeline ${r.id}`)}, ${esc(r.status)}, started ${esc(hhmm(r.created_at))}.</p>
 <ol class="hist ring">${steps}</ol>
 <div class="bar" role="img" aria-label="traffic: stable ${100 - pct}%, canary ${pct}%">
  <span class="stable" style="width:${100 - pct}%">${pct < 100 ? `stable ${100 - pct}%` : ''}</span><span class="canary" style="width:${pct}%">${pct > 0 ? `${st('promote') === 'success' ? 'new stable' : 'canary'} ${pct}%` : ''}</span>
 </div>
 ${back ? `<p class="err">A check failed: ${link(r.jobs.rollback.web_url, 'rollback')} restored 100% of traffic to the stable revision.</p>` : ''}
</section>`;
}

function html(d) {
  const tp = d.trust_policy;
  const proj = d.project_url;
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Stale status</title>
<style>
 :root{color-scheme:light dark;--bg:#f6f7f9;--fg:#1f2328;--muted:#59636e;--card:#fff;--line:#d1d9e0;--ok:#1a7f37;--bad:#cf222e;--warn:#9a6700;--info:#0969da;--q:#8250df;--code:#eff1f3}
 @media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--card:#161b22;--line:#30363d;--ok:#3fb950;--bad:#f85149;--warn:#d29922;--info:#58a6ff;--q:#a371f7;--code:#262c36}}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
 main{max-width:960px;margin:0 auto;padding:28px 16px 40px}
 h1{font-size:26px;margin:0} h2{font-size:17px;margin:28px 0 10px} h3{font-size:16px;margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
 .lede{color:var(--muted);margin:6px 0 0;max-width:70ch}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
 .card header{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px}
 .badge{color:#fff;font-size:12px;font-weight:600;letter-spacing:.02em;padding:3px 10px;border-radius:999px}
 .assert{color:var(--muted);margin:0 0 10px;font-size:14px}
 ul.ev{list-style:none;padding:0;margin:0 0 12px} ul.ev li{display:flex;gap:10px;padding:6px 0;border-top:1px solid var(--line)}
 ul.ev small{display:block;color:var(--muted);font-size:12.5px}
 .ok,.bad,.na{font-weight:700;width:1.1em;flex:none;text-align:center} .ok{color:var(--ok)} .bad{color:var(--bad)} .na{color:var(--muted)}
 dl{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;margin:0;font-size:14px} dt{color:var(--muted)} dd{margin:0;overflow-wrap:anywhere}
 code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--code);padding:1px 5px;border-radius:4px;overflow-wrap:anywhere}
 .links{margin:10px 0 0;font-size:14px} a{color:var(--info)}
 ol.loop{margin:0;padding-left:20px} ol.loop li{margin:4px 0}
 .loopline{margin:0 0 10px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap} .dot{width:9px;height:9px;border-radius:50%;display:inline-block;flex:none;transform:translateY(-1px)}
 .tw{overflow-x:auto} table{border-collapse:collapse;width:100%;font-size:14px} th,td{text-align:left;padding:7px 10px 7px 0;border-top:1px solid var(--line);white-space:nowrap} th{color:var(--muted);font-weight:600;border-top:0}
 ol.hist{list-style:none;display:flex;padding:0;margin:4px 0 12px;font-size:12.5px;counter-reset:s}
 ol.hist li{flex:1;position:relative;padding:18px 4px 0 0;min-width:0} ol.hist li b{display:block;font-size:12px}
 ol.hist li span,ol.hist li small{display:block;color:var(--muted)}
 ol.hist li::before{content:"";position:absolute;top:4px;left:0;width:10px;height:10px;border-radius:50%;background:var(--line);z-index:1}
 ol.hist li::after{content:"";position:absolute;top:8px;left:12px;right:4px;height:2px;background:var(--line)} ol.hist li:last-child::after{display:none}
 ol.hist li.done::before,ol.hist li.done::after{background:var(--ok)} ol.hist li.fail::before{background:var(--bad)}
 ol.hist li.now::before{background:var(--info);animation:pulse 1.2s ease-in-out infinite}
 ol.ring{font-size:13px} ol.ring li b{font-size:13px}
 @keyframes pulse{0%,100%{box-shadow:0 0 0 0 color-mix(in srgb,var(--info) 55%,transparent)}50%{box-shadow:0 0 0 6px transparent}}
 .bar{display:flex;height:26px;border-radius:6px;overflow:hidden;border:1px solid var(--line);font-size:12px;font-weight:600;color:#fff}
 .bar span{display:flex;align-items:center;justify-content:center;white-space:nowrap;transition:width .8s ease}
 .bar .stable{background:var(--muted)} .bar .canary{background:var(--ok)}
 td{white-space:normal} td:first-child{white-space:nowrap}
 .err{color:var(--bad)} footer{margin-top:28px;color:var(--muted);font-size:13px}
</style></head>
<body><main>
<h1>Stale</h1>
<p class="lede">Every “safe to ship” verdict is a cached value with evidence behind it. When the world changes (a root is distrusted, a security release ships, a table grows), Stale re-proves it and fixes it, with no human in the loop.</p>

<h2>Claims</h2>
${d.claims_error ? `<p class="err">${esc(d.claims_error)}</p>` : ''}
<div class="grid">${d.claims.map(c => card(c, d)).join('') || '<p>No claim data.</p>'}</div>
<p class="lede" style="font-size:13px">States come from the claim issues and the minted lockfile on <code>main</code>. The checks above are recomputed by this page from the same public feeds and the database for display; Stale's control loop and pipelines make the decisions.${d.feeds_error ? ` <span class="err">Feeds: ${esc(d.feeds_error)}</span>` : ''}</p>

${world(d)}
${rollout(d)}
${activity(d)}

<h2>Serving revision</h2>
<div class="grid">
 <section class="card"><dl>
  <dt>Revision</dt><dd><code>${esc(d.revision)}</code></dd>
  <dt>Commit</dt><dd><code>${esc((d.commit || '-').slice(0, 8))}</code></dd>
  <dt>Node</dt><dd><code>${esc(d.node_version)}</code></dd>
  <dt>Schema</dt><dd><code>${esc(d.schema_version || 'unknown')}</code>${d.invalid_indexes.length ? ` <span class="err">INVALID: ${esc(d.invalid_indexes.join(', '))}</span>` : ''}</dd>
 </dl></section>
 <section class="card"><dl>
  <dt>Trust policy</dt><dd>${tp.loaded ? 'loaded' : '<span class="err">not loaded</span>'}</dd>
  <dt>Excluded SPKIs</dt><dd>${esc(tp.excluded_spki_count)}</dd>
  <dt>Roots removed</dt><dd>${esc(tp.removed)} of ${esc((tp.effective_ca_count || 0) + (tp.removed || 0))}</dd>
  <dt>Demo Root G3</dt><dd>${tp.demo_root_g3_present ? 'trusted' : 'not trusted'}</dd>
 </dl></section>
</div>

<h2>How the loop works</h2>
<section class="card"><ol class="loop">
 <li><b>The world changes, with no commit</b>: a feed, live database statistics, or the serving revision.</li>
 <li><b>The scheduled control loop</b> collects evidence from this revision and the database; deterministic rules open a claim issue.</li>
 <li><b>A GitLab Duo flow</b> is assigned and opens one merge request, editing only the claim's allowed paths.</li>
 <li><b>CI proves the fix</b>: policy check, security scans, re-proof inside the built image, shadow migration under write load, then auto-merge.</li>
 <li><b>The release</b> deploys a 0% canary with its own URL, probes it, shifts traffic 50% → 100%, re-mints the claim and closes the issue. Any failure restores the stable revision.</li>
</ol></section>

<footer>
 ${proj ? `${link(proj, 'Source')} · ${link(proj + '/-/pipelines', 'Pipelines')} · ${link(proj + '/-/merge_requests?state=all&label_name[]=stale-remediation', 'Remediation MRs')} · ${link(proj + '/-/pipeline_schedules', 'Schedules')} · ` : ''}
 ${link('/status.json', 'status.json')} · generated ${esc(d.generated_at.replace('T', ' ').slice(0, 19))} UTC, refreshes every 30 s
 ${d.sci ? ` · re-proofs skipped: ${esc(d.sci.reproofs_skipped)}` : ''}
</footer>
</main></body></html>`;
}

module.exports = deps => Router()
  .get('/status.json', async (req, res, next) => {
    try { res.set('Cache-Control', 'no-store').json(await statusData(deps)); } catch (e) { next(e); }
  })
  .get('/status', async (req, res, next) => {
    try { res.set('Cache-Control', 'no-store').type('html').send(html(await statusData(deps))); } catch (e) { next(e); }
  });

module.exports.html = html;

'use strict';
// Claim states for /status: minted proofs from the lockfile on `main` (GitLab API raw file,
// STALE_LOCKFILE_URL) plus open claim issues. Cached for 30 s (SRS §13.2, v1 defect #19).

const TTL_MS = 30_000;
const STATE_ORDER = [['quarantined', 'QUARANTINED'], ['reproving', 'REPROVING'],
  ['remediating', 'REMEDIATING'], ['retry-pending', 'REMEDIATING'], ['stale', 'STALE']];

function apiParts(lockfileUrl) {
  const m = /^(https?:\/\/[^/]+\/api\/v4)\/projects\/([^/]+)\//.exec(lockfileUrl || '');
  return m ? { api: m[1], project: m[2] } : null;
}

function deriveState(issue, minted) {
  if (issue) {
    const values = new Set(issue.labels.filter(l => l.startsWith('stale-state::')).map(l => l.slice(13)));
    for (const [v, s] of STATE_ORDER) if (values.has(v)) return s;
    return 'STALE';
  }
  return minted ? 'VALID' : 'NOT MINTED';
}

function createClaimsStatus({ lockfileUrl = process.env.STALE_LOCKFILE_URL, fetchImpl = globalThis.fetch,
                              now = () => Date.now() } = {}) {
  let cache = null;
  const get = async url => {
    const r = await fetchImpl(url, { signal: AbortSignal.timeout(4000) });
    if (!r.ok) throw new Error(`GET ${url.split('?')[0]} -> HTTP ${r.status}`);
    return r.json();
  };

  async function load() {
    const parts = apiParts(lockfileUrl);
    if (!parts) return { source: 'unconfigured', error: 'STALE_LOCKFILE_URL is not set', claims: [], sci: null };
    const out = { source: 'gitlab', error: null, project_url: null, claims: [], sci: null };
    let lock;
    try {
      lock = await get(lockfileUrl);
    } catch (err) {
      return { ...out, source: 'error', error: err.message };
    }
    let issues = [];
    try {
      const project = await get(`${parts.api}/projects/${parts.project}`);
      out.project_url = project.web_url;
      issues = await get(`${parts.api}/projects/${parts.project}/issues?state=opened&per_page=100`);
    } catch (err) {
      out.error = `issues unavailable: ${err.message}`;
    }
    const web = out.project_url;
    for (const [id, e] of Object.entries(lock.claims || {})) {
      const issue = issues.find(i => (i.labels || []).includes(`stale-claim::${id}`));
      out.claims.push({
        id, state: deriveState(issue, Boolean(e.minted_at)), fingerprint: e.fingerprint, minted_at: e.minted_at,
        revision: e.revision,
        open_issue_url: issue ? issue.web_url : null,
        open_issue_iid: issue ? issue.iid : null,
        issue_iid: e.issue_iid || null,
        issue_url: web && e.issue_iid ? `${web}/-/issues/${e.issue_iid}` : null,
        mr_urls: web ? (e.mr_iids || []).map(i => `${web}/-/merge_requests/${i}`) : [],
        pipeline_url: web && e.pipeline_id ? `${web}/-/pipelines/${e.pipeline_id}` : null,
      });
    }
    out.sci = lock.sci || null;
    out.activity = [];
    out.control_loop = null;
    try {
      const base = `${parts.api}/projects/${parts.project}`;
      const recent = await get(`${base}/issues?state=all&per_page=40&order_by=created_at&sort=desc`);
      out.activity = recent
        .filter(i => (i.labels || []).some(l => l.startsWith('stale-claim::')) && !/^\[(policy-check|spike)\]/.test(i.title))
        .slice(0, 6)
        .map(i => ({ iid: i.iid, title: i.title, state: i.state, web_url: i.web_url, created_at: i.created_at,
                     closed_at: i.closed_at,
                     claim: (i.labels.find(l => l.startsWith('stale-claim::')) || '').slice(13),
                     stage: (i.labels.find(l => l.startsWith('stale-state::')) || '').slice(13) || null }));
      const pipes = await get(`${base}/pipelines?per_page=20&source=schedule`);
      const cl = pipes.find(p => p.name === 'control-loop') || null;
      out.control_loop = cl && { status: cl.status, created_at: cl.created_at, web_url: cl.web_url };
    } catch (err) {
      out.error = out.error || `activity unavailable: ${err.message}`;
    }
    out.remediation_mrs = [];
    out.gate = null;
    out.rollout = null;
    try {
      const base = `${parts.api}/projects/${parts.project}`;
      out.remediation_mrs = (await get(`${base}/merge_requests?state=all&labels=stale-remediation&per_page=30`))
        .map(m => ({ iid: m.iid, state: m.state, web_url: m.web_url, related: Number((/Related to #(\d+)/.exec(m.description || '') || [])[1]) || null }));
      // Release children: the newest one is the last gate decision; the newest one that deployed is the rollout.
      const children = await get(`${base}/pipelines?source=parent_pipeline&per_page=6`);
      for (const p of children) {
        const jobs = await get(`${base}/pipelines/${p.id}/jobs?per_page=50`);
        const names = new Set(jobs.map(j => j.name));
        if (!out.gate) {
          out.gate = { decision: names.has('deploy-canary') ? 'release' : names.has('release-deferred') ? 'defer'
            : names.has('release-blocked') ? 'block' : names.has('release-noop') ? 'no-op' : 'unknown',
          created_at: p.created_at, web_url: p.web_url };
        }
        if (names.has('deploy-canary')) {
          out.rollout = { id: p.id, status: p.status, web_url: p.web_url, created_at: p.created_at, updated_at: p.updated_at,
            jobs: Object.fromEntries(jobs.map(j => [j.name, { status: j.status, web_url: j.web_url,
              finished_at: j.finished_at, duration: j.duration }])) };
          break;
        }
      }
    } catch (err) {
      out.error = out.error || `releases unavailable: ${err.message}`;
    }
    return out;
  }

  return async function claimsStatus() {
    if (cache && now() - cache.at < TTL_MS) return cache.value;
    const value = await load();
    cache = { at: now(), value };
    return value;
  };
}

module.exports = { createClaimsStatus, deriveState, apiParts };

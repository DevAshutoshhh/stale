# Stale — Software Requirements Specification & AI Implementation Blueprint

**Project:** Stale — autonomous re-verification of expired safety assumptions
**Hackathon:** GitLab Transcend (Devpost) · Path A (Start Fresh) · Autonomy level: Hands-off
**Targets:** Best Hands-off Agent (Path A) · Most Stages Covered (special prize) · Google Cloud bonus (up to +0.2)
**Version:** 2.0.0 (supersedes 1.0.0) · **Date:** 2026-10-24 · **License:** MIT + GitLab DCO 1.1

---

## 0. How to use this document

This is the single source of truth for the AI coding agents and humans building the `stale` repository.

- **MUST / MUST NOT / SHOULD / MAY** follow RFC 2119 meaning.
- Every path, command, schema, label, variable, and exit code below is normative. Do not rename things.
- Items marked **[VERIFY]** depend on platform behaviour that may differ on the provisioned GitLab instance or GCP project. Before writing code that depends on them, verify them with a tiny spike and record the result in `docs/verification-log.md`.
- When this document and a vendor's live documentation disagree on syntax, follow the vendor docs. Then record the difference in `docs/verification-log.md`.
- Build in the order given in §19. Do not start a milestone until the previous milestone's acceptance tests pass.

---

## 1. What changed from v1.0 and why

v1.0 contained defects that would have produced a system that does not work. Coding agents MUST NOT reintroduce any of them.

| # | v1.0 statement | Problem | v2.0 resolution |
|---|---|---|---|
| 1 | Remediation sets `NODE_OPTIONS=--use-system-ca` to drop distrusted roots | `--use-system-ca` **adds** the OS store **on top of** Node's bundled roots. It removes nothing. Tested on Node 22.23.4: default CA count goes from 116 to 606, and a distrusted extra root stays trusted. | A Node trust-policy module filters the default CA set by SPKI hash at startup using `tls.getCACertificates()` + `tls.setDefaultCACertificates()` (§13.3). This was tested: the distrusted root was rejected with `SELF_SIGNED_CERT_IN_CHAIN`. |
| 2 | Effective store = OS bundle when `--use-system-ca` is set | Wrong: see #1. | The effective store is whatever `tls.getCACertificates('default')` returns inside the running process after the policy applies. It is reported by `/internal/runtime` (§13.2). |
| 3 | Demo Root G3 lives in Node's bundled store | A self-made test CA can never be in Node's compiled-in Mozilla list. | The demo roots are trusted through `NODE_EXTRA_CA_CERTS` (`deploy/trust/extra-roots.pem`). This mirrors how enterprises add private CAs, and is just as hard to notice. |
| 4 | `partner-sim` is a separate Cloud Run service serving TLS on ports 8443/8444 | Cloud Run terminates TLS at Google's front end with Google-managed certificates. It exposes one port and cannot serve your own certificate chain. | `partner-sim` runs as a **sidecar container** in the same Cloud Run service. It serves TLS on `localhost:8443` and `localhost:8444` with the demo chains (§13.4). |
| 5 | Feeds live in `demo/feeds/*.json` and `world_shift.py` edits them | Editing repo files is a commit, which breaks the "zero-commit world shift" claim. | Feeds live in a public GCS bucket. `world_shift.py` writes to the bucket, never to the repo (§17). |
| 6 | Lockfile holds live claim status; `stale check` updates it | Scheduled pipelines would have to commit to `main` every run, which causes races and pipeline loops. | The lockfile stores only **minted proofs**. Live state = fresh rule evaluation + scoped labels on the claim's issue (§6). |
| 7 | `stale gate` reads the lockfile, with a vague exemption for remediation releases | It deadlocks the fix it is meant to ship, and it can pass while the world is stale. | The gate re-evaluates rules against the **candidate** (built image, pending migrations, lockfile). It returns pass, defer, or block (§7.3). |
| 8 | `$CI_JOB_JWT_V2` | Removed from GitLab (17.0). | Use `id_tokens:` only. |
| 9 | Migrations are node-pg-migrate `.js` files parsed with `pglast` | `pglast` parses SQL, not JavaScript. Separately, node-pg-migrate's transaction handling for concurrent index builds is tool-specific. | Plain SQL migrations run by `stale migrate` (§10), which splits statements and controls transactions explicitly. |
| 10 | Rewrite uses `CREATE INDEX CONCURRENTLY IF NOT EXISTS` | If an earlier concurrent build failed, it left an INVALID index behind. `IF NOT EXISTS` then silently skips, and the broken index stays forever. | Playbook: `DROP INDEX CONCURRENTLY IF EXISTS` then `CREATE INDEX CONCURRENTLY` (no `IF NOT EXISTS`). The runner verifies `indisvalid` afterwards. |
| 11 | `ADD COLUMN ... DEFAULT` rewrites the table | That has not been true since PG 11 for non-volatile defaults. | M3 flags only volatile defaults and type changes that rewrite the table (§9.2). |
| 12 | At 10%/50% rings, k6 asserts the distrusted partner returns 502 on the live URL | 90%/50% of requests hit the old revision, which still trusts the root. The probe would fail by design. | Claim probes run only against the canary **tag URL**. Live-URL ring checks cover only SLOs, session isolation, and per-revision metrics (§12). |
| 13 | `world-watch` exits 10 when stale | That turns the scheduled pipeline red, which triggers the canary-analyst flow on a pipeline that isn't a release. | Control-loop jobs exit 0 and report through issues. Only real errors fail them. The analyst flow ignores pipelines other than releases (§11.2). |
| 14 | MR title contains `[Closes #iid]` | Merging the MR would auto-close the issue **before** the canary proves the fix. | MRs say `Related to #iid`. Only `stale mint` closes the issue. |
| 15 | Auto-merge job checks "all pipeline jobs green" from inside the same pipeline | A job cannot observe the completion of its own pipeline. | `verify-mr` runs early, then sets GitLab **auto-merge** (merge when the pipeline succeeds) (§15.4). |
| 16 | SCI example: 42 min → 18.48 g | The arithmetic is inconsistent and the figures are invented. | Avoided minutes are measured from real job durations, and every constant is stated (§18). Seed files contain zeros. |
| 17 | Analyst flow reads `retry_count_24h` from the lockfile and pushes commits | Retry counting is a safety decision, so it must not be made by an LLM. | Retry budget is counted deterministically by CI from issue labels. The analyst only diagnoses (§11.2, §6). |
| 18 | `docker run <image>` inside CI | Shared runners need docker-in-docker, and Artifact Registry is private. | Images are pushed to **both** the GitLab Container Registry and Artifact Registry. Inspection jobs use the image directly as the job `image:` (§15.3). |
| 19 | `/status` reads `.stale/claims.lock.json` baked into the image | The lockfile is minted *after* deploy, so the image always carries a stale copy. | `/status` fetches the lockfile from the GitLab API at runtime, with a 30 s cache (§13.2). |
| 20 | Releases deploy on every merge to `main` | That makes Scenario B (a migration waiting on the release train) impossible, because the migration would already have been applied. | Two lanes: a **release train** (scheduled or manual) and a **hotfix lane** (merged `stale-remediation` MRs only) (§15). |
| 21 | Node versions `v24.10.0` → `v24.12.1` | These don't match reality. The real October 2026 security release is v24.22.1 / v22.23.4 / v26.12.1. | The demo baseline is the 24.x release immediately before v24.22.1 (§17.1). |
| 22 | Inconsistent variable names (`DUO_FLOW_USER_ID`, `DUO_FLOW_SERVICE_ACCOUNT_ID`, `DUO_FLOW_BOT_USERNAME`) | Agents would invent three different variables. | One registry of variables (§4). |
| 23 | Duo flow YAML not specified | The agents would guess the schema. | Full flow drafts are given in the Flow Registry v1 syntax (§11). |
| 24 | Session-isolation probe presented as proving the Railway class | Cloud Run has no shared cache in the path, so it can only check the app's own half. | Stated honestly in §12.3. |

---

## 2. Core invariants (non-negotiable)

1. **Epistemic separation.**
   - Deterministic code (`engine/`, CI jobs) decides **whether** a claim is stale, whether an MR may merge, whether a canary passed, and how many retries remain.
   - GitLab Duo flows decide only **how** to fix a stale claim, and only inside that claim's `allowPaths`.
   - Flows never edit `.stale/**`, never merge, never deploy, and never change claim state labels.
2. **Zero-commit triggering.** A claim can go stale because the outside world changed: a feed, live database statistics, a registry, or a serving revision. No repository commit is involved.
3. **Secretless cloud auth.** CI reaches GCP only through GitLab `id_tokens` + Workload Identity Federation. The repository and CI variables contain no service-account JSON keys.
4. **No exploits.** Exposure is shown through version evidence, trust-store evidence, and TLS handshake outcomes. Never write exploit code for any CVE.
5. **Production is never left half-changed.** Any failure after a canary takes traffic sends 100% back to the recorded stable revision.

---

## 3. Glossary

| Term | Meaning |
|---|---|
| Claim | A versioned safety assertion in `.stale/claims/<id>.yaml` |
| Evidence | Normalised facts gathered by collectors for one claim and one target |
| Target | `serving` (what production runs now) or `candidate` (what a pipeline is about to ship) |
| Fingerprint | `sha256:` hash of the canonical (RFC 8785) evidence JSON |
| Finding | A rule that fired, with rule id, severity, and details |
| Mint | Write a proof record to the lockfile after a successful release |
| Lane | Pipeline kind: `control`, `mr`, `main`, `train`, `hotfix` |

---

## 4. Configuration registry

Every variable used anywhere MUST appear in this table. Names are exact.

### 4.1 GitLab CI/CD variables (project settings)

| Variable | Type | Protected | Masked | Purpose |
|---|---|---|---|---|
| `STALE_BOT_TOKEN` | var | yes | yes | Project access token (role Maintainer; scopes `api`, `write_repository`). Used for issues, labels, auto-merge, and the mint commit. Its bot user MUST be allowed to push to protected `main`. |
| `STALE_REMEDIATOR_USER_ID` | var | no | no | Numeric user id of the `stale-remediator` flow service account |
| `STALE_ANALYST_USER_ID` | var | no | no | Numeric user id of the `stale-canary-analyst` flow service account |
| `STALE_REMEDIATOR_USERNAME` | var | no | no | Username of the remediator service account (MR author check) |
| `STALE_HUMAN_ESCALATION_USER_ID` | var | no | no | Human to assign on quarantine |
| `GCP_PROJECT_ID` / `GCP_PROJECT_NUMBER` | var | no | no | GCP project |
| `GCP_REGION` | var | no | no | e.g. `us-central1` |
| `WIF_POOL` / `WIF_PROVIDER` | var | no | no | Workload Identity pool and provider ids |
| `GCP_DEPLOYER_SA` | var | no | no | Service account email to impersonate (e.g. `stale-deployer@…`) |
| `AR_REPO` | var | no | no | Artifact Registry repo name, default `stale` |
| `CLOUDSQL_INSTANCE` | var | no | no | `project:region:instance` |
| `DB_NAME` / `DB_USER` | var | no | no | `ledger` / `stale_migrator` |
| `DB_PASSWORD` | var | yes | yes | Postgres password for `stale_migrator` (a database credential, not a GCP key) |
| `STALE_FEEDS_BASE_URL` | var | no | no | `https://storage.googleapis.com/<bucket>/feeds` |
| `STALE_ROW_THRESHOLD` | var | no | no | Default `500000` (demo) |
| `STALE_GRID_INTENSITY_GCO2_KWH` | var | no | no | Default `440` |
| `STALE_PIPELINE` | set per schedule | — | — | `control-loop` or `release-train` |

### 4.2 Runtime environment (Cloud Run `ledger-api` container)

| Variable | Example | Purpose |
|---|---|---|
| `PORT` | `8080` | Set by Cloud Run |
| `K_REVISION` | set by Cloud Run | Revision name |
| `DATABASE_URL` | Unix socket via the Cloud SQL connector | Postgres connection |
| `NODE_EXTRA_CA_CERTS` | `/app/deploy/trust/extra-roots.pem` | Private/demo roots |
| `STALE_EXCLUDED_SPKI_FILE` | `/app/deploy/trust/excluded-spki.txt` | Read by the trust policy |
| `PARTNER_TRUSTED_URL` | `https://localhost:8443/hook` | Sidecar endpoint (trusted chain) |
| `PARTNER_DISTRUSTED_URL` | `https://localhost:8444/hook` | Sidecar endpoint (Demo Root G3 chain) |
| `STALE_LOCKFILE_URL` | GitLab API raw-file URL for `.stale/claims.lock.json` on `main` | `/status` page |
| `DEMO_TOKENS` | `alpha:tok-alpha,beta:tok-beta` | Static bearer tokens (demo only) |

### 4.3 GitLab labels (create in `infra/gitlab-labels.sh`)

Scoped labels (`a::b`) require GitLab Premium/Ultimate. Hackathon provisioning is expected to be Ultimate **[VERIFY]**. If scoped labels are unavailable, use the same strings and enforce exclusivity in code.

| Label | Meaning |
|---|---|
| `stale-claim::<id>` | Issue/MR belongs to claim `<id>` |
| `stale-state::stale` / `::remediating` / `::reproving` / `::retry-pending` / `::quarantined` | Live state (exactly one per open claim issue) |
| `stale-attempt::1` / `::2` | Remediation attempts in the current 24 h window |
| `stale-remediation` | MR authored by the remediator flow for a claim |
| `stale-retry` | MR is a retry after a failed canary |

---

## 5. Repository layout

```text
stale/
├── LICENSE                                # MIT (must show in the project's About panel)
├── README.md
├── docs/
│   ├── architecture.md                    # 3-plane diagram, lanes, state model
│   ├── verification-log.md                # results of every [VERIFY] spike
│   └── real-vs-simulated.md
├── .gitlab-ci.yml                         # workflow:rules router + includes
├── ci/
│   ├── common.yml                         # .gcp_auth, .engine, .gitlab_api templates
│   ├── control-loop.yml                   # scheduled: collect/check/open issues/dispatch
│   ├── mr.yml                             # tests, scans, reprove, verify-mr, auto-merge
│   ├── main.yml                           # build, scans, plan-release (dynamic child)
│   ├── release.yml                        # child pipeline: migrate, canary, rings, mint
│   └── release-train.yml                  # scheduled train: build + plan-release
├── .stale/
│   ├── claims/
│   │   ├── anchor-trusted.yaml
│   │   ├── migration-safe.yaml
│   │   ├── dependency-trusted.yaml
│   │   └── contract-compatible.yaml       # enabled: false (stretch)
│   ├── claims.lock.json                   # minted proofs only; written only by `stale mint`
│   └── playbooks/
│       ├── anchor-trusted.md
│       ├── migration-safe.md
│       └── dependency-trusted.md
├── duo/flows/
│   ├── stale-remediator.yaml              # source of truth; registered in the AI Catalog
│   └── stale-canary-analyst.yaml
├── engine/
│   ├── pyproject.toml                     # console script: stale = stale_engine.cli:app
│   ├── stale_engine/
│   │   ├── cli.py                         # typer app
│   │   ├── config.py                      # env var registry loader (fails fast on missing)
│   │   ├── models.py                      # pydantic v2 models (§6)
│   │   ├── fingerprint.py                 # RFC 8785 + sha256
│   │   ├── state.py                       # derived state machine (§6.4)
│   │   ├── gitlab_api.py                  # thin REST client (httpx), retries + pagination
│   │   ├── gcp.py                         # Cloud Run / Monitoring helpers (google-cloud libs)
│   │   ├── issue_format.py                # issue body render/parse (§6.5)
│   │   ├── sci.py
│   │   ├── migrate/                       # SQL migration runner (§10)
│   │   │   ├── runner.py
│   │   │   └── lockmodes.py               # statement → lock mode table
│   │   ├── collectors/
│   │   │   ├── runtime_report.py          # /internal/runtime of serving or canary revision
│   │   │   ├── image_inspect.py           # same report, from inside a candidate image
│   │   │   ├── node_feed.py
│   │   │   ├── root_feed.py
│   │   │   ├── pg_stats.py
│   │   │   ├── pending_migrations.py
│   │   │   └── npm_registry.py
│   │   ├── rules/
│   │   │   ├── anchor.py                  # R1–R4
│   │   │   ├── migration.py               # M1–M5
│   │   │   └── dependency.py              # D1–D4
│   │   ├── weakkeys.py                    # small-scalar EC table + RSA size check
│   │   └── reproof/
│   │       ├── shadow_migration.py
│   │       └── canary.py                  # tag-URL probes
│   └── tests/                             # pytest; golden evidence fixtures
├── app/
│   ├── ledger-api/
│   │   ├── Dockerfile
│   │   ├── package.json / package-lock.json
│   │   ├── deploy/trust/
│   │   │   ├── extra-roots.pem            # Demo Trusted Root + Demo Root G3 (baseline)
│   │   │   └── excluded-spki.txt          # sha256 SPKI hex, one per line (baseline: empty)
│   │   ├── db/migrations/
│   │   │   ├── 20261020090000_init_ledger.sql
│   │   │   └── 20261021090000_add_ledger_entries_merchant_idx.sql   # pending
│   │   └── src/
│   │       ├── trust-policy.cjs           # loaded first via --require (§13.3)
│   │       ├── index.js
│   │       ├── db.js
│   │       └── routes/{balance,webhooks,runtime,status,health}.js
│   └── partner-sim/                       # sidecar image
│       ├── Dockerfile
│       └── server.js
├── loadtest/
│   ├── ring_slo.js                        # k6: SLO + session isolation (live URL)
│   └── README.md
├── infra/
│   ├── main.tf  variables.tf  outputs.tf  # Cloud Run, Cloud SQL, AR, GCS, WIF, IAM
│   ├── service.ledger-api.yaml            # Knative service spec template (multi-container)
│   └── gitlab-labels.sh
└── demo/
    ├── generate_demo_pki.py               # Demo Trusted Root, Demo Root G3 (k=4), leaves
    ├── feeds/baseline/                    # seed copies only; live copies are in GCS
    │   ├── node-releases.json
    │   ├── node-schedule.json
    │   └── root-distrust.json
    ├── world_shift.py                     # scenario-a | scenario-b | reset (GCS + DB only)
    └── verdaccio/                         # optional dependency-trusted demo
```

---

## 6. Data contracts

### 6.1 Claim file (`.stale/claims/<id>.yaml`)

`engine/stale_engine/models.py` MUST validate these files with pydantic. Unknown keys are an error.

```yaml
apiVersion: stale/v2
kind: Claim
metadata:
  id: anchor-trusted              # ^[a-z0-9-]+$ ; equals file stem
  owner: platform-security
  enabled: true
spec:
  assertion: >-
    Every revision serving traffic runs a Node release that is patched for its
    major line, and its effective default CA set contains no distrusted or weak anchor.
  targets: [serving, candidate]
  evidence:                        # ordered; each collector returns a JSON object
    - collector: runtime_report    # serving: tag URL `stable`; candidate: image_inspect
    - collector: node_feed
      config: { releases: "${STALE_FEEDS_BASE_URL}/node-releases.json",
                schedule: "${STALE_FEEDS_BASE_URL}/node-schedule.json" }
    - collector: root_feed
      config: { url: "${STALE_FEEDS_BASE_URL}/root-distrust.json" }
  fingerprint:
    include: [runtime_report.node_version, runtime_report.effective_ca_spki,
              node_feed.line_security_floor, node_feed.line_eol, root_feed.distrusted_spki]
  rules: [R1, R2, R3, R4]
  remediation:
    flow: stale-remediator
    playbook: .stale/playbooks/anchor-trusted.md
    allowPaths:
      - app/ledger-api/Dockerfile
      - app/ledger-api/deploy/trust/**
    maxAttemptsPer24h: 2
  reproof:
    preDeploy: [image_runtime_report]         # rules re-run on candidate evidence
    canary: [partner_trusted_ok, partner_distrusted_rejected, runtime_report_matches]
  rollout:
    soakSeconds: 120                          # per ring; x3 when R2 fired
```

The other claim files follow the same shape. Their exact contents are in §9.

### 6.2 Evidence file (`.stale/out/evidence-<target>.json`, CI artifact, never committed)

```json
{
  "schema": "stale.evidence/v2",
  "target": "serving",
  "collected_at": "2026-10-24T10:00:00Z",
  "claims": {
    "anchor-trusted": {
      "evidence": { "runtime_report": { }, "node_feed": { }, "root_feed": { } },
      "fingerprint": "sha256:…",
      "findings": [ { "rule": "R3", "severity": "high", "detail": { } } ],
      "errors": []
    }
  }
}
```

### 6.3 Fingerprint algorithm (`fingerprint.py`)

1. Select only the dotted paths in `spec.fingerprint.include`. Volatile fields are never included.
2. Normalise:
   - sets become sorted lists;
   - SPKI hashes become lowercase hex;
   - row counts become `bucket = floor(log10(max(reltuples, 1)) * 4) / 4`, so that natural growth does not invalidate caches on every run.
3. Serialise with RFC 8785 using the `rfc8785` PyPI package.
4. `sha256:` + hex digest.

Fingerprints decide only whether expensive re-proofs can be skipped (§8 `reprove`). They **never** decide staleness on their own; rules do.

### 6.4 Lockfile (`.stale/claims.lock.json`) — minted proofs only

Only `stale mint` writes it, in the release pipeline, after 100% promotion. Seed contents:

```json
{
  "schema": "stale.lock/v2",
  "claims": {
    "anchor-trusted":   { "fingerprint": "sha256:<from first real mint>", "minted_at": null,
                          "pipeline_id": null, "job_id": null, "revision": null,
                          "commit_sha": null, "issue_iid": null, "mr_iids": [] },
    "migration-safe":   { "...": "same shape" },
    "dependency-trusted": { "...": "same shape" }
  },
  "sci": { "reproofs_skipped": 0, "runner_seconds_avoided": 0, "gco2eq_avoided": 0.0 }
}
```

**Live state is derived, not stored.** `state.py` computes:

| Condition (evaluated in order) | State |
|---|---|
| Claim issue open with `stale-state::quarantined` | QUARANTINED |
| Claim issue open with `::reproving` | REPROVING |
| Claim issue open with `::remediating` or `::retry-pending` | REMEDIATING |
| Claim issue open with `::stale` | STALE |
| No open issue, and rules pass on serving evidence | VALID |
| No open issue, but rules fire on serving evidence | STALE (the control loop opens the issue) |

**Transitions and who performs them** (no LLM performs any of these):

| From → To | Performed by | Mechanism |
|---|---|---|
| VALID → STALE | `control-loop` job `check` | Create issue with labels `stale-claim::<id>`, `stale-state::stale` |
| STALE → REMEDIATING | `control-loop` job `dispatch` | Assign `STALE_REMEDIATOR_USER_ID` (triggers the flow); set `::remediating`; add `stale-attempt::1` |
| REMEDIATING → REPROVING | MR pipeline job `mark-reproving` | Runs when an MR labelled `stale-remediation` + `stale-claim::<id>` gets its first pipeline |
| REPROVING → VALID | Release job `mint` | Write lockfile, post summary, close issue |
| REPROVING → REMEDIATING (retry) | Release job `rollback` | If attempts < max: set `::retry-pending`, add `stale-attempt::2`. Then `dispatch` (next control-loop run, ≥ 10 min later, or as soon as an analyst note exists) un-assigns and re-assigns the remediator |
| any → QUARANTINED | `rollback` (attempts exhausted) or `verify-mr` (policy violation) | Set `::quarantined`, unassign bots, assign `STALE_HUMAN_ESCALATION_USER_ID`, post evidence |

### 6.5 Claim issue format (`issue_format.py`)

- **Title:** `[stale] <claim-id>: <first finding rule> – <one-line summary>`
- **Body:** markdown. The machine-readable block MUST be byte-exact so both the flow and the engine can parse it:

````markdown
## Stale claim: anchor-trusted
**Assertion:** …
**Findings:** R1 (high), R3 (high)

| Evidence | Last minted | Now |
|---|---|---|
| Node | v24.x.y | v24.x.y (security floor for 24.x: v24.22.1) |
| Distrusted anchors in effective set | none | Demo Root G3 (sha256:3eab…) |

<!-- stale:machine:begin -->
```json
{"claim":"anchor-trusted","rules":["R1","R3"],"playbook":".stale/playbooks/anchor-trusted.md",
 "allowPaths":["app/ledger-api/Dockerfile","app/ledger-api/deploy/trust/**"],
 "facts":{"node_floor":"v24.22.1","node_image_tag":"24.22.1-bookworm-slim",
          "add_excluded_spki":["3eab3fba…"]}}
```
<!-- stale:machine:end -->
````

`facts` contains everything the agent needs, so it never has to look anything up on the web.

### 6.6 MR conventions (enforced by `verify-mr`)

- **Source branch:** `stale/<claim-id>/<issue-iid>-a<attempt>`
- **Title:** `fix(stale): remediate <claim-id> (attempt <n>)`
- **Description:** MUST contain `Related to #<iid>` and MUST NOT contain closing keywords (`close`, `fix`, `resolve` + `#`).
- **Labels:** `stale-remediation`, `stale-claim::<id>`, and on retries `stale-retry`.

---

## 7. Evaluation targets and the gate

### 7.1 Serving evidence (control loop)

- **anchor:**
  - `GET <stable-tag-url>/internal/runtime`. The tag `stable` always points at the 100% revision (§15.5).
  - If several revisions carry traffic, query each tagged revision. Untagged revisions with traffic are a finding (`R0`: unobservable revision).
- **migration:** pending migrations are the migration files at `main` HEAD that are absent from the live `schema_migrations` table, combined with live `pg_class` statistics.
- **dependency:** `package-lock.json` at `main` HEAD, checked against registry metadata.

### 7.2 Candidate evidence (gate, MR reprove)

- **anchor:** the job runs **inside the candidate image** (`image: $CI_REGISTRY_IMAGE/ledger-api:$CI_COMMIT_SHA`) and executes `node /app/src/runtime-report.cjs`. This prints the same JSON as `/internal/runtime`, with the trust policy applied.
- **migration:** the candidate's migration files, checked against live statistics.
- **dependency:** the candidate's lockfile.

### 7.3 `stale gate --target candidate` result

| Result | Condition | Exit code |
|---|---|---|
| `pass` | No enabled claim has findings on candidate evidence | 0 |
| `defer` | Findings exist only for claims whose issue is open in REMEDIATING/REPROVING/retry-pending (a fix is in flight) | 3 |
| `block` | Any finding on a claim with no open remediation, or any QUARANTINED claim | 1 |

`plan-release` (§15.3) turns the result into a dynamic child pipeline:

- `pass` → the real release;
- `defer` → a single job that prints the reason and succeeds;
- `block` → a failing job with the findings.

This removes the need for any "exemption". In the demo, if the anchor fix merges first while the migration is still stale, that release defers. The migration fix merges next, and one release then ships both fixes.

---

## 8. `stale` CLI (Python 3.12, typer)

Dependencies:

- `typer`, `pydantic>=2`, `httpx`, `cryptography>=43`, `pglast>=7`, `psycopg[binary]>=3.2`, `rfc8785`, `pyyaml`
- `google-cloud-run`, `google-cloud-monitoring`, `google-auth`
- dev: `pytest`, `pytest-httpx`

Global options: `--claims-dir .stale/claims`, `--out .stale/out`, `--json`.

**Exit codes:** 0 ok · 1 policy failure (blocked/failed) · 2 infrastructure or collector error · 3 deferred.

| Command | Behaviour |
|---|---|
| `stale validate` | Validate all claim files, playbooks exist, `allowPaths` globs are valid. Run in every pipeline. |
| `stale collect --target serving\|candidate [--claim ID]` | Run collectors and write the evidence file. A collector error is recorded in `errors` and leads to exit 2 **only** if every collector for a claim failed. |
| `stale check --target serving [--open-issues] [--dry-run]` | Evaluate rules. For each claim with findings and no open claim issue, create the issue (§6.5) if `--open-issues` is set. Always exits 0 unless exit 2 applies. Writes `check-report.md` as a job artifact. |
| `stale dispatch` | For claim issues labelled `::stale`, assign the remediator and set `::remediating` + `stale-attempt::1`. For `::retry-pending` issues where an analyst note exists **or** 10 min have elapsed: unassign, then re-assign the remediator and set `::remediating`. Idempotent. |
| `stale gate --target candidate` | §7.3. Writes `gate.json`. |
| `stale verify-mr --mr-iid N` | §15.4. Exit 1 also performs the quarantine transition. |
| `stale reprove --claim ID --stage pre-deploy [--force]` | If the candidate evidence fingerprint equals the minted fingerprint and `--force` is absent, print `[SKIP]` and write `sci-skip.json` with the job's historical median duration (§18). Otherwise run the claim's `preDeploy` proofs (§12.1). |
| `stale reprove --stage canary --canary-url URL` | Run the canary probes of every claim with an open issue, plus always `runtime_report_matches` (§12.2). |
| `stale migrate plan\|apply --database-url URL [--dir app/ledger-api/db/migrations]` | §10. |
| `stale mint --revision REV` | Re-collect **serving** evidence. For every enabled claim, if rules pass: update its lock entry, and if a claim issue is open, post the summary and close it. Commit the lockfile with `[skip ci]` using `STALE_BOT_TOKEN`; on a push race, rebase and retry up to 3 times. |
| `stale rollback-transition --reason FILE` | Retry or quarantine bookkeeping (§6.4) for every claim whose issue is in `::reproving`. |
| `stale report` | Markdown summary of states, fingerprints, and SCI totals. |

---

## 9. Claims and rules

### 9.1 `anchor-trusted` (eMudhra + Node2Shell)

**Evidence: `runtime_report`** (identical JSON from `/internal/runtime` and from `runtime-report.cjs`):

```json
{
  "node_version": "v24.22.0",
  "revision": "ledger-api-00012-xyz",
  "trust_policy": { "loaded": true, "excluded_spki_count": 0, "removed": 0 },
  "ca_source_flags": { "use_system_ca": false, "use_openssl_ca": false, "extra_ca_file": true },
  "effective_ca_spki": ["<sha256 hex of DER SPKI>", "…"],
  "effective_ca_subjects": { "<spki>": "CN=Demo Root G3" },
  "effective_ca_keys": { "<spki>": { "type": "ec", "curve": "prime256v1", "x": "<hex>" } }
}
```

**Feeds** (format fixed; the baseline copies live in `demo/feeds/baseline/`):

- `node-releases.json`: the exact shape of `https://nodejs.org/dist/index.json` (array of `{version, date, lts, security}`).
- `node-schedule.json`: the shape of `https://raw.githubusercontent.com/nodejs/Release/main/schedule.json` (`{"v24": {"start": "...", "end": "..."}}`).
- `root-distrust.json`:
  ```json
  {"schema":"stale.rootfeed/v1","updated_at":"…",
   "distrusted":[{"spki_sha256":"…","subject":"…","reason":"…","source":"…"}]}
  ```
  In production mode, an optional adapter builds this file from Mozilla OneCRL and root removals. That adapter is out of scope for the demo.

**Derived:** `line_security_floor` = the highest version in the same major line whose entry has `security: true`. `line_eol` = the schedule `end` date for the line.

**Rules:**

| Rule | Fires when | Severity |
|---|---|---|
| R1 node-patched-for-line | `semver(node_version) < line_security_floor` | high |
| R2 node-line-not-eol | `today > line_eol` (soak ×3, since the fix needs a major upgrade) | high |
| R3 anchors-in-root-program | `effective_ca_spki ∩ distrusted ≠ ∅` | high |
| R4 anchors-not-weak | an EC anchor whose public x-coordinate equals `(k·G).x` for 1 ≤ k < 2^16 on P-256 or P-384, **or** an RSA anchor whose modulus is < 2048 bits | critical |

**R4 implementation (`weakkeys.py`):**

- Precompute x-coordinates for k in [1, 65535] per curve using `ec.derive_private_key(k, curve).public_key()`.
- Cache them as a sorted array in `engine/stale_engine/data/weak_ec_x_<curve>.bin`, generated at build time by `python -m stale_engine.weakkeys build`.
- Lookup is a binary search. Measured in this repo's spike: k=4 is found instantly.

Note: checking x alone also catches the negated point (−kG), which is just as weak.

**Real-world note:** a spike on Node 22.23.4 showed its bundled roots include `emSign Root CA - G1` but **not** `emSign ECC Root CA - G3`. Do not claim that real Node images bundle G3. The collector is generic; the demo uses Demo Root G3.

### 9.2 `migration-safe` (Railway)

**Evidence:**

- `pending_migrations`: list of `{version, file, no_transaction: bool, statements: [{sql_normalized, kind, table, lock_mode, concurrent, sets_lock_timeout_ms}]}`. Parsed with `pglast.split` + `pglast.parse_sql`.
- `pg_stats`: `{table: {reltuples, relpages}}` from `SELECT relname, reltuples::bigint, relpages FROM pg_class WHERE relkind='r' AND relnamespace='public'::regnamespace`.
- `threshold` = `STALE_ROW_THRESHOLD`.

**Lock-mode table (`lockmodes.py`):** it MUST cover at least the following.

| Statement | Lock |
|---|---|
| `CREATE INDEX` | SHARE |
| `CREATE INDEX CONCURRENTLY` | SHARE UPDATE EXCLUSIVE (non-blocking for DML) |
| `DROP INDEX` | ACCESS EXCLUSIVE |
| `DROP INDEX CONCURRENTLY` | SHARE UPDATE EXCLUSIVE |
| `ALTER TABLE … ADD COLUMN` (no default or a non-volatile default) | ACCESS EXCLUSIVE, brief, no table rewrite |
| `ADD COLUMN … DEFAULT <volatile>` | ACCESS EXCLUSIVE + table rewrite |
| `ALTER COLUMN TYPE` (rewriting) | ACCESS EXCLUSIVE + table rewrite |
| `ADD CONSTRAINT … NOT VALID` | SHARE ROW EXCLUSIVE, brief |
| `VALIDATE CONSTRAINT` | SHARE UPDATE EXCLUSIVE |

Anything unknown → `ACCESS EXCLUSIVE` (fail safe).

**Rules** (a "large" table means `reltuples >= threshold`):

| Rule | Fires when |
|---|---|
| M1 no-blocking-index-on-large-table | `CREATE INDEX` without `CONCURRENTLY` on a large table |
| M2 no-rewrite-on-large-table | Volatile default or rewriting type change on a large table |
| M3 lock-timeout-required | Any statement taking ACCESS EXCLUSIVE or SHARE on a large table, in a file that does not `SET lock_timeout` (≤ 2000 ms) before it |
| M4 concurrent-needs-no-transaction | `CONCURRENTLY` used in a file without the `-- stale:no-transaction` header (regardless of size: it would fail at runtime) |
| M5 concurrent-must-be-retry-safe | `CREATE INDEX CONCURRENTLY … IF NOT EXISTS`, or a concurrent create without a preceding `DROP INDEX CONCURRENTLY IF EXISTS` of the same name |

### 9.3 `dependency-trusted` (axios) — Should

**Evidence** per locked package `name@version`, plus its previous published version:

- whether `dist.attestations` is present;
- the `_npmUser.email` / maintainers list;
- whether `scripts.preinstall|install|postinstall` exist, and the sha256 of each;
- the `time[version]` timestamp.

**Rules:**

- **D1:** provenance present on the previous version, absent on this one.
- **D2:** publisher email not in the previous version's maintainer emails.
- **D3:** a new or changed install script.
- **D4:** version younger than 72 h.

**Demo:** a Verdaccio registry serving `@demo/ledger-utils` **[VERIFY]**. Verdaccio does not emulate npm provenance, so the demo registry metadata is served from a static JSON mirror whose `dist.attestations` field we control.

### 9.4 `contract-compatible` (CrowdStrike) — stretch, `enabled: false`

Specify only if milestones M1–M7 are done.

**Sketch:**

- **Evidence:** a config JSON schema hash, plus `contract_version` label of every revision with traffic > 0.
- **Rule:** the config uses a field not supported by the minimum live `contract_version`.

---

## 10. Migration runner (`stale migrate`)

- **Files:** `app/ledger-api/db/migrations/<14-digit-version>_<name>.sql`.
- **Optional header line 1:** `-- stale:no-transaction`.
- **Sections:** `-- migrate:up` … `-- migrate:down`.
- **Tracking table:** `schema_migrations(version text primary key, applied_at timestamptz, checksum text)`.
- **Apply algorithm:**
  1. Read applied versions.
  2. Take a session advisory lock: `pg_advisory_lock(hashtext('stale-migrate'))`.
  3. For each pending file in order, split the up-section with `pglast.split`.
  4. If the file is transactional: `BEGIN`, execute each statement, insert the tracking row, `COMMIT`.
  5. If the file is `no-transaction`: autocommit mode on the same connection. Execute each statement separately so that `SET lock_timeout` persists. Insert the tracking row last. Run `RESET lock_timeout` at the end.
  6. After each file, check `SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid`. If any INVALID index exists, exit 1 and do not record the version.
- **`plan`:** prints the pending files and the M-rule findings, using live statistics. It never executes anything.

**Seed migrations:**

```sql
-- 20261020090000_init_ledger.sql
-- migrate:up
CREATE TABLE ledger_entries (
  id bigserial PRIMARY KEY,
  user_id text NOT NULL,
  merchant_id text NOT NULL,
  amount_cents bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ledger_entries_user_idx ON ledger_entries(user_id);
-- migrate:down
DROP TABLE ledger_entries;
```

```sql
-- 20261021090000_add_ledger_entries_merchant_idx.sql   (pending; approved when the table was small)
-- migrate:up
CREATE INDEX ledger_entries_merchant_idx ON ledger_entries(merchant_id);
-- migrate:down
DROP INDEX ledger_entries_merchant_idx;
```

**Expected remediation** (the playbook gives this exact target shape):

```sql
-- stale:no-transaction
-- migrate:up
SET lock_timeout = '1500ms';
DROP INDEX CONCURRENTLY IF EXISTS ledger_entries_merchant_idx;
CREATE INDEX CONCURRENTLY ledger_entries_merchant_idx ON ledger_entries(merchant_id);
-- migrate:down
DROP INDEX CONCURRENTLY IF EXISTS ledger_entries_merchant_idx;
```

---

## 11. Agent plane — GitLab Duo custom flows

**Facts verified against GitLab docs on 2026-10-24:**

- Custom flows are GA in 19.2.
- Triggers available: mention, assign, assign-reviewer, pipeline events, MR lifecycle, work-item created.
- On an assign trigger, `context:goal` = the resource IID. On a pipeline trigger, it is the full pipeline webhook payload.
- Custom flows support `environment: ambient` only.
- Not supported in custom flows: top-level `name`/`description`/`product_group`, `model` in prompts, `response_schema_*`, `ui_role_as`, and `stop` in prompt params.
- Inline prompts need `unit_primitives: []` and `placeholder: history`.
- Literal `{{ }}` in prompt text breaks Jinja rendering.

**[VERIFY]** the exact tool names and their argument schemas in the AI Catalog editor's validation, especially `create_branch`, `create_commit`, `create_merge_request`, `update_issue`, and `gitlab_api_get`. Adjust names if validation rejects them, and log the result.

**Registration:**

1. Paste each YAML file into Automate → Flows → New flow (private) in the project.
2. Enable the flow with the trigger listed below.
3. Record the created service account id and username in the §4.1 variables.

### 11.1 `duo/flows/stale-remediator.yaml` (trigger: **Assign**)

```yaml
version: "v1"
environment: ambient

components:
  - name: "remediator"
    type: AgentComponent
    prompt_id: "stale_remediator_prompt"
    inputs:
      - from: "context:goal"
        as: "issue_iid"
      - from: "context:project_id"
        as: "project_id"
      - from: "context:inputs.agent_platform_standard_context.primary_branch"
        as: "primary_branch"
    toolset:
      - "get_issue"
      - "list_issues"
      - "get_repository_file"
      - "list_repository_tree"
      - "create_branch"
      - "create_commit"
      - "create_merge_request"
      - "create_issue_note"
    ui_log_events:
      - "on_agent_final_answer"
      - "on_tool_execution_success"
      - "on_tool_execution_failed"

prompts:
  - prompt_id: "stale_remediator_prompt"
    name: "Stale remediator"
    unit_primitives: []
    prompt_template:
      system: |
        You repair one stale safety claim by opening exactly one merge request.
        Rules you must follow:
        1. Read the issue. Parse the JSON between the markers
           "<!-- stale:machine:begin -->" and "<!-- stale:machine:end -->".
           It gives: claim, rules, playbook, allowPaths, facts.
        2. Read the playbook file and follow it exactly. Read the claim file under .stale/claims/.
           If the issue has notes from the canary analyst, read them and address them.
        3. You may modify ONLY files matching allowPaths. Never modify anything under .stale/,
           never modify CI files, never modify application source code outside allowPaths.
        4. Use only values given in "facts". Do not invent versions, hashes or names.
        5. Create a branch named stale/CLAIM/ISSUE-aN where CLAIM is the claim id, ISSUE is the
           issue iid and N is the attempt number from the issue's stale-attempt label.
        6. Commit all edits in one commit with message "fix(stale): remediate CLAIM".
        7. Open a merge request into the primary branch titled
           "fix(stale): remediate CLAIM (attempt N)" with labels "stale-remediation" and
           "stale-claim::CLAIM" (add "stale-retry" when N is greater than 1). The description must
           contain "Related to #ISSUE" and a short list of each file changed and why. Never use the
           words close, closes, fix, fixes, resolve or resolves next to an issue reference.
        8. Post one note on the issue linking the merge request.
        If the playbook cannot be followed within allowPaths, do not open a merge request; post a note
        on the issue explaining exactly what blocked you.
        When the merge request is open and the note is posted (or the blocking note is posted),
        your final answer is the merge request URL or the word BLOCKED. No further steps are needed after that.
      user: |
        Project ID: {{ project_id }}
        Issue IID: {{ issue_iid }}
        Primary branch: {{ primary_branch }}
      placeholder: history
    params:
      timeout: 600

routers:
  - from: "remediator"
    to: "end"

flow:
  entry_point: "remediator"
  inputs:
    - category: agent_platform_standard_context
      input_schema:
        primary_branch:
          type: string
          description: Default branch of the repository
        workload_branch:
          type: string
          description: git ref of the workload branch
        session_owner_id:
          type: string
          description: User id that initiated the flow
```

### 11.2 `duo/flows/stale-canary-analyst.yaml` (trigger: **Pipeline events → failed**)

The flow fires on **every** failed pipeline. Its first step MUST exit when the payload is not a failed **release** child pipeline. Identify those by the pipeline variable `STALE_LANE` being `release`, which appears in the payload's `object_attributes.variables` **[VERIFY]**. Fallback: the job name `canary-ring` among failed builds.

```yaml
version: "v1"
environment: ambient

components:
  - name: "analyst"
    type: AgentComponent
    prompt_id: "stale_analyst_prompt"
    inputs:
      - from: "context:goal"
        as: "pipeline_event"
      - from: "context:project_id"
        as: "project_id"
    toolset:
      - "gitlab_api_get"
      - "list_issues"
      - "get_issue"
      - "create_issue_note"
    ui_log_events:
      - "on_agent_final_answer"
      - "on_tool_execution_failed"

prompts:
  - prompt_id: "stale_analyst_prompt"
    name: "Stale canary analyst"
    unit_primitives: []
    prompt_template:
      system: |
        You diagnose failed release pipelines for the Stale system. You never change code,
        labels, assignees or deployments.
        1. Inspect the pipeline event. If it is not a failed release pipeline (no failed job named
           canary-ring, canary-reprove or migrate), your final answer is IGNORED and you stop.
        2. Use gitlab_api_get to read the failed jobs' traces (endpoint /projects/:id/jobs/:job_id/trace)
           and the job artifact rollback-reason.json if present.
        3. Find open issues labelled stale-state::reproving or stale-state::retry-pending.
        4. On each, post one note that starts with the line "<!-- stale:analyst -->" and then gives:
           the failing check, the evidence (quote at most 10 log lines), the most likely cause, and a
           concrete instruction for the next remediation attempt that stays within the claim's allowPaths.
        When the notes are posted, your final answer is the list of issue IIDs you commented on.
        No further steps are needed after that.
      user: |
        Project ID: {{ project_id }}
        Pipeline event: {{ pipeline_event }}
      placeholder: history
    params:
      timeout: 300

routers:
  - from: "analyst"
    to: "end"

flow:
  entry_point: "analyst"
```

### 11.3 Playbooks

Each playbook MUST state:

- the exact file edits;
- a before/after example;
- the forbidden edits;
- how to read `facts`.

`anchor-trusted.md` must say:

1. In `app/ledger-api/Dockerfile`, change the `ARG NODE_IMAGE=` value to `node:<facts.node_image_tag>`. Change nothing else in the Dockerfile.
2. Append each `facts.add_excluded_spki` value as a new line to `deploy/trust/excluded-spki.txt`, if it is not already present.
3. Do **not** edit `extra-roots.pem`. Keeping the root in the file proves the runtime filter works.
4. Do not touch `NODE_OPTIONS`.

`migration-safe.md` gives the §10 target shape and requires keeping the version and filename unchanged.

---

## 12. Re-proof and canary probes

### 12.1 Pre-deploy (MR pipeline and gate)

- **anchor:** run the rules on candidate evidence (§7.2). All of R1–R4 must pass.
- **migration (`shadow_migration.py`), using a `postgres:18` CI service:**
  1. Apply all *applied* migrations.
  2. Seed `ledger_entries` to `STALE_ROW_THRESHOLD × 1.2` rows with `generate_series`, then run `ANALYZE`.
  3. Start a writer thread that inserts a row every 10 ms and records its latency.
  4. Run `stale migrate apply` for the pending files.
  5. In parallel, poll every 50 ms:
     `SELECT mode FROM pg_locks l JOIN pg_class c ON c.oid=l.relation WHERE c.relname='ledger_entries' AND l.pid <> pg_backend_pid() AND l.granted`.
  6. **Fail** if `ShareLock` or `AccessExclusiveLock` was held for more than 100 ms, if the writer's p99 latency exceeds 250 ms, or if any index is INVALID.
  7. Write the observed lock timeline to `shadow-report.json`.
- **dependency:** `npm ci --ignore-scripts` must succeed, then `npm test`. Attach a file-level diff of the pinned package to the job artifacts.

### 12.2 Canary probes (`reprove --stage canary`, against the canary **tag URL** only)

| Probe | Request | Pass |
|---|---|---|
| `runtime_report_matches` | `GET /internal/runtime` | `revision` equals the new revision; rules R1–R4 pass on this report |
| `partner_trusted_ok` | `POST /api/v1/webhooks/deliver {"target":"trusted"}` | 200, `delivered:true` |
| `partner_distrusted_rejected` | `POST … {"target":"distrusted"}` | 502, `delivered:false`, `tls_error ∈ {SELF_SIGNED_CERT_IN_CHAIN, UNABLE_TO_GET_ISSUER_CERT_LOCALLY, UNABLE_TO_VERIFY_LEAF_SIGNATURE, CERT_UNTRUSTED}` |
| `migration_applied` | `GET /status.json` | `schema_version` ≥ highest migration version; no invalid indexes |

### 12.3 Ring checks (`loadtest/ring_slo.js`, against the **live** URL during each ring)

- **Load:** 20 req/s for `SOAK_SECONDS`.
- **Thresholds:** `http_req_failed < 0.01`, `p(95) < 400ms`. The k6 exit code is 99 when a threshold fails.
- **Session isolation:** users `alpha`/`beta` call `GET /api/v1/balance` with their own bearer tokens. Every response's `user_id` must equal the caller, and `Cache-Control` must contain `no-store` and `private`.
  - **Honesty note:** Cloud Run has no shared cache in this path. This probe guards the app's cache contract, which is what stops a CDN like Railway's from caching it. It does not prove a CDN is safe.
- **Per-revision errors:** after each ring, query Cloud Monitoring for `run.googleapis.com/request_count` filtered by `resource.labels.revision_name` and `metric.labels.response_code_class="5xx"` over the soak window. Fail if the canary revision's 5xx ratio is more than baseline + 1%.

---

## 13. Execution plane

### 13.1 Cloud Run service `ledger-api` (multi-container)

- **Ingress container:** `ledger-api` on port 8080.
- **Sidecar:** `partner-sim`. It has no ingress port and listens on `localhost:8443` and `localhost:8444`.
- **Deploy:** `gcloud run deploy ledger-api --container ledger-api --image … --port 8080 --container partner-sim --image …` **[VERIFY]** the multi-container flag syntax. The fallback is `gcloud run services replace` with `infra/service.ledger-api.yaml` rendered by `envsubst`.
- **Settings:** `--allow-unauthenticated` (judges need access), `--add-cloudsql-instances $CLOUDSQL_INSTANCE`, `--min-instances 0`, `--max-instances 3`.

### 13.2 `ledger-api` endpoints (Node 24, Express 5)

| Endpoint | Behaviour |
|---|---|
| `GET /healthz` | 200 `ok` |
| `GET /internal/runtime` | Runtime report (§9.1). Computes SPKI hashes with `crypto.X509Certificate(pem).publicKey.export({type:'spki',format:'der'})` → sha256 hex over `tls.getCACertificates('default')`. Unauthenticated (it contains no secrets). |
| `GET /api/v1/balance` | Requires `Authorization: Bearer <token>`, mapped to a user via `DEMO_TOKENS`. Runs `SELECT COALESCE(SUM(amount_cents),0)::bigint AS balance FROM ledger_entries WHERE user_id=$1`. Sets `Cache-Control: private, no-store` and `Vary: Authorization`. Returns `{user_id, balance_cents, revision}`. 401 when the token is missing or unknown. |
| `POST /api/v1/webhooks/deliver` | Body `{target}`. Uses `https.request` to the matching `PARTNER_*_URL` with the **default** agent (so the trust policy applies), `servername:'localhost'`, 3 s timeout. Returns 200 `{delivered:true}`, or 502 `{delivered:false, tls_error:err.code}`. |
| `GET /status`, `GET /status.json` | Judge dashboard: revision, node version, trust-policy summary, claim states (from the lockfile via the GitLab API, cached 30 s), schema version, SCI totals. The HTML is server-rendered and has no external assets. |

### 13.3 Trust policy (`src/trust-policy.cjs`, loaded with `node --require ./src/trust-policy.cjs src/index.js`)

```js
// Runs before any TLS use. Removes distrusted anchors from Node's default CA set.
const tls = require('node:tls'), crypto = require('node:crypto'), fs = require('node:fs');
const file = process.env.STALE_EXCLUDED_SPKI_FILE;
const excluded = new Set((file && fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '')
  .split('\n').map(s => s.trim().toLowerCase()).filter(s => /^[0-9a-f]{64}$/.test(s)));
const spki = pem => crypto.createHash('sha256')
  .update(new crypto.X509Certificate(pem).publicKey.export({ type: 'spki', format: 'der' }))
  .digest('hex');
const before = tls.getCACertificates('default');
const kept = before.filter(p => !excluded.has(spki(p)));
if (kept.length !== before.length) tls.setDefaultCACertificates(kept);
globalThis.__stalePolicy = { loaded: true, excluded_spki_count: excluded.size,
                             removed: before.length - kept.length };
```

**Requirements:**

- Node ≥ 22.19 / 24.5, where `tls.setDefaultCACertificates` exists. The spike on v22.23.4 confirmed `getCACertificates('default')` reflects `setDefaultCACertificates`.
- The policy only filters the **default** set. Code that passes an explicit `ca:` option bypasses it. The app MUST NOT pass `ca:`.
- `--use-openssl-ca` makes `getCACertificates('default')` return an empty list (observed). Therefore `ledger-api` MUST NOT use `--use-openssl-ca`, and `runtime_report` treats an empty set as finding `R0`.

### 13.4 `partner-sim` sidecar

- A small `node:https` server.
- `:8443` serves `leaf-trusted` chained to **Demo Trusted Root**.
- `:8444` serves `leaf-g3` chained to **Demo Root G3**.
- Both leaves are issued for SAN `DNS:localhost`. Each responds 200 `{ok:true}`.
- The certificates are generated by `demo/generate_demo_pki.py` and **baked into the sidecar image at build time**. The private keys are demo-only; commit them under `app/partner-sim/certs/` with a README warning.

`generate_demo_pki.py`:

- Demo Root G3 uses `ec.derive_private_key(4, ec.SECP256R1())`.
- Demo Trusted Root uses a normal random P-256 key.
- The script prints both roots' SPKI hashes and writes `extra-roots.pem` (both roots).

### 13.5 `Dockerfile` (ledger-api)

```dockerfile
ARG NODE_IMAGE=node:24.22.0-bookworm-slim   # baseline; remediation bumps this line only [VERIFY tag exists]
FROM ${NODE_IMAGE}
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts
COPY src ./src
COPY deploy ./deploy
ENV NODE_EXTRA_CA_CERTS=/app/deploy/trust/extra-roots.pem \
    STALE_EXCLUDED_SPKI_FILE=/app/deploy/trust/excluded-spki.txt
USER node
CMD ["node", "--require", "./src/trust-policy.cjs", "src/index.js"]
```

`src/runtime-report.cjs` MUST exist in the image so that candidate inspection (§7.2) can run `node --require ./src/trust-policy.cjs src/runtime-report.cjs`.

---

## 14. Data seed

- **Users:** `alpha` (balance 10000 cents) and `beta` (25000 cents). Seeded by `demo/world_shift.py reset`.
- **Bulk rows:** use random merchants (`m-0000`…`m-0999`) and spread across other synthetic users.

---

## 15. CI/CD

### 15.1 Lanes (`.gitlab-ci.yml`)

```yaml
workflow:
  rules:
    - if: $CI_PIPELINE_SOURCE == "schedule" && $STALE_PIPELINE == "control-loop"
      variables: { STALE_LANE: control }
    - if: $CI_PIPELINE_SOURCE == "schedule" && $STALE_PIPELINE == "release-train"
      variables: { STALE_LANE: train }
    - if: $CI_PIPELINE_SOURCE == "web" && $STALE_PIPELINE        # manual run of either
      variables: { STALE_LANE: $STALE_PIPELINE }
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      variables: { STALE_LANE: mr }
    - if: $CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
      variables: { STALE_LANE: main }
    - if: $CI_PIPELINE_SOURCE == "parent_pipeline"
      variables: { STALE_LANE: release }
    - when: never

stages: [plan, create, verify, package, secure, configure, release, monitor, govern]

include:
  - local: ci/common.yml
  - local: ci/control-loop.yml
    rules: [{ if: $STALE_LANE == "control" || $STALE_LANE == "control-loop" }]
  - local: ci/mr.yml
    rules: [{ if: $STALE_LANE == "mr" }]
  - local: ci/main.yml
    rules: [{ if: $STALE_LANE == "main" }]
  - local: ci/release-train.yml
    rules: [{ if: $STALE_LANE == "train" || $STALE_LANE == "release-train" }]
```

**[VERIFY]** `include:rules` with variables set in `workflow:rules`. If that is not honoured, put `rules:` on each job instead, using a shared `.lane_*` template.

**Schedules (create in the UI; record them in the README):**

- `control-loop`: every 15 min (`*/15 * * * *`).
- `release-train`: daily at 10:00 project time.
- In the demo, both are also run manually ("Run pipeline").

### 15.2 Templates (`ci/common.yml`)

```yaml
.gcp_auth:
  id_tokens:
    GITLAB_OIDC_TOKEN:
      aud: https://iam.googleapis.com/projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}
  before_script:
    - echo "$GITLAB_OIDC_TOKEN" > /tmp/oidc
    - gcloud iam workload-identity-pools create-cred-config
        "projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}"
        --service-account="${GCP_DEPLOYER_SA}" --credential-source-file=/tmp/oidc --output-file=/tmp/gcp.json
    - export GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp.json CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE=/tmp/gcp.json
    - gcloud config set project "$GCP_PROJECT_ID"

.engine:
  image: python:3.12-slim
  before_script:
    - pip install -q ./engine

.cloudsql_proxy:   # starts proxy on 127.0.0.1:5432 using the WIF credentials
  script_prefix: &proxy
    - curl -sSLo /usr/local/bin/cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.0/cloud-sql-proxy.linux.amd64   # [VERIFY latest]
    - chmod +x /usr/local/bin/cloud-sql-proxy
    - cloud-sql-proxy --credentials-file /tmp/gcp.json "$CLOUDSQL_INSTANCE" --port 5432 & sleep 3
```

Jobs needing both GCP and Python use an image with both installed: build `ci/images/stale-ci` (python 3.12 + gcloud SDK + k6) once in M1 and push it to the GitLab registry.

### 15.3 Pipelines and jobs

**Control loop (`ci/control-loop.yml`):**

| Job | Stage | Does |
|---|---|---|
| `validate` | plan | `stale validate` |
| `collect-serving` | plan | Cloud SQL proxy + `stale collect --target serving` |
| `check` | plan | `stale check --target serving --open-issues` (always exits 0 unless error) |
| `dispatch` | create | `stale dispatch` |
| `report` | govern | `stale report` → job artifact + pipeline summary |

**MR (`ci/mr.yml`):**

| Job | Stage | Does |
|---|---|---|
| `validate` | plan | `stale validate` |
| `verify-mr` | govern | Only for MRs labelled `stale-remediation`: path and policy check, then enables auto-merge (§15.4) |
| `mark-reproving` | create | For `stale-remediation` MRs: issue → `::reproving` |
| `engine-tests` | verify | `pytest engine/tests` |
| `app-tests` | verify | `npm ci --ignore-scripts && npm test` in `app/ledger-api` |
| `lockfile-sync` | verify | Only on `stale-remediation` MRs touching `package.json`: `npm install --package-lock-only --ignore-scripts`; if the lockfile changed, push it to the MR branch with `STALE_BOT_TOKEN` and exit 1 (a new pipeline follows) |
| `build-candidate` | package | Kaniko build of `ledger-api` and `partner-sim`. Push to `$CI_REGISTRY_IMAGE/*:$CI_COMMIT_SHA` **and** Artifact Registry |
| `inspect-candidate` | verify | `image: $CI_REGISTRY_IMAGE/ledger-api:$CI_COMMIT_SHA`, runs `runtime-report.cjs` → artifact `candidate-runtime.json` |
| `reprove` | verify | `stale reprove --claim <each> --stage pre-deploy` using `candidate-runtime.json` and a `postgres:18` service |
| SAST, secret detection, dependency scanning, container scanning | secure | GitLab templates, jobs re-staged to `secure` (`CS_IMAGE=$CI_REGISTRY_IMAGE/ledger-api:$CI_COMMIT_SHA`) **[VERIFY job names in the pipeline editor]** |
| `security-gate` | secure | Parses `gl-*-report.json` artifacts; fails on any **new** Critical/High finding not in `.stale/security-baseline.json` |

**Main (`ci/main.yml`)** runs on every push to `main`, including merged remediation MRs:

| Job | Stage | Does |
|---|---|---|
| `build`, `inspect-candidate`, scans, `security-gate` | package/verify/secure | Same as MR |
| `plan-release` | release | Looks up merged MRs for `$CI_COMMIT_SHA` (`GET /projects/:id/repository/commits/:sha/merge_requests`). If **no** merged MR carries `stale-remediation`, it writes a no-op child ("waiting for release train"). Otherwise it runs `stale gate --target candidate` and writes `release-plan.yml` = release / defer / block |
| `release` | release | `trigger: { include: [{ artifact: release-plan.yml, job: plan-release }], strategy: depend }` |

**Release train (`ci/release-train.yml`):** same jobs as main, but `plan-release` always runs the gate and the release regardless of MR labels.

**Release child (`ci/release.yml`)** with `resource_group: production` on every job:

| Job | Stage | Does |
|---|---|---|
| `record-stable` | configure | Reads the current 100% revision (`gcloud run services describe --format=json`) → dotenv `STABLE_REVISION` |
| `migrate` | release | Cloud SQL proxy + `stale migrate apply` (expand-only migrations ship before code) |
| `deploy-canary` | release | `gcloud run deploy … --no-traffic --tag c-$CI_PIPELINE_ID` → dotenv `CANARY_REVISION`, `CANARY_URL` (taken from `status.traffic[].url` where `tag` matches) |
| `canary-reprove` | monitor | `stale reprove --stage canary --canary-url $CANARY_URL` |
| `canary-ring` | monitor | For R in 10, 50, 100: `update-traffic --to-tags c-$CI_PIPELINE_ID=$R` → `k6 run loadtest/ring_slo.js` → per-revision 5xx check |
| `promote` | release | `update-traffic --to-revisions $CANARY_REVISION=100`, then `--update-tags stable=$CANARY_REVISION`, then `--remove-tags c-$CI_PIPELINE_ID`. Never use `--to-latest`: it can pick up a revision this pipeline did not verify. |
| `mint` | govern | `stale mint --revision $CANARY_REVISION` |
| `rollback` | monitor | `when: on_failure`, `needs: [record-stable]`. Runs `update-traffic --to-revisions $STABLE_REVISION=100`, writes `rollback-reason.json` (failed job names, probe output), then `stale rollback-transition --reason rollback-reason.json` |

The migration in a hotfix runs before the canary. This is safe only because §9.2 rules guarantee non-blocking, retry-safe DDL. A failed canary does **not** roll the migration back; expand-only migrations are backward compatible by design. State this in the README.

### 15.4 `verify-mr` + auto-merge

All checks must hold, otherwise the claim is quarantined:

1. The MR author's username equals `STALE_REMEDIATOR_USERNAME`.
2. The MR has `stale-remediation` and exactly one `stale-claim::<id>` label, and that claim's issue is open.
3. Every changed path (`GET /projects/:id/merge_requests/:iid/diffs`, paginated) matches the claim's `allowPaths` globs (fnmatch with `**`). The one exception is `package-lock.json` when it was pushed by `lockfile-sync` (author = token bot user).
4. No path under `.stale/`, `ci/`, `.gitlab-ci.yml`, or `duo/` changed.
5. The description contains no closing keyword next to a `#<n>` reference.

Then call `PUT /projects/:id/merge_requests/:iid/merge` with `auto_merge=true` (older instances: `merge_when_pipeline_succeeds=true`) **[VERIFY parameter name]**. GitLab merges only when the whole pipeline succeeds. Project setting: "Pipelines must succeed" = on.

### 15.5 Stage mapping (honest)

| GitLab stage | Real jobs in this project |
|---|---|
| Plan | `collect-serving`, `check` (opens the issue) |
| Create | `dispatch` → Duo remediator writes the MR; `mark-reproving` |
| Verify | `engine-tests`, `app-tests`, `inspect-candidate`, `reprove` (shadow migration) |
| Package | `build-candidate` (Kaniko → GitLab registry + Artifact Registry) |
| Secure | SAST, secrets, dependency and container scanning, `security-gate`, R3/R4/D1–D4 |
| Configure | `record-stable`, WIF credential exchange, Cloud Run traffic/tag config |
| Release | `plan-release`, `migrate`, `deploy-canary`, `promote` |
| Monitor | `canary-reprove`, `canary-ring`, Cloud Monitoring checks, analyst flow |
| Govern | `verify-mr`, gate decisions, `mint` (audit trail), quarantine |

---

## 16. Infrastructure (`infra/`, Terraform ≥ 1.7, google provider ≥ 6)

**Resources:**

- APIs: run, sqladmin, artifactregistry, iam, iamcredentials, sts, monitoring, storage.
- Artifact Registry docker repo `stale`.
- Cloud SQL Postgres 18 (`db-f1-micro` or the smallest tier available) with public IP + IAM-authorised connector only (no authorised networks), database `ledger`, users `stale_app` and `stale_migrator`.
- GCS bucket `<project>-stale-world`. Public read on `feeds/` objects only (uniform bucket-level access + an `allUsers:objectViewer` binding scoped to that bucket, which contains feeds only).
- Service accounts:
  - `stale-runtime` (Cloud SQL Client);
  - `stale-deployer` (Run Admin, Service Account User on `stale-runtime`, Artifact Registry Writer, Cloud SQL Client, Monitoring Viewer).
- Workload Identity pool + OIDC provider with:
  - issuer `https://gitlab.com`;
  - attribute mapping `google.subject=assertion.sub`, `attribute.project_path=assertion.project_path`, `attribute.ref=assertion.ref`, `attribute.ref_protected=assertion.ref_protected`;
  - **attribute condition** `assertion.project_path == "<group>/<project>"`.
- `roles/iam.workloadIdentityUser` on `stale-deployer` for `principalSet://iam.googleapis.com/projects/<n>/locations/global/workloadIdentityPools/<pool>/attribute.project_path/<group>/<project>`.

The Cloud Run service itself is created by the first release pipeline, not by Terraform, so the deploy flow stays in CI.

---

## 17. Demo world (`demo/`)

### 17.1 Versions

- Baseline image: the 24.x tag immediately before `24.22.1` (expected `24.22.0`) **[VERIFY the Docker Hub tag]**.
- Patched image: `node:24.22.1-bookworm-slim`.
- The mirrored `node-releases.json` baseline marks no newer 24.x entry as security. Scenario A adds the real v24.22.1 entry with `security: true`.

### 17.2 `world_shift.py`

It never touches the repository.

| Command | Effect |
|---|---|
| `reset` | Upload `demo/feeds/baseline/*` to the bucket. Truncate `ledger_entries`, insert the 2 demo users' rows plus 100k background rows, `ANALYZE`. Reset Cloud Run to the baseline revision (manual helper). |
| `scenario-a` | Upload a `node-releases.json` that adds `{"version":"v24.22.1","security":true,...}`. Upload a `root-distrust.json` that lists Demo Root G3's SPKI with `source: "demo mirror of root-program action"`. |
| `scenario-b` | Insert `STALE_ROW_THRESHOLD × 1.5` rows via `INSERT … SELECT generate_series`, then `ANALYZE ledger_entries`. `reltuples` updates only after ANALYZE. |

### 17.3 End-to-end expected timeline (acceptance test E2E-1)

1. `reset`. Then a release train ships the baseline, and the control loop shows all claims VALID.
2. `scenario-a` + `scenario-b`, with no commits.
3. Control loop (manual run): two issues are opened. `dispatch` assigns the remediator, and two flows run.
4. Two MRs open. The MR pipelines pass, and `verify-mr` enables auto-merge.
5. The first merge's main pipeline produces `plan-release` = **defer** (the other claim is still in flight). The second merge produces **pass** → release child: migrate → canary → probes → 10/50/100 → promote → mint.
6. Both issues are closed with summaries. `/status` shows the new revision, Node v24.22.1, Demo Root G3 removed, and the merchant index valid.

**E2E-2 (failure path):**

1. Before step 3, set the demo flag `STALE_DEMO_BREAK_CANARY=1` on the release child. This makes `canary-ring` fail at 10%.
2. Expected: rollback to the stable revision, `::retry-pending`, an analyst note, re-dispatch, a second attempt, and success (with the flag unset after the first failure).
3. A third failure leads to `::quarantined` and the human is assigned.

---

## 18. SCI accounting (`sci.py`)

**When `reprove` skips** (the fingerprint equals the minted one):

1. Look up the median duration of the last 5 **non-skipped** runs of the same job name via `GET /projects/:id/jobs?scope=success`.
2. Write `sci-skip.json {job, seconds_avoided}`.

`stale mint` aggregates the `sci-skip.json` artifacts from pipelines since the last mint into `lockfile.sci`.

**Formula, per avoided job:**

```latex
E_{kWh} = \frac{P_{kW} \cdot t_{s}}{3600}, \qquad C = E \cdot I + M
```

| Constant | Value | Notes |
|---|---|---|
| P (power) | 0.030 kW (2 vCPU × 15 W, `saas-linux-small-amd64`) | Stated assumption |
| I (grid intensity) | `STALE_GRID_INTENSITY_GCO2_KWH`, default 440 | Stated assumption |
| M (embodied) | 0 unless `STALE_EMBODIED_G_PER_MIN` is set | Do not invent a value |

Report the numbers as "estimated avoided emissions", with the constants printed beside them. The functional unit R = one verification pipeline.

---

## 19. Build order and acceptance criteria

| Milestone | Deliverables | Acceptance (all must pass) |
|---|---|---|
| M0 Spikes | `docs/verification-log.md` | Every [VERIFY] item confirmed or worked around |
| M1 Foundation | LICENSE, repo layout, `ci/images/stale-ci`, Terraform applied, labels script, empty Cloud Run service | `terraform plan` clean; a CI job lists Cloud Run services through WIF |
| M2 Engine core | models, fingerprint, rules R1–R4, M1–M5, D1–D4, weakkeys, issue_format, state | `pytest`: golden fixtures per rule (fires/does not fire); fingerprint stable across key order; k=4 detected; issue body round-trips |
| M3 App + trust | ledger-api, trust policy, runtime report, partner sidecar, demo PKI | Local docker compose: distrusted call → 502 after adding SPKI to excluded list; 200 before; trusted → 200 always |
| M4 Migration runner | `stale migrate`, shadow reprove | Plain `CREATE INDEX` on 600k rows → shadow fails (ShareLock seen); rewritten file → passes; an injected failed CIC leaves an INVALID index which the runner detects |
| M5 Release lane | release child, canary, rings, rollback, promote, mint | Manual release ships the baseline; forced ring failure restores the stable revision with 100% traffic |
| M6 Control loop + flows | control-loop, dispatch, both flows registered, verify-mr, auto-merge | E2E-1 passes for scenario A alone |
| M7 Full demo | Scenario B, defer logic, failure path | E2E-1 (both scenarios) and E2E-2 pass twice in a row from `reset` |
| M8 Should | dependency-trusted with mirror registry | D1/D2/D3 fire on the poisoned version; the remediation pin passes |
| M9 Submission | README, docs, video script, `/status` polish | Repo public, MIT visible, pipeline history shows the runs, app live until Nov 16 |

---

## 20. Risks to check first (M0 spike list)

1. Assignment by a bot token (`STALE_BOT_TOKEN`) triggers the flow's Assign trigger.
2. Flow tool names and argument schemas (`create_branch`, `create_commit`, `create_merge_request`, `gitlab_api_get`).
3. The pipeline-event payload contains enough to recognise release pipelines.
4. `gcloud run deploy` multi-container flags, plus tag URLs available in `status.traffic`.
5. `include:rules` honours variables set in `workflow:rules`.
6. Auto-merge API parameter name on gitlab.com today.
7. Security template job names and their stage override.
8. The exact Node 24.x baseline Docker tag exists.
9. Scoped labels are available in the provisioned group.

---

## 21. Real vs simulated (for README and video)

| Element | Demo | Production behaviour |
|---|---|---|
| Distrusted root | Demo Root G3 (P-256 key with private scalar 4) + a GCS-hosted distrust feed we update | Collector reads a Mozilla-derived feed |
| Node security release | Mirrored feed entry for the real v24.22.1 | `nodejs.org/dist/index.json` |
| Table growth | Real rows inserted into Cloud SQL + ANALYZE | Organic growth |
| Poisoned package | Mirror registry metadata we control (no harmful code) | Public npm |
| Cloud Run, Cloud SQL, scans, canary, Duo flows, GitLab issues/MRs | Real | Real |

No real compromised keys, no exploit code, and no third-party systems are touched.

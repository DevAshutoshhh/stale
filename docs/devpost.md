# Devpost submission text (draft)

> Every claim below is visible in the repo, its pipeline history, or the video.

## Inspiration

In October 2026 a researcher found that eight eMudhra intermediate CA keys were trivially guessable. Mozilla removed *emSign ECC Root CA - G3* in NSS 3.131.1, and Node.js shipped v24.22.1 with that root store. Every Node service that had been "verified safe to ship" a week earlier was still running the old trust store, and nothing in its pipeline would notice, because nothing in its repository had changed.

The same shape shows up in other incidents. Railway's 2025-10-28 outage came from an index migration that was fine when the table was small and took a blocking lock once it held ~1 billion rows. The axios compromise in March 2026 made a pinned "trusted" dependency untrustworthy overnight. CrowdStrike's Channel File 291 shipped content whose compatibility had been assumed, not re-proven.

Each was a safety verdict nobody re-checked. CI only re-checks when code changes. We wanted the world changing to be a trigger too.

## What it does

Stale treats every "safe to ship" verdict as a cached value with evidence behind it.

- A **claim** (`.stale/claims/*.yaml`) states an assertion, how to collect evidence, which deterministic rules decide staleness, which files a fix may touch, and how to re-prove it.
- A scheduled **control loop** collects evidence from the serving Cloud Run revision (`/internal/runtime`), public feeds, and live Postgres statistics. When rules fire, it opens a GitLab issue with a machine-readable block of facts and assigns a **GitLab Duo custom flow**.
- The flow (`stale-remediator`) reads the issue and the claim's playbook and opens one merge request that edits only allowed paths.
- The MR pipeline enforces policy (`verify-mr`: author, labels, paths, no closing keywords), runs SAST, secret detection and container scanning, re-runs the claim's rules inside the built image, and replays pending migrations against a shadow Postgres under write load. Then it sets auto-merge.
- On `main`, a gate decides **release / defer / block**. The release child pipeline migrates, deploys a 0% Cloud Run canary with its own tag URL, probes it (a partner signed by the distrusted root must now be rejected), shifts traffic 50% → 100% under a k6 SLO check, promotes, mints the proof into a lockfile, and closes the issue. Any failure sends 100% of traffic back to the recorded stable revision.

The demo covers two claims: `anchor-trusted` (Node security floor plus the effective CA set, including weak-key detection) and `migration-safe` (blocking DDL on large tables).

## How we built it

- **Engine:** Python 3.12, typer, pydantic v2, `pglast` for SQL parsing, `rfc8785` canonical JSON for fingerprints, `cryptography` for a precomputed weak-EC-key table. 191 pytest tests, 8 of them against real Postgres 18.
- **App:** Node 24 + Express 5. The trust policy runs before any TLS use and filters `tls.getCACertificates('default')` by SPKI hash with `tls.setDefaultCACertificates()`. A partner-sim sidecar serves TLS on localhost with a demo PKI.
- **GitLab:** a lane router in `.gitlab-ci.yml` (control loop, MR, main, manual release), a dynamic child pipeline for releases, GitLab security templates re-staged into a `secure` stage, auto-merge, scoped labels as the state machine, and a Duo custom flow on the Assign trigger.
- **Google Cloud:** Cloud Run (multi-container, revision tags for canaries), Cloud SQL Postgres 18, Artifact Registry, a public GCS bucket for the feeds, Cloud Monitoring for per-revision 5xx, Secret Manager. CI reaches GCP only through GitLab `id_tokens` and Workload Identity Federation; there is no service-account key.

## Challenges we ran into

- `--use-system-ca` *adds* the OS store to Node's roots; it removes nothing. Removing a distrusted root needed a runtime filter on the default CA set.
- A demo root with a deliberately weak key made our own R4 rule fire before any world change, so the baseline could never be VALID. We gave the demo root a normal key and kept R4 tested with a k=4 key.
- "Safe" migrations are relative to table size, which changes without commits. We measure it: the shadow migration records a `pg_locks` timeline while a writer inserts every 10 ms. A plain `CREATE INDEX` on 600k rows held a ShareLock for ~1.4 s; the `CONCURRENTLY` rewrite held none.
- Cloud Run's YAML did not accept the Cloud SQL volume form we expected; the revision annotation worked. Google's front end reserves `/healthz` on public URLs.
- Cloud Monitoring metrics arrive 1–3 minutes late, so the per-revision 5xx check reports "no data" honestly instead of passing silently.
- GitLab rejects custom-flow prompts that contain HTML comments, and our issue format uses them as machine-block markers. The prompt now describes the markers in words.
- Duo flows act with a composite identity: GitLab shows the MR author as whoever triggered the flow, while the commits come from the flow's service account. Our merge policy first rejected a correct fix for that reason. It now checks provenance instead: every commit by a service account, plus a note by the remediator account linking the MR.
- An MR pipeline runs the MR branch's own code, so a rogue MR could weaken its own checks. The decision jobs now load the policy engine and claim files from the target branch.
- The hackathon role could not allow pipeline variables or protect branches, so manual lanes run from pipeline schedules (the lane is the schedule's name).
- Concurrency, found by a fresh end-to-end run: GitLab started two scheduled control loops 21 seconds apart, both opened issues, and four release children interleaved, so one child's deploy replaced the service under another's canary (the rollback did its job). `resource_group` on a job only serializes job by job; we now hold a `release-train` group on the trigger job for the whole child pipeline, serialize the control loop's issue-writing jobs, and play at most one re-release per dispatch run.

## Accomplishments that we're proud of

- The release path works on real Cloud Run: 0% canary with a tag URL, 50% → 100% rings with k6 (isolation checks 802/802), and a forced failure that restored 100% to the stable revision and wrote `rollback-reason.json`.
- The trust fix is verified end to end: with Demo Root G3 excluded, the trusted partner still returns 200 and the distrusted partner returns 502 with a certificate error, without touching the root file.
- A fully hands-off run in 26 minutes: a world shift with no commit → control loop opened issue #4 → Duo opened MR !2 (exactly the two allowed files) → MR pipeline passed and auto-merged → release child ran the canary (distrusted partner now rejected with 502) → 50% → 100% → mint closed the issue. Links are in `docs/demo-evidence.md`.
- Two claims at once: after a Node release, a root distrust and a table growing past 500k rows, the loop opened two issues and two Duo MRs. The first merge's release deferred, because the other claim's fix was still in flight, and the second merge shipped both in one release. That release applied the agent's `CREATE INDEX CONCURRENTLY` rewrite and proved it on the canary.
- Failure path: with a demo switch forcing the canary to fail at 50%, traffic went back to the stable revision at 100% and the claim moved to retry. The next scheduled control loop saw the fix already on main, re-released it, and the claim closed.
- Recovery without a human: when a Duo session dropped on the platform before acting, the next scheduled control loop re-dispatched it.
- The policy holds: a hand-made "remediation" MR that edited `.stale/` was rejected by `verify-mr`, the claim was quarantined, and nothing merged.

## What we learned

- The question "is this still true?" belongs to deterministic code, and the question "how do we fix it?" can be delegated, as long as policy checks and re-proofs gate the result.
- Probes that prove a fix must hit only the new revision (tag URL); ring checks on the live URL can only check SLOs, because most traffic still reaches the old revision.
- Keeping live state in labels and only minted proofs in the lockfile avoids pipelines committing to `main` on every run.

## What's next

- `dependency-trusted` for the axios class (lost provenance, new publisher, new install scripts, very young versions).
- `contract-compatible` for the CrowdStrike class.
- A canary-analyst flow that diagnoses failed releases, a scheduled release train, Terraform for the infrastructure, SCI accounting for skipped re-proofs, and a root feed built from Mozilla OneCRL.

## Built with

GitLab CI/CD · GitLab Duo Agent Platform (custom flows) · GitLab SAST / Secret Detection / Container Scanning · Google Cloud Run · Cloud SQL for PostgreSQL 18 · Artifact Registry · Cloud Storage · Cloud Monitoring · Secret Manager · Workload Identity Federation · Python · typer · pydantic · pglast · psycopg · Node.js 24 · Express 5 · k6 · Kaniko

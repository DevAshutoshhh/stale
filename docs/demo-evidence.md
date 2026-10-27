# Demo evidence

Every link points at the public hackathon project
[`gitlab-ai-hackathon/transcend-october-2026/43668335/showcase`](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase).
Live service: https://ledger-api-ihjeuvjbka-uc.a.run.app/status


## Platform checks (Phase 3/4)

| What | Pipeline |
|---|---|
| WIF smoke: Cloud Run listed with a federated credential, no key file | [2989734232](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2989734232) |
| Manual baseline release: build → scans → gate → migrate → canary → 50/100 → promote → mint | [2989778002](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2989778002) → child [2989794514](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2989794514) |
| Normal push to `main` → no-op release child | [2989869200](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2989869200) → child [2989885412](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2989885412) |
| Forced canary failure (`STALE_DEMO_BREAK_CANARY=1`) → rollback to stable at 100% + `rollback-reason.json` | [2989915340](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2989915340) → child [2989929449](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2989929449) |

## Duo flow (Phase 5)

| What | Link |
|---|---|
| Spike: Assign trigger via API starts the flow; agent posts "spike ok" | issue [#2](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/issues/2) |
| Tuning attempt 1 (scenario A): correct MR, closed before the composite-identity fix | issue [#3](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/issues/3), MR [!1](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/merge_requests/1), control loop [2990136006](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990136006) |

| Policy check: hand-made MR editing `.stale/` → verify-mr REJECTED, claim quarantined, no merge | MR [!3](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/merge_requests/3), issue [#5](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/issues/5), pipeline [2990781844](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990781844) |

## Hands-off runs

### Run 1 — scenario A (anchor-trusted), 2026-10-26, zero human actions

| Time (UTC) | Step | Link |
|---|---|---|
| 16:11 | `world_shift.py scenario-a` (GCS feeds only) + control loop (schedule) | [2990651878](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990651878) |
| 16:15 | `check` opens the claim issue (R1, R3); `dispatch` assigns the remediator flow | issue [#4](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/issues/4) |
| 16:17 | Duo `stale-remediator` opens the MR: `Dockerfile` → `node:24.22.1-bookworm-slim`, `excluded-spki.txt` += Demo Root G3 SPKI; "Related to #4" | MR [!2](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/merge_requests/2) |
| 16:17–16:19 | MR pipeline: validate, mark-reproving, engine/app tests, build, inspect, SAST, secrets, container scanning, security-gate, reprove, verify-mr ("OK: !2 — auto-merge enabled") | [2990677591](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990677591) |
| 16:19 | GitLab auto-merges !2 | merge commit `2e2ea4d` |
| 16:19–16:30 | Main pipeline: build, scans, gate → plan-release = pass | [2990688545](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990688545) |
| 16:30–16:37 | Release child: record-stable, migrate, deploy-canary (0% + tag), canary-reprove (trusted 200; distrusted 502 `UNABLE_TO_VERIFY_LEAF_SIGNATURE`; runtime report Node v24.22.1, no findings), canary-ring 50→100, promote, mint | [2990703930](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990703930) |
| 16:37 | `mint` commits the lockfile (`e26c386`), posts "Re-minted: anchor-trusted is VALID again", closes #4 | issue [#4](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/issues/4) |

After: `/status.json` → revision `ledger-api-p2990703930`, Node v24.22.1, trust policy `removed: 1`, Demo Root G3 not trusted, both claims VALID.


### Run 2 — E2E-1 run 1: scenario A + B together, 2026-10-26

One manual step: MR !4's first pipeline failed in `engine-tests` because a test read the repo's migration file (which !4 legitimately rewrites). The test was fixed on main and !4 was rebased via the GitLab API; the agent's commit and author were unchanged. Everything else was triggered by GitLab.

| Time (UTC) | Step | Link |
|---|---|---|
| 16:44–17:07 | Reset: demo reset commit, world reset, baseline release | [2990836564](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990836564) |
| 17:08 | `scenario-a` + `scenario-b` (GCS feeds + 750k rows + ANALYZE; no commits) | — |
| 17:09–17:13 | Control loop: two issues (R1,R3 and M1,M3), remediator dispatched to both | [2990915556](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990915556), issues [#6](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/issues/6), [#7](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/issues/7) |
| 17:14–17:15 | Duo opens MR [!4](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/merge_requests/4) (migration rewritten to the §10 target shape) and MR [!5](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/merge_requests/5) (Dockerfile + SPKI) | — |
| 17:17 | !5 pipeline passes, auto-merged | [2990942392](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990942392) |
| 17:19–17:21 | Main for !5: gate **DEFER** ("anchor-trusted: pass; migration-safe: defer (REPROVING; M1, M3)") → child `release-deferred` | [2990952424](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990952424) → [2990967263](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990967263) |
| 17:17 | !4 first pipeline: engine-tests failed (test bug, see above) | [2990937762](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990937762) |
| 17:22–17:24 | !4 rebased; pipeline passes; auto-merged | [2990971880](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990971880) |
| 17:24–17:41 | Main for !4: gate **pass** → child: migrate applies the CONCURRENTLY rewrite → canary probes (trusted 200, distrusted 502, runtime Node v24.22.1, migration_applied) → 50→100 → promote → mint closes #6 and #7 | [2990982597](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990982597) → [2990998377](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2990998377) |

After: `/status.json` → `ledger-api-p2990998377`, Node v24.22.1, schema `20261021090000`, no invalid indexes, Demo Root G3 not trusted, both claims VALID.

### Run 3 — E2E-1 run 2: scenario A + B together, 2026-10-26

No manual action on any issue or MR. One code change landed mid-run: the anchor-trusted Duo session failed on the platform ("session 10077411 failed (dropped)") before opening an MR, so `dispatch` gained recovery for failed sessions (pushed 18:11) and the `control-loop` schedule was activated (*/15). The recovery itself was triggered by that schedule.

| Time (UTC) | Step | Link |
|---|---|---|
| 17:43–18:00 | Reset (commit, world reset) + baseline release | [2991059514](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991059514) |
| 18:01 | `scenario-a` + `scenario-b` (no commits) | — |
| 18:02–18:05 | Control loop: issues [#8](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/issues/8) (anchor) and [#9](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/issues/9) (migration), both dispatched | [2991117745](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991117745) |
| 18:06 | #8's Duo session failed (dropped) on the platform; #9's session opened MR [!6](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/merge_requests/6) | — |
| 18:09 | !6 pipeline passes, auto-merged | [2991139177](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991139177) |
| 18:13 | Main for !6: gate **DEFER** → child `release-deferred` | [2991149312](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991149312) → [2991159114](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991159114) |
| 18:25 | Scheduled control loop: `dispatch` "#8 anchor-trusted: session failed, re-dispatched (1/3)" → new session opens MR [!7](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/merge_requests/7) | [2991186108](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991186108) |
| 18:29 | !7 pipeline passes, auto-merged | [2991204073](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991204073) |
| 18:29–18:46 | Main for !7: gate **pass** → one release ships both → mint closes #8 and #9 | [2991211266](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991211266) → [2991221616](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991221616) |

After: `/status.json` → `ledger-api-p2991221616`, Node v24.22.1, schema `20261021090000`, no invalid indexes, Demo Root G3 not trusted, both claims VALID.

### Run 4 — E2E-2: forced canary failure → rollback → retry → success, 2026-10-26

Operator actions: the demo switch `STALE_DEMO_BREAK_CANARY=1` before the release and its removal after the first rollback. Everything else was started by the `control-loop` schedule (*/15). The observer's network dropped for ~1 h during this run; GitLab carried on.

| Time (UTC) | Step | Link |
|---|---|---|
| 18:47–19:05 | Reset + baseline release | [2991268716](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991268716) |
| 19:05 | Demo switch on; `scenario-a` (no commit) | — |
| 19:19 | Scheduled control loop failed in `check`: the runner could not clone the repo (GitLab CI gateway unreachable) — infrastructure, not Stale | [2991372059](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991372059) |
| 19:35 | Next scheduled control loop: issue [#10](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/issues/10) opened and dispatched | [2991421705](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991421705) |
| 19:38–19:41 | Duo opens MR [!8](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/merge_requests/8); pipeline passes; auto-merged | [2991435212](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991435212) |
| 19:41–20:0x | Main for !8 → release child: canary ring fails at 50% (demo switch) → `.post` rollback → 100% stable → `rollback-transition`: #10 `retry-pending`, `stale-attempt::2` | [2991441266](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991441266) |
| 20:04 | Operator: demo switch off | — |
| 20:07 | Scheduled control loop: `dispatch` → "Retry 2: the remediation (!8) is already on main, so the retry is a new release" → plays `release` | [2991521005](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991521005) |
| 20:11–20:27 | Re-release: canary → probes → 50→100 → promote → mint closes #10 | [2991532793](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2991532793) |

After: `/status.json` → `ledger-api-p2991542606`, Node v24.22.1, Demo Root G3 not trusted, both claims VALID.

### Run 5 — E2E-1 attempt that found a concurrency bug, 2026-10-27

GitLab started two `control-loop` schedule pipelines 21 s apart ([2992265457](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992265457), [2992266146](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992266146)); both opened issues (#11/#13, #12/#14), the remediator opened four correct MRs (!9–!12), and four release children interleaved. Child [2992283765](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992283765) lost its canary tag to another child's deploy, `canary-reprove` got 404, and `rollback` restored the stable revision at 100%. The next control loop re-released; [2992336196](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992336196) succeeded and mint closed all four issues. Fixed in `faa3e54` (resource groups on the control loop and on the release trigger; one re-release per dispatch run).

### Run 6 — E2E-1, scenario A + B, after the concurrency fix (used for the video), 2026-10-27

No human action on any issue or merge request. One code push landed mid-run (see 03:25).

| Time (UTC) | Step | Link |
|---|---|---|
| 02:44–03:03 | Reset commit `43d30cf`, world reset, baseline release → `ledger-api-p2992518214` (Node v24.22.0, Demo Root G3 trusted, both VALID) | [2992513853](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992513853) → [2992518214](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992518214) |
| 03:04 | `scenario-a` + `scenario-b` (GCS feeds, 850,004 rows); `main` still at `3b528db` | — |
| 03:06–03:08 | Control loop: `check` opened [#15](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/issues/15) (R1, R3) and [#16](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/issues/16) (M1, M3); `dispatch` assigned the remediator. The scheduled loop at 03:08 ran after it (serialized) and opened nothing. | [2992551444](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992551444), [2992562008](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992562008) |
| 03:09–03:10 | Duo sessions 10100062 (#15, 67 s) and #16's session open MR [!13](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/merge_requests/13) (Dockerfile + SPKI) and [!14](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/merge_requests/14) (`CREATE INDEX CONCURRENTLY` rewrite) | — |
| 03:10–03:13 | Both MR pipelines pass (verify-mr enables auto-merge); !14 auto-merges | [2992565616](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992565616), [2992565659](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992565659) |
| 03:13–03:17 | Main for !14: gate **DEFER** → child `release-deferred` | [2992570622](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992570622) → [2992577746](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992577746) |
| 03:13–03:25 | !13's auto-merge stalled (green pipeline, mergeable, still open). A code push to `main` at 03:25:37 (`96b8c3b`, which makes `dispatch` merge such MRs) made GitLab re-check, and the auto-merge completed at 03:25:40 as the bot user that set it. | [!13](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/merge_requests/13) |
| 03:25–03:42 | Main for !13: gate **pass** → release child: migrate (CIC) → canary 0% + tag → probes (trusted 200, distrusted 502, Node v24.22.1, migration applied) → 50% → 100% → promote → mint closes #15 and #16 | [2992595896](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992595896) → [2992600961](https://gitlab.com/gitlab-ai-hackathon/transcend-october-2026/43668335/showcase/-/pipelines/2992600961) |

After: `/status.json` → `ledger-api-p2992600961`, Node v24.22.1, schema `20261021090000`, no invalid indexes, Demo Root G3 not trusted, both claims VALID. World shift to both issues closed: 38 min.

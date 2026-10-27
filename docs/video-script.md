# Demo video script — 2:51

Narration is first person and AI-generated (Microsoft Edge neural voice `en-US-AndrewNeural` via `edge-tts`;
Devpost allows AI narration). Every screen is a real page or real output: the live `/status`, public GitLab
pages of the showcase project, the Duo session's tool log read from the GitLab GraphQL API (the AI → Sessions
page needs sign-in), and a terminal replay of the real `world_shift.py` output. Three views (the claim issue, the
MR diff, the mint note) are rendered from the GitLab REST API with GitLab's markdown renderer, because
anonymous GraphQL-backed pages were rate-limited for the recording IP; they are labelled on screen. Waiting time is edited out
and marked on screen. Captions are burned in. No music.

| Time | On screen | Narration |
|---|---|---|
| 0:00–0:04 | Title card | — |
| 0:04–0:32 | Four incident cards: CrowdStrike Channel File 291, Railway, axios on npm, eMudhra + Node 24.22.1 | "In July 2024, a content update everyone assumed was compatible crashed about eight and a half million Windows machines. In October 2025, an index build approved when a table was small took Railway's API down for half an hour. In March 2026, malicious axios versions reached npm. And last week, a root certificate was distrusted while services kept trusting it. Each one was a safety verdict nobody re-checked." |
| 0:32–0:56 | `/status` at baseline (both VALID, Node v24.22.0, Demo Root G3 trusted); `.stale/claims/anchor-trusted.yaml` with the assertion, rules and allowPaths highlighted | "So I built Stale. It treats every safe-to-ship verdict as a cached value, with evidence behind it. This is the live service. A claim like anchor-trusted says what must stay true: the serving Node is patched for its line, and no distrusted root is in the trust store. Each claim is minted by a release, with a fingerprint of its evidence. Right now, both claims are valid." |
| 0:56–1:12 | Terminal: `git log` → `world_shift.py scenario-a` → `scenario-b` → `git log` (same commit) | "Now the world changes. Node ships a security release, a root program distrusts a certificate, and a table grows past half a million rows. This script writes only to a feed bucket and the database. The commit list doesn't move." |
| 1:12–1:32 | `/status` "What changed in the world"; control-loop pipeline; `check` log (R1, R3 / M1, M3); claim issue with evidence table and machine block; `dispatch` log | "Every fifteen minutes, a scheduled control loop collects evidence from the running service and the live database. Deterministic rules fire, and it opens one issue per claim, with a machine block holding the exact facts a fix needs. The rules are plain code, not a model. Then the loop assigns my GitLab Duo flow." |
| 1:32–1:59 | Duo session tool log; MR diff (Dockerfile + `excluded-spki.txt`); `verify-mr` "auto-merge enabled"; MR pipeline (scans, reprove); shadow-migration log; merged | "The Duo flow reads the issue and the playbook, and opens one merge request. Here it bumps the Node image and excludes the distrusted root. It may only touch the files the claim allows. Verify M R checks that, and would quarantine the claim if not. The pipeline runs the security scans, re-proves the claim inside the built image, and replays the migration on a shadow database under write load. Then it auto-merges." |
| 1:59–2:29 | `release-deferred` log (Gate: DEFER); release child pipeline; `canary-reprove` log (trusted 200, distrusted 502 `UNABLE_TO_VERIFY_LEAF_SIGNATURE`); `canary-ring` 50 → 100; E2E-2 `rollback` log | "On main, a gate decides: release, defer, or block. The first fix defers, because the other claim is still in flight. The second ships both. The canary starts with no traffic and its own URL. The partner signed by the distrusted root is now rejected, and the trusted one still works. Then traffic goes to fifty, then a hundred percent, with load checks and error rates gating each step. If any check fails, traffic goes straight back to the stable revision." |
| 2:29–2:46 | Closed issue with "Re-minted … VALID again"; `/status` after (Node v24.22.1, G3 not trusted, claim history, rollout 100%); README stages table | "Mint re-proves both claims on the new revision, commits the proof, and closes the issues. The status page now shows the new revision, and each claim's path from issue to mint. No human touched the issues or the merge requests. Stale: re-verify what you already shipped." |
| 2:46–2:51 | End card: tagline, repo and live URLs | — |

## How it is produced

Tooling lives outside the repo (`~/stale-video/tools`): Playwright drives Chrome at 1280×720 CSS / 1.5× and
captures DevTools screencast frames (1920×1080); lower-thirds and highlight boxes are injected into the page;
`assemble.py` builds the cut with ffmpeg (0.4 s crossfades, narration per scene, burned-in captions, loudness
normalised). Shot IDs come from the E2E run listed in `docs/demo-evidence.md`.

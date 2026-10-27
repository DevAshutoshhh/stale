# Playbook: anchor-trusted

Use this playbook when a claim issue for `anchor-trusted` is assigned to you. The fix is at most
**two file edits**. Do nothing else.

## 1. Read the facts

The issue body has a JSON block between `<!-- stale:machine:begin -->` and
`<!-- stale:machine:end -->`. Its `facts` object can contain:

| Key | Meaning | Example |
|---|---|---|
| `node_floor` | Lowest patched Node release for this major line | `v24.22.1` |
| `node_image_tag` | Docker Hub tag to use for that release | `24.22.1-bookworm-slim` |
| `add_excluded_spki` | SHA-256 SPKI hashes (64 lowercase hex chars) of root CAs to remove from trust | `["3eab3fba…"]` |

A key that is missing means that edit is not needed. Use the values exactly as given. Never invent,
shorten or look up a version, tag or hash.

## 2. Edits

### Edit A — only if `facts.node_image_tag` is present

File: `app/ledger-api/Dockerfile`. Change the value of the `ARG NODE_IMAGE=` line to
`node:<facts.node_image_tag>`. Change **nothing else** in the Dockerfile.

Before:

```dockerfile
ARG NODE_IMAGE=node:24.22.0-bookworm-slim
```

After (with `node_image_tag` = `24.22.1-bookworm-slim`):

```dockerfile
ARG NODE_IMAGE=node:24.22.1-bookworm-slim
```

### Edit B — only if `facts.add_excluded_spki` is present

File: `app/ledger-api/deploy/trust/excluded-spki.txt`. For each hash in `facts.add_excluded_spki`
that is not already a line in the file, append it as a new line. Keep existing lines. The file must
end with a newline.

Before (baseline, empty file):

```text
```

After (with `add_excluded_spki` = `["3eab3fba…<64 hex chars>"]`):

```text
3eab3fba…<the full 64 hex chars>
```

The runtime trust policy (`src/trust-policy.cjs`) reads this file at startup and removes those
roots from Node's default CA set.

## 3. Forbidden edits

- Do **not** edit `app/ledger-api/deploy/trust/extra-roots.pem`. The root stays in that file on
  purpose: the canary proves the runtime filter removes it.
- Do **not** add or change `NODE_OPTIONS`, and never use `--use-system-ca` or `--use-openssl-ca`.
  They do not remove roots.
- Do **not** pass a `ca:` option anywhere in application code.
- Do **not** edit anything under `.stale/`, `ci/`, `duo/`, `.gitlab-ci.yml`, or `app/ledger-api/src/`.
- Do **not** edit any file outside `allowPaths`:
  `app/ledger-api/Dockerfile`, `app/ledger-api/deploy/trust/**`.

## 4. Merge request

- Branch: `stale/anchor-trusted/<issue-iid>-a<attempt>`
- Commit message: `fix(stale): remediate anchor-trusted`
- Title: `fix(stale): remediate anchor-trusted (attempt <n>)`
- Labels: `stale-remediation`, `stale-claim::anchor-trusted` (plus `stale-retry` when attempt > 1)
- Description: `Related to #<issue-iid>`, then one bullet per changed file saying why. Never write a
  closing keyword (close, fix, resolve and their forms) next to an issue reference.

If an analyst note (first line `[stale:analyst]`) exists on the issue, apply its instruction
as long as it stays inside `allowPaths`.

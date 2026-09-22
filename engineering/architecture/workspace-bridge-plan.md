# Workspace bridge: copy what the agent works on

Status: agreed with the maintainer 2026-09-22 ("ok start"). Found by P7 of
the [production proving plan](production-proving-plan.md).

## What was measured

Before every run's first sandbox command, the bridge copies the agent's
workspace into the sandbox and checks each file against the policy. On the
steward's server that was about 1,000 files: two clones of the repository
(470 and 468 files) that a worker had cloned inside the bridged folder and
the bridge copied back, and every earlier background run's own records
(`events.jsonl`, `run.json`). Until T3 of the telemetry storage plan, each
file's check was also recorded twice in the trace.

## What stays true

The bridge's promise: the files in the workspace are there when a command
runs. Narrowing it to "files this run touched" would silently leave out a
data file a script reads, so it is not done.

## Units

- **W1. A git checkout does not come back.** A folder the sandbox created
  that contains `.git` is a checkout, not the agent's output: it is not
  copied back, and the model is told why (clone outside the workspace).
- **W2. The runtime's own records do not go in.** The background layer's
  run records are not copied into a sandbox.
- **W3. Include and exclude patterns.** The bridge takes glob patterns for
  what to copy in either direction; the default copies everything, as now.
- **W4. The steward's workspace cleaned.** The two clones are removed on the
  server (an operation, not code).

Each code unit: a test that fails first, the fix, the suite as CI runs it,
a PR.

## Execution log

| Unit | Status | Commit | Notes |
| --- | --- | --- | --- |
| W4 | Done | — | 2026-09-22 on the server: the two clones (468 and 470 files, 19 MB) removed; the steward's workspace went from about 1,030 files to 89 (1.1 MB). |
| W1 | Done | `b99eafd` | The listing reports each nested `.git`; the files under that folder are not copied back and the model is told to clone outside the workspace. A `.git` at the workspace's top is not a checkout. |
| W2 | Done | `1cea3d2` | `run.json` and `events.jsonl` in a `run_*` folder are not copied in, and a command cannot write one back; what an agent writes in a run's folder is copied as before. |
| W3 | Done | `636201a` | `governance_config.workspace_bridge`: `include` and `exclude` globs, both directions, validated at startup; the default copies everything. Documented in the execution page. |

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

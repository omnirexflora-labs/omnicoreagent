# Fixes from the 0.5.0rc5 release-candidate gate

The rc5 gate ran the six areas against the built wheel, each finding rated
BLOCKER / MUST-FIX / LATER. Durability (area C) and control held; these are
the BLOCKER and MUST-FIX findings. Each unit is test-first and one commit.

## Units

- V1 (blocker) A model call the provider rejected reads `error` with its
  reason: the error event was not linked to its call, so the trajectory read
  `no_response`, "never made" (area E).
- V2 (must fix; rc4's U12 did not hold) Closing an SSE stream cancels its
  run through a real server: the server cancels the stream generator, and
  that cancellation hit the first await of its cleanup, before the run was
  cancelled. Every task is now cancelled synchronously first. Checked with a
  real server test and live with a slow tool (area F).

- V3 (blocker) A budget pause in a turn of parallel calls keeps the calls
  that ran, and a call refused before its tool ran is recorded `not_run` and
  runs on resume; raised at once, the pause cancelled the other calls and
  dropped the results of those that ran, and the refused call read as an
  unknown outcome that never ran (area B).
- V4 Two refusals of one budget in a turn make one request whose shortfall
  adds up, so one grant covers them; each made its own, and the documented
  grant-then-resume failed (area B).

- V5 (blocker) A timed-out or cancelled call is told to the model as
  possibly having taken effect (check before calling again), not as failed: a
  synchronous tool's thread cannot be stopped, and a card "failed due to a
  timeout" was charged (area E).

- V6 An absolute path under the workspace files root names that file, for
  storage and policy alike (Harbor: `/app/ssl/x` was written to
  `/app/app/ssl/x`); an absolute path elsewhere stays inside the workspace
  as before (area E).

- V7 A headless run with no run record (no model key) writes its trajectory
  in the run's shape, one segment; the docs' read_run.py failed on it (area E).

- V8 (security) The generated `.dockerignore` excludes `.env` and
  `workspace` in every folder (`**/`): an agent in a subfolder had its
  `.env` baked into the image. Confirmed with a real build (area D).

- V9 A Docker sandbox that dies mid-command is reported lost and the next
  command gets a fresh one, as execution.mdx says: it read as a timeout and
  every later command failed against the dead container (area D).

- V10 A workspace link that leads out of the workspace is skipped, not fatal:
  a folder link failed every execute before its command ran, and a file link
  the command wrote turned its output into an error (area D).

- V11 (security-relevant) The approval summary shows each command as
  written, with its variable settings and redirects, control characters made
  visible: rebuilt from its arguments it hid `>> ~/.ssh/authorized_keys` and
  `GIT_SSH_COMMAND=...`, and quoted `~` as a folder name (area B).

- V12 The sandbox-reset notice after a resume says it is the runtime's note,
  not the user's, that the resumed calls ran after the reset, and to carry on
  with the task: worded as news, the model answered it and the run's answer
  was lost (area B).


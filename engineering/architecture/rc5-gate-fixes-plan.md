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


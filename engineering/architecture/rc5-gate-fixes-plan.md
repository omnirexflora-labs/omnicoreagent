# Fixes from the 0.5.0rc5 release-candidate gate

The rc5 gate ran the six areas against the built wheel, each finding rated
BLOCKER / MUST-FIX / LATER. Durability (area C) and control held; these are
the BLOCKER and MUST-FIX findings. Each unit is test-first and one commit.

## Units

- V1 (blocker) A model call the provider rejected reads `error` with its
  reason: the error event was not linked to its call, so the trajectory read
  `no_response`, "never made" (area E).

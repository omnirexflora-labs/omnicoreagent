# Fixes from the 0.5.0rc6 release-candidate gate

The rc6 gate ran the six areas, each finding rated BLOCKER / MUST-FIX / LATER.
Area F came back READY; these are the BLOCKER and MUST-FIX findings of the
others. Each unit is test-first and one commit.

## Units

- W1 (blocker) An absolute path outside the workspace is refused, as the docs
  say: taken as relative, /tmp/x was written to <root>/tmp/x and the record
  named /tmp/x. "/" alone and "/files/..." still mean the workspace (area E).

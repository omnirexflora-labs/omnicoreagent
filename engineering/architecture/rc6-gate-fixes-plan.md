# Fixes from the 0.5.0rc6 release-candidate gate

The rc6 gate ran the six areas, each finding rated BLOCKER / MUST-FIX / LATER.
Area F came back READY; these are the BLOCKER and MUST-FIX findings of the
others. Each unit is test-first and one commit.

## Units

- W1 (blocker) An absolute path outside the workspace is refused, as the docs
  say: taken as relative, /tmp/x was written to <root>/tmp/x and the record
  named /tmp/x. "/" alone and "/files/..." still mean the workspace (area E).
- W2 (blocker, security) grep, glob and ls consult the read policy for each
  file: checked on the folder searched only, grep returned the contents of a
  file a read rule protected, and glob and ls its name (area D).

- W3 (blocker, security) A redirect after a list, pipeline or group applies
  to the commands it reaches: carried onto single commands only, it was
  dropped, so the approver read `echo k` for `echo k >> ~/.ssh/authorized_keys`
  and a read-only allow rule let the write through (area B).

- W4 A misspelt `exclude_capability` value is refused: it excluded nothing,
  so `*` minus `proces.exec` allowed process.exec (area B).

- W5 Re-registering a task a policy change paused clears that pause, so its
  schedule runs again; a pause a person made stays. V16 re-bound the task but
  left the schedule paused for good (areas A and C).

- W6 A budget request counts each refused call once, keyed by the call: a
  recovery refused the same not-run calls again and doubled the shortfall, so
  the default grant allowed twice what was meant (area C; a regression from
  rc5's V4).

- W7 After a client hangs up, the request trace ends too: its finish ran
  inside the stream's cancelled scope, where every await is interrupted, and a
  stream paused at a yield never ran its cleanup at all. The disconnect
  watcher is its own task and ends the trace; the stream hands the finish to
  a task of its own. Verified live with the tester's reproduction (CLI,
  timeout off, real model; async tool, sync tool, mid-answer): both traces end
  cancelled. The real-server test covers the run and traces, but did not
  reproduce this failure on its own (area A).


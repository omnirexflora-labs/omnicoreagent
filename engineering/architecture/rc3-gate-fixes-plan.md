# Fixes from the 0.5.0rc3 release-candidate gate

The rc3 gate ran the same six areas against the built wheel. Each unit is
test-first and one commit; 0.5.0 is built and gated again after these land,
and published only when a gate finds nothing to fix.

## Units

- T1 A call a budget stopped is not a model error: it was recorded as
  `model_error` with a Python stack, so a client alerting on model errors
  paged at every ordinary budget pause, and the call read `error`, not the
  documented `no_response` (area F).
- T2 A governed agent's unknown-outcome result carries no argument values,
  as every governed tool result: the rc2 fix (S13) put them back; an
  ungoverned agent's keeps them. (Area F's F-7, an empty `args` on a refused
  call, is this same rule, by design.)

- T3 A command refused inside `execute` reads `denied` in the trace, as in
  the run record; S4 changed only the record, and the two disagreed (area B).

- T4 A second call of the same turn that asks the same question (two
  commands that both need the sandbox network) waits on the one pending
  approval and is replayed on resume; it was refused and never run (area B).

- T5 The `omnicoreagent` console script notes Ctrl-C before importing the
  CLI (1-4 s under load): a Ctrl-C in that window gave a traceback and exit
  130, or was swallowed in the import machinery and the run went ahead
  (area E).

- T6 The model client and token encoding load on a daemon thread: a run
  stopped while they loaded held the process 43-74 s at exit for an import
  nobody needed (area E).

- T7 A run stopped during the warm-up keeps its trace on the record: it said
  cancelled or timeout with `trace_ids: []`, and its trajectory had no
  segment (area E).

- T9 A background run that succeeded on a retry no longer carries the
  earlier attempt's error (the attempt keeps it); the docs say the trace
  window also applies when a process first reads its traces (area C).

- T10 OmniServe's socket listens, not only binds, and the CLI holds the
  port it checked while the agent loads: a second server starting meanwhile
  took the port too, and the first crashed after its startup (area A; S7
  was incomplete).

- T11 Rules appended to a policy object after it was built are checked when
  the agent is built (bucket and unique id); a deny rule in the allow bucket
  allowed (area A).

- T12 The unknown-manifest-field error lists only settable fields (area A).
- T13 A summary slower than 60 s falls back to the recent messages, as a
  failed one does; the history load's own limit (90 s, the store) raises a
  TimeoutError saying what timed out. It raised a bare TimeoutError out of
  `run()` after a fixed 20 s that covered the summary's model call (area A).


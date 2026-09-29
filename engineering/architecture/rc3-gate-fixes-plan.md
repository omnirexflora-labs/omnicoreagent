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


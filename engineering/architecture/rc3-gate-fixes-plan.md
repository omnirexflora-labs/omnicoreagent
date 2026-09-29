# Fixes from the 0.5.0rc3 release-candidate gate

The rc3 gate ran the same six areas against the built wheel. Each unit is
test-first and one commit; 0.5.0 is built and gated again after these land,
and published only when a gate finds nothing to fix.

## Units

- T1 A call a budget stopped is not a model error: it was recorded as
  `model_error` with a Python stack, so a client alerting on model errors
  paged at every ordinary budget pause, and the call read `error`, not the
  documented `no_response` (area F).

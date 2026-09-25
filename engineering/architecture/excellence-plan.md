# Excellence: the harness, the CLI, the README and the docs

Status: asked for by the maintainer 2026-09-25 ("i want anyone testing this
testing any part of omnicoreagent and be wow … i want our readme to be perfect i
want our docs to be superb"). Excellence here means something a stranger can
check: a score that moves on a real task, a README whose every command works as
written, a CLI that holds up on a first try, docs with no claim the code does
not keep.

## Where we start

A real, hard, private Harbor task scored **0**, with **66 of 83** verifier tests passing, 13 of 60
steps, $0.18. The runtime did its part — the full specification reached the
model through the artifact offload — and the agent did not: it stopped early,
generalized one passing negative test into a claim about all invalid input, and
missed parts of the task's core logic. The harness gave it three lines of guidance.

## Units

Each: failing test first, the full suite on the server, a commit, a PR. A unit
that changes how the agent works is also measured on the real task and on the
three tasks of our own, which must keep passing.

- **X1. A harness that finishes honestly.**
  - *Completion review*, a runtime option (`completion_review`, rounds, default
    off): when the model gives a final answer, the runtime asks it once to map
    each requirement to the command that showed it holds and to go on working
    where it cannot; the loop continues in the same run and trace, and the
    request is a recorded runtime message.
  - *An engineering prompt* for the Harbor agent: read the whole specification
    first, check each rule with a case of its own, keep working while steps
    remain, and never report a check that was not run.
  - Measured: that task before (0; 66/83) and after, same model and caps.
  - First measurement, one trial each (2026-09-25): reward 0 both; 66/83 before,
    65/83 after, 13 steps and $0.18 before, 22 steps and $0.34 after. The review
    fired as designed — the model said "done" at step 12 without having run a
    check, and the review turned that into nine more steps that wrote and ran its
    own checks and ended with a requirement-by-requirement record — but the 16
    persistent failures are in the task's core logic, where the model's own
    checks shared its misreading of the contract. One trial per arm cannot tell
    a small gain from noise; five per arm are next. Our own tasks: house-report
    passed; the other two never started (the server's Docker address pools were
    exhausted by stale networks, pruned since).
  - Five trials per arm (2026-09-25), same model, caps and task, run side by
    side: reward 0 in all ten. Verifier checks passed 65.2 on average before
    (63–69) and 66.6 after (64–70): +1.4, within the ±3 one trial varies by.
    Steps about 10 before and 25 after; cost $0.16 and $0.33 a trial. The
    agent checks and reports more; its checks share its reading of the task's
    core logic, where every trial failed. The prompt and the review changed
    together, so this cannot say which one cost the extra steps.
  - Decision: `completion_review` stays in the runtime and is **off by default
    in Harbor trials** (`--agent-kwarg completion_review=1` turns it on); the
    engineering prompt stays.
- **X2. The CLI on a first try.** A clean container, a fresh install, every
  command and flag run as a stranger would: help text, errors, exit codes.
  Findings fixed, and the walk kept as a test.
- **X3. The README, executed.** Every command and code block in the README run
  in a clean container, every claim traced to code; rewritten so the first
  screen shows what is different about this runtime and the first five
  minutes work.
- **X4. The docs, executed.** The same audit for every docs page: examples run,
  APIs exist, links resolve; held by tests where a claim can be.
- **X5. The trajectory's last gap.** A run with `capture="full"` still reports
  `evidence_status: partial`; find why and close it, or say in the trace what
  is left out and why.
  *Measured 2026-09-25:* a default agent with full capture now reports
  `complete`; `partial` remains only where something was really redacted or
  truncated. One false positive is left: a tool with a parameter named like a
  secret (`api_key`) has that parameter's schema (`{"type": "string"}`)
  redacted in the `context_tools` event by `redact_keys`, which marks the run
  partial. Fix: redact values, not JSON-schema property definitions.

## Cost

Model spend only in X1's measured runs (about $0.20–0.50 a trial on
`gpt-5.6-terra`) and in any docs example that calls a model. Everything else is
local or on the server's CPU.

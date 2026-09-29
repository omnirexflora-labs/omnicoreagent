# Plan: what the 0.5.0rc1 gate found

Nothing is published until a locally built release candidate passes a full end-to-end gate (the maintainer,
2026-09-29). The rc1 gate (engineering/validation/release-candidate-0.5.0rc1.md) ran the built wheel in six areas
with the real model and real services. Every core guarantee held; these did not, and are fixed here, each test
first with the gate's own reproduction. Docs errors that do not depend on these changes are fixed on a separate
branch (docs/rc1-gate). Then rc2 is built and the whole gate runs again.

## Blockers
- R1 The first model call imports LiteLLM on the event loop: import it off the loop, and warm it when a run starts.
- R2 Budgets are applied to a copy of the caller's policy, never the caller's object.
- R3 An auto-discovered policy file narrows the profile: it keeps the profile's allow rules.
- R4 The sandbox bridge's own commands are the runtime's, not the agent's: command rules do not judge them.
- R5 Approvals returned by run() and by OmniServe carry the command and the decision.
- R6 `postgresql://` works on a fresh install.
- R7 `including_subagents` counts sub-agents of every segment, suspended ones included.
- R8 `training_records` builds a resumed run from all of its traces when its record is gone.

## Must fix
- R9 The `local` provider refuses a host command before asking about the network.
- R10 A budget request of an abandoned run cannot be granted.
- R11 A permissive fall-through says no rule matched.
- R12 OmniServe lists runs by status and session.
- R13 A crash's orphaned sandbox is removed when its run resumes or is abandoned; the sweep can be scoped.
- R14 Policy load errors say why, at build; unknown command keys and sandbox options are refused at build.
- R15 Headless Ctrl-C interrupts the run, writes the evidence, exits 6.
- R16 Retention counts, payload retention time zone, OTLP timeouts, MCP timeout noise, denied approval status.

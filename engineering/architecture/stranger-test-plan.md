# Stranger test, 2026-09-26: what four apps built from the docs alone found

Four agents, each given only the README, the docs and the cookbook and the
0.4.0 release-candidate wheel (built from main at `59ff03e`), built four
applications: a refund desk (approvals, durable runs across processes,
OmniServe), a sandboxed code runner (Docker, network policy, code mode,
budgets, privacy), a research team (MCP, sub-agents, skills, AGENTS.md,
streaming) and a nightly reporter (background tasks, headless CLI,
telemetry, offload, the upgrade guide). All four apps work in the end; 13
of 25 features worked first try. Findings are verified before they are
fixed; the reproductions are in each unit's test.

## Units, in order

- **S1. A resumed run's evidence is whole.** Verified: the call a person
  approved runs on resume but is listed in `tool_calls_outside_steps`, so every
  documented loop over `steps` misses it; `training_records()` reports it as
  `awaiting_approval`; its policy decisions read `ask`, then
  `allow/matched_allow`, as if a rule allowed it; `get_run_trajectory` totals
  `including_subagents` hold only the last segment (12 tokens of 24).
  Fix: the approved call belongs to the step that asked for it (or a step of
  its own on resume), the approval appears as the decision (approver,
  approval id), the training record takes the executed call, totals sum every
  segment.
- **S2. A second process can see what it approves.** Verified: `get_run()`
  approvals carry `arguments_digest` but not `arguments`; HTTP has them.
- **S3. Full capture records what it says it records.** Reported: tool
  arguments `[REDACTED]` at `capture="full"` under governance; calls inside a
  `run_code` program have no arguments; a model call a budget stopped has no
  `facts`; a call paused for a network ask is counted as `error`. Verify each.
- **S4. Governance says no, or asks, the way the docs say.** Reported: a
  capability no rule matches returns "Unknown capability requires approval" as
  a tool error and the run ends `success` (docs: an ask pauses); an MCP server
  start matching an ask raises at connect; a Docker host allowlist is accepted
  at build and refused only after a person approved it; a path validation
  error is reported as a policy denial. Verify each.
- **S5. A background run's deadline holds.** Reported once, not reproduced:
  `timeout_seconds=60` ended at 188 s and the run stayed `running`.
  `run_with_timeout` waits without limit for a cancelled run to finish;
  investigate, bound the wait. Also: a `RuntimeWarning: coroutine ... never
  awaited` from LiteLLM's responses bridge on every run.
- **S6. The docs tell a stranger what they needed.** Wrong: `call_sub_agent`
  (is `delegate_<name>`), `agent.get_history` (is `get_session_history`), the
  `spawn_subagents` argument shape, the configuration page's provider list,
  one description shared by `request_limit` and `total_tokens_limit`.
  Missing: the capability names, rule and target syntax and precedence (the
  security model page promises them); SQLite for memory and task stores and
  its extra; the trajectory's fields (`governance`, approvals, statuses,
  `facts`); `run()` status values next to record statuses; eight OmniServe
  routes (the table should be checked against the API document by a test);
  the `BackgroundAgentManager` reference (generated, like the others) and its
  run statuses; skill authoring and `run_skill_script`; a Python MCP server;
  the JSONL exporter; the network allowlist syntax and which providers enforce
  it; `grant_budget`'s default amount; cross-process resume needing the same
  workspace for traces; a cookbook example using private attributes.

## Status

- S1 done: the approved call runs in a `resumed` step carrying the paused
  step's number (steps number by the run, not by segment); its decision is
  `allow` / `approved` with `approved_by` and `approval_id`; the story's
  `including_subagents` sums every segment; training records keep the
  resumed step's calls.
- S2 done: `get_run()` approvals carry `arguments`; the runtime reads the
  stored record through `_run_record`, so nothing extra is written back.
- S3 done: a result's `args` follow the recorder's rule (kept at full
  capture); calls from a program take their arguments from the execution
  record; every model call entry has every field (`no_response` when it
  never answered); a call that asked for authority while running is
  `awaiting_approval`, a refusal raised while running is `denied`.
- S4 done: the explicit delegate's spawn request names its call, so an ask
  pauses the lead and a resume runs the child (it was an orphaned approval
  and a tool error); a host allowlist on docker, e2b, vercel or local is
  refused when the agent is built. Rule precedence (deny, ask, allow, then
  the mode's default) and connect-time MCP asks are for S6's docs. Left: a
  path validation error reported as a policy denial.
- S5 done: `run_with_timeout` gives a cancelled run 15 s to stop, then
  raises on time and leaves the work behind (logged). Not reproduced: the
  LiteLLM "never awaited" warning (no warning on a plain run with 1.101 or
  1.102). Noted: ResourceWarnings at cleanup (trace file, a transport),
  visible only with warnings enabled.

- S6 done (docs, stacked on S1-S5):
  - Generated references: `reference/policy` (every capability from the new
    `CAPABILITIES` registry, how a request is decided, rule syntax, every
    built-in profile's rules) and `reference/background` (every
    `BackgroundAgentManager` method, now documented, run statuses and
    overlap policies). Tests keep both complete.
  - OmniServe's route tables list every route; a test checks them against
    the API document.
  - Corrected: `delegate_<name>` (not `call_sub_agent`),
    `get_session_history`, the `spawn_subagents` shape, the sandbox provider
    list, the `request_limit` / `total_tokens_limit` descriptions.
  - Added: `run()` and record status vocabularies; cross-process resume needs
    the same workspace; the trajectory's fields at a glance (and a loop that
    survives a call with no response); SQLite and its extra; the network
    allowlist syntax and which providers enforce it; `grant_budget`'s default
    amount; skill authoring; a Python MCP server and trusting a server under
    governance; the JSONL exporter; the first Docker run pulling its image.
  - Found while documenting: `budget_status` ignored grants that enforcement
    counts; fixed with a test. The offload cookbook example used private
    attributes; it reads the run's trajectory now.
  - Left: model names vary across examples; the headless CLI's runs report
    `execution_surface: "interactive"`.

## Round two (2026-09-26, on main after #285)

Two more agents, new apps: an ops copilot served over HTTP (strict dict
policy, SSE, approvals, budgets, background tasks, steering, evidence) and a
code review bot (Docker, a skill, spawned workers, the headless CLI,
outcomes and export, the upgrade guide). 12 of 12 features worked; 8 first
try, none failed. Fixed, each verified first and tested:

- A per-request grant vanished from `budget_status` when the run finished
  (its counter was removed): `settle` keeps it on the record, in the same
  read. `GET /runs/{id}/budget` returns the run's budget `requests`.
- A policy refusing an operator's HTTP request (a background task) was a
  500; it is a 403 with the reason.
- The trajectory's decisions name their `matched_rule_ids`; an approved
  call's `approval_id` is the approval the person decided.
- Recording an outcome or reading a run needs no model key.
- A removed 0.3 name (`SequentialAgent`, ...) says what replaced it.
- `list_all_available_tools` lists the `delegate_<name>` tools.
- Unreadable arguments (an empty path) are `rejected`, not `denied`.
- E2B/Daytona's network isolation check is in the session event.
- The skills catalog shows the tool calls, not the skill's host path (a
  model shown the path ran the script with `execute`, in a sandbox without it).
- Docs: a generated budgets reference; the sub-agents example's tool names
  and the story's segment shape (both wrong); operators and the policy;
  interval tasks' first run; waiting for `/ready`; exporting a run with
  children; outcomes after a headless run; putting files in the workspace;
  Docker's default image; what workers inherit.

## Release

S1–S4 are about the evidence and the policy — what the release promises —
and go in before 0.4.0 is tagged. S5 is investigated first; S6's wrong
statements are fixed before the tag, its missing pages as far as time allows
and the rest right after.

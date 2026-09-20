# Governed budgets plan

Status: proposed 2026-09-20. Raised by the maintainer: "governance is supposed
to govern everything" — an application should be able to set a budget for the
whole application and for a single request, and work should stop when it runs
out.

## Where this stands today

Two mechanisms exist, and neither does what is being asked:

| Today | What it counts | Scope | Gap |
| --- | --- | --- | --- |
| `policy.budget` (`max_requests`, `max_cost`) | Authority requests (tool calls). `max_cost` adds up `AuthorityRequest.budget_cost`, which **nothing ever sets**. | One policy object in one process | Not money; resets when the process restarts; not shared between runs or workers |
| `agent_config` `request_limit` / `total_tokens_limit` (`UsageLimits`) | Model calls and tokens | One run | Not governance, not money, not per application |

So nothing counts real spend, nothing survives a restart, and nothing is
shared. A team cannot say "this application may spend $200 a day", and a
single request cannot be capped in money at all.

## What a budget should be

A budget is an authority question, like any other: *may this run spend this?*
It belongs in governance, is decided before the spend happens, is recorded,
and — because runs can now pause — can be topped up by a person instead of
only failing.

### Meters

| Meter | Charged | Why it matters |
| --- | --- | --- |
| `model_cost_usd` | Estimated before a model call, corrected to the real cost after the response | The number a finance team cares about |
| `model_tokens` | After each response | Works when a model's price is unknown |
| `model_calls` | Before each model call | Simple runaway guard |
| `tool_calls` | Each governed tool call | Cost that is not the model (an MCP tool may bill) |
| `sandbox_seconds` | A session's lifetime, charged as it runs and at close | What Modal, E2B and Daytona actually bill |
| `subagent_runs` | Each delegation | Stops fan-out |

### Scopes, and how they nest

| Scope | Identity | Typical limit |
| --- | --- | --- |
| `request` | One `run_id` | "no single request may spend more than $2" |
| `session` | One `session_id` | "a conversation may spend $10" |
| `agent` | One agent name | "the support agent may spend $50 a day" |
| `application` | An id you choose (a tenant, a deployment) | "this application may spend $200 a day" |

Every charge goes up the chain: a request charges its own meter, its
session's, its agent's and the application's. **Any exhausted level stops the
work**, and the run says which one. Each scope has a window: `total`,
`day`, or `month` (UTC), so `application/day` resets at midnight and
`request/total` never resets.

### When it runs out

- **Pause (the default).** The run stops where it is and waits, exactly like
  an approval: `run()` returns `awaiting_approval` with "the application's
  daily budget is exhausted; $4 more would finish this". A person tops it up
  and resumes, or denies and the run ends cleanly. Nothing is lost, and the
  work already done is not thrown away.
- **Terminate.** For unattended jobs: the run ends as `budget_exhausted`,
  recorded in its trace and record. Background runs map to a failed run with
  that reason.
- **Warn before the wall.** Crossing `warn_at` (80% by default) records a
  `budget_warning` in the trace with the meter, the scope and what is left.

### Where the numbers live

In the memory store the application already chose, beside run state, with
atomic increments, so ten workers share one budget and a restart does not
reset it. Counters are keyed by scope, identity and window.

A budget is part of the **policy**, so it is covered by the policy hash and
cannot be widened at runtime without changing the policy; `governance_config`
accepts budgets as a convenience for applications that use no policy file.

## Units

- **B1. Meters and the ledger.** A `BudgetLedger` on the memory store: atomic
  add-and-read per scope key and window, for all four backends, with a
  concurrency test that two workers cannot both spend the last dollar.
- **B2. Policy and configuration.** Budgets in the policy envelope (covered by
  its hash) with per-scope meters and windows; validation; nothing budgeted by
  default; the old `policy.budget` keeps working and is expressed in the new
  shape.
- **B3. Enforcement.** Reserve before a model call and correct after it
  (tokens and cost), charge each governed tool call, charge sandbox seconds as
  a session runs and at close, charge delegations; the cheapest check first;
  warnings; the `terminate` path and the new `budget_exhausted` status.
- **B4. Pause and top up.** Exhaustion becomes an approval on the run
  (`agent.grant_budget(run_id, meter, amount, approver=...)`, or a denial that
  ends the run), `resume` continues the work; OmniServe routes; what a
  background run does.
- **B5. Reporting, docs, proof.** Budgets in run totals and the run record; a
  "Budgets" docs page; lines in the security model; an acceptance script
  proving an application-wide budget stops a second run, that a paused run
  tops up and finishes, and that the ledger is shared across processes.

## Decisions to confirm

| # | Question | Recommendation |
| --- | --- | --- |
| 1 | What happens by default when a budget runs out? | Pause and wait for a top-up; `terminate` for unattended jobs. It reuses approvals, and no work is lost. |
| 2 | Where do budgets live? | In the policy, so they are hashed and cannot be widened at runtime; `governance_config` is a convenience. |
| 3 | A model whose price is unknown (no cost from the provider) | Charge tokens, count the cost as zero, and flag the run `cost_incomplete`, rather than refusing the call. Refusing would break local and new models. |
| 4 | How are sandbox seconds counted? | A session's lifetime (what providers bill), charged as it runs, not only at the end, so a long session cannot overrun the budget. |

## Execution log

| Unit | Status | Commit | Notes |
| --- | --- | --- | --- |

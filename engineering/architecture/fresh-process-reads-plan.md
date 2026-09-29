# Plan: a fresh process reads and manages runs correctly

Recording real footage of 0.4.3 (2026-09-29) found `budget_status(run_id)`
returning `[]` from a second process, though the docs say it works from any
process. The cause is a class of bug: some public methods read the budgets from
the inner agent, which exists only once the agent is initialized, and a process
that only reads or manages runs never ran, so never initialized. Nothing raised;
the answer was silently wrong. The tests missed it because every one called these
methods on the agent that had just run the query, in the same process. One even
asserted `... or True`, which cannot fail.

## Found by an audit of every public method that reads the inner agent

| Method | In a fresh, uninitialized agent | Fix |
|---|---|---|
| `budget_status(run_id)` | `[]`, as if nothing were budgeted | initialize first |
| `abandon_run(run_id)` | ends the run but leaves its budget holds, so the day's counter stays over-counted | initialize first |
| `can_execute()` | `False` (it is synchronous) | document: after `initialize()` |

## Units

- **F1** · Test first: real separate processes sharing a SQLite store. Process one
  runs a budgeted agent until it waits for budget, with a hold left by a dead
  attempt. Process two, a freshly built agent that has not run anything, reads
  `budget_status` and abandons the run; it must see the budgets and release the
  hold. Also: every public method that reads or manages a run gives the same
  answer from a fresh agent as from the one that ran it.
- **F2** · The fix: `budget_status` and `abandon_run` initialize the agent first,
  as `grant_budget`, `resume` and the other run-management methods already do.
- **F3** · The vacuous assertion in `test_budget_status.py` becomes a real one.
- **F4** · Docs: `can_execute()` answers after `initialize()`.

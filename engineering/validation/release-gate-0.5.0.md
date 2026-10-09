# The 0.5.0 release gate: what was tested, what broke, and why it shipped

Status: final (2026-10-05). Nine release candidates (0.5.0rc1–rc9) were built and tested between
2026-09-29 and 2026-10-05. 0.5.0 is the rc9 code plus one corner fix.

## How a candidate was tested

Each candidate was built as a wheel and installed into a clean environment, the way a user would
install it. No tester ran the repository's source. Independent testers worked in parallel, each in
its own directory, with its own ports and a spending cap:

| Area | Question it answered |
|---|---|
| A, docs as written | Does every example and command in the docs run as printed and print what the page says? |
| B, control | Does the agent do only what the policy allows? Do approvals, budgets and worker profiles hold? |
| C, durability | After `kill -9` at every point of a run, on SQLite, PostgreSQL, Redis and MongoDB, is anything silently redone? |
| D, execution | Is the sandbox the boundary the docs say? Docker, E2B and Daytona live; every provider's contract at build time. |
| E, the record | Is every run's trace complete, readable start to finish, private by default, and free of credentials? |
| F, a stranger | Can someone who has never seen the project build a real application from the docs alone? |
| S, command rules | Does the shell-command parser agree with real `dash` and `bash` on a fuzzed corpus of thousands of lines? |

From rc5, every finding was rated BLOCKER, MUST-FIX or LATER. From rc9, findings were rated CORE (a
documented core promise fails on a normal path) or CORNER (an edge, wording, or rare path).

## The stop rule

A tester asked to find problems in a runtime this wide will always find some, so the count was never
going to reach zero. 0.5.0 ships when a gate shows **no CORE finding**. The CORNER findings of that
gate are fixed in the same round, each with a test and the full suite. Everything rated LATER is
listed as a known issue and goes to 0.5.1.

## The trend

| Gate | Blockers / CORE | MUST-FIX | Areas ready | Notes |
|---|---|---|---|---|
| rc1–rc2 | blockers | — | — | governance on by default, new |
| rc3, rc4 | 0 | — | — | no blocker in any area |
| rc5 | — | many | 1 of 6 | rating introduced |
| rc6 | — | 10 | 1 of 6 | |
| rc7 | 4 | 14 | 0 of 7 | worker profiles, the simple policy and the security review landed between rc6 and rc7 |
| rc8 | 0 | 5 | 2 of 7 | each in a corner of an rc7 fix |
| **rc9** | **0 CORE** | 2 CORNER | **3 of 3 scoped** | the stop rule is met |

## What held from rc2 onward

- **Durability.** No non-idempotent tool call was ever re-run by the runtime after a crash: 44
  recoveries in rc7 and more in rc8 and rc9, on four stores, with zero double charges. Approvals
  decided in another process apply exactly once.
- **Governance.** Allow, ask and deny; approvals bound to the exact call; budgets.
- **The record.** Redacted by default, with credentials scrubbed from every record and export.
- **The docs.** They run as printed; area A was READY in rc8.

## What changed during the gate

The gate ran while three pieces landed, and most findings from rc7 on came from them:

- **The security review** (PR #322): folder moves and deletes past path rules, worker budgets,
  `>& file`, Unicode in approval summaries, links, OmniServe metrics, rate limiting and CORS.
- **Worker profiles** (PR #323): the lead picks a model, effort, tools and narrower rules per
  worker.
- **The simple policy** (PR #324): the sandbox is the boundary, and command rules are prefix rules.
  Every gate since rc4 had found a new way past the old shell parser
  ([plan](../architecture/simple-policy-plan.md)).

## Findings, by gate

The per-gate results, with every finding, its rating and its fix, are in the PRs that fixed them:

- rc5: #320
- rc6: #321
- rc7: #325
- rc8: #326
- rc9: the 0.5.0 release PR

Each fix has a test that fails without it.

## Known issues in 0.5.0

These are listed for users in the [changelog](../../docs/changelog.mdx#known-issues-in-050). They were
deferred to 0.5.1 because none breaks a core promise on a normal path.

*Status at 0.6.0:* the sibling-skill read and the small budget's repeated pauses were fixed in
0.5.1; the slow first run is `import litellm` and still applies, listed under 0.6.0's known issues.

# Trajectory acceptance

This is the Phase C1 proof for the
[telemetry trajectory completion plan](../architecture/telemetry-trajectory-completion-plan.md).
One call to `agent.run()` must produce a trace from which a reader can see the
whole run, from request to final answer. The
[acceptance script](trajectory_acceptance.py) runs a scenario that exercises
every part of that and checks all ten checklist items from the output of the
trajectory reader (`agent.get_trajectory()`).

## How to run it

```text
python engineering/validation/trajectory_acceptance.py --check-fixture
PYTHONPATH=src .venv/bin/python engineering/validation/trajectory_acceptance.py --run
PYTHONPATH=src .venv/bin/python engineering/validation/trajectory_acceptance.py --write-fixtures
PYTHONPATH=src .venv/bin/python engineering/validation/trajectory_acceptance.py --live --env-file <path to .env>
```

`--check-fixture` uses only the standard library and the committed files, so a
reviewer does not need OmniCoreAgent installed. `--run` runs the scripted
scenario three ways in a temporary workspace. `tests/test_trajectory_acceptance.py`
runs both as part of the test suite. `--live` needs `LLM_API_KEY` and
`OMNICOREAGENT_TEST_MODEL`; it never prints or stores the key.

## Scenario

The lead agent runs with a scripted model, local tools, a configured subagent,
context compression (`sliding_window`, `summarize_and_truncate`), and tool
offloading:

| Step | What happens |
| --- | --- |
| 1 | One parallel batch: `lookup` succeeds, `explode` raises, `lookup` with the malformed arguments `{broken` is rejected, `slow` exceeds the 3 s tool limit (`tool_call_timeout`, recorded in the run header), and `big_report` returns a result large enough to be offloaded to a workspace artifact. |
| 2 | The model returns an empty response; the runtime adds its empty-response retry message. |
| 3 | The model reads the offloaded artifact by the ID it received in the observation. |
| 4 | Context compression summarizes older messages (an internal `context_summary` model call); the model delegates to the `researcher` subagent, which makes its own tool call. |
| 5 | Another compression, then the final answer `ACCEPTANCE_COMPLETE`. |

It runs directly with full capture, directly with the default privacy-first
capture, and through OmniServe `/run/sync` with full capture.

## Checklist results

All three runs pass all ten items.

| # | Item | What is checked |
| --- | --- | --- |
| 1 | Request | The query, `trace_id`, `run_id`, `session_id`, and the entry surface (`interactive` directly, `serve` through OmniServe). |
| 2 | Harness | Model, `max_steps`, the context strategy, the tool catalog (names and count), the system prompt digest, configuration fingerprints, and the agent, prompt, tool schema, and memory configuration versions. |
| 3 | Steps | Five steps; every agent turn records tokens, finish reason, latency, and attempts; with full capture the request messages, response, and raw tool-call arguments are present; with default capture the response is `not_recorded`. |
| 4 | Tool calls | Step 1 outcomes are exactly success, error, rejected (`invalid_arguments`, raw text `{broken`), timeout, and success; the failure keeps its error. |
| 5 | Observations | The second agent turn lists exactly step 1's five observations as newly received; with full capture each observation equals the tool message that call sent to the model. |
| 6 | Context management | The current-datetime prefix and the empty-response retry are runtime messages; compressions and `context_summary` calls are recorded; offloaded results carry references; the artifact read succeeds. |
| 7 | Delegation | The delegation reports the `subagent` provider and succeeds; the child's trajectory is nested under the calling tool with its own steps, tool call, and final answer. |
| 8 | Final answer | Status `completed`, the answer, and the link to the model response that produced it. |
| 9 | Totals | 5 steps; 5 agent turns plus the summary calls; tokens equal the sum of the per-call records; cost is known for every call; tool outcomes are 4 success, 1 error, 1 rejected, 1 timeout; subagent-inclusive totals exceed the lead's own. |
| 10 | Honesty | Full capture: evidence `complete`, no capture gaps. Default capture: `partial`, with `not_recorded` gaps. In every run the trajectory accounts for every event of the trace. |

## Committed fixture

`fixtures/trajectory-acceptance/` holds the direct, full-capture run:
`trajectory.json` (the reader's output), `evidence.json` (the portable
evidence envelope), and `traces.jsonl` (the records the JSONL store wrote).
Identifiers are replaced by stable aliases and the temporary workspace path by
`<workspace>`; the payloads are synthetic. Most of the size comes from full
capture recording the complete tool catalog and message history with every
model call, which is what full capture promises.

## Live result

A real model through LiteLLM (Python 3.12.13, LiteLLM 1.100.1, `gpt-5.4-mini`)
with one local tool, full capture:

| Fact | Value |
| --- | --- |
| Steps | 2 |
| Tool calls | 1 success, delivered to the next model call |
| Tokens | 3,028 input, 32 output, 3,060 total, 2,048 cached input |
| Estimated cost | $0.0010326 (every call priced) |
| Provider response IDs | 2 distinct |
| Final answer | "Order A-17 is shipped." |
| Events accounted for | 25 of 25 |
| Evidence | `complete`, no capture gaps |
| API key in the trace | No |

The portable evidence document of the live run validates against the
published schema.

## Found during C1

- **A shared store lost events across event loops.** Since B1, one store object
  is shared per file. Its `asyncio.Lock` bound itself to the first event loop
  that contended for it, and in any later loop (a second `asyncio.run`, a
  notebook, a sync wrapper, a test suite) contended writes failed and
  best-effort telemetry dropped them. The scenario's second run showed tool
  calls with no recorded result. The stores now hold one lock per event loop.
- **Provider response IDs were intermittently corrupted.** The PII filter
  rewrote digit groups inside UUIDs as phone or card numbers
  (`chatcmpl-7fe09b14-[REDACTED_PHONE]-d995b29d39db`): 1.13 % of random
  provider IDs, which also made the affected run `partial`. A match inside a
  UUID is now left alone; 0 of 300,000 random IDs are altered, and phone and
  card numbers, including hyphen-joined ones, are still redacted.

## Boundaries

The scripted runs have no provider response IDs, which is why the live run
exists. The live scenario is deliberately small: a real model cannot be told to
produce malformed arguments or time out on cue, so those paths are proven by
the scripted runs. MCP tools are not exercised; the MCP v2 adapter is planned
separately.

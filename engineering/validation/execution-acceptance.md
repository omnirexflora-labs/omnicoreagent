# Execution acceptance

This is the E8 proof for the
[governed execution plan](../architecture/governed-execution-plan.md). An agent
may run code in three lanes — its own tools, commands in a sandbox (`execute`
and skill scripts), and a program in code mode — and every one of them must be
authorized by policy, contained where it should be, and readable afterwards.

The [acceptance script](execution_acceptance.py) runs one scenario that uses
all three lanes, pauses for a person's approval, resumes, and then checks eight
items from what the run left behind: its trace segments, its durable record,
and the workspace.

## The scenario

1. The agent writes two workspace files (its own tools).
2. It runs a command in a Docker sandbox that adds the numbers up and writes
   the answer to a file (sandbox, and the workspace bridge carries the file
   back).
3. It runs a program in code mode that reads that file through a governed tool
   call and doubles the number (Monty, with the call nested under `run_code`).
4. It tries to delete a file. The policy asks for approval, so the run pauses;
   nothing unapproved runs.
5. A reviewer approves, the run resumes and finishes.

## The checklist

1. The run reads as one story: request, both segments, final answer.
2. The sandbox session is recorded, opened and closed, with its provider.
3. Every sandboxed command is recorded with its exit code and the rule that
   allowed it.
4. A file the sandbox wrote came back as a governed workspace write.
5. A program's tool calls are nested under `run_code`, each with its own
   policy decision.
6. The run paused for approval, and the file survived until someone approved.
7. The run record shows every call's state and both trace segments.
8. Run totals count the executions, and nothing was lost from the record.

Item 8 accepts payloads recorded as `redacted` or `not_recorded`: under
governance, tool arguments are redacted on purpose, and the reader reports
that honestly. What it refuses is a payload that went missing.

## How to run it

```text
python engineering/validation/execution_acceptance.py --check-fixture
PYTHONPATH=src .venv/bin/python engineering/validation/execution_acceptance.py --run
PYTHONPATH=src .venv/bin/python engineering/validation/execution_acceptance.py --write-fixtures
```

`--check-fixture` uses only the standard library and the committed records, so
a reviewer needs neither OmniCoreAgent nor Docker nor Monty. `--run` runs the
scenario for real (Docker and `omnicoreagent[codemode]`) in a temporary
workspace. `tests/test_execution_acceptance.py` runs the fixture check always
and the real scenario where Docker and Monty are available.

The committed fixture holds the run's own records with identifiers and the
temporary workspace path replaced; nothing else is edited.

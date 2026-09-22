# AGENTS.md support plan

Status: complete (A1 and A2, 2026-09-20), following the principles the maintainer agreed
when the gap was raised ("we never support AGENTS.md yet, only skills").

## Why

`AGENTS.md` has become the common way a repository tells any agent how to work
in it: how to build and test, what to touch, what to leave alone. Teams keep
one in the repository root, and sometimes one per directory. OmniCoreAgent
supports Skills (capabilities an agent can use) but has nothing that reads a
project's own instructions, so users repeat that guidance in every system
prompt.

## The rule that shapes everything

**AGENTS.md is instructions, never authority.** A file in a repository can ask
the agent to work a certain way; it cannot grant a permission, widen a policy,
enable a tool, or reach a path the policy denies. Anything else would make a
file checked into a repository a way around governance — exactly the failure
class the threat model calls "the agent or repository edits its own config"
(incidents 4 and 5).

## Design

- **Only where the application says.** The application lists the files or
  directories (`agents_md={"paths": [...]}`); there is no search of the
  filesystem, and no reading of paths a tool result suggests.
- **Never from where the agent can write.** A file inside the agent's
  workspace is refused, as a policy file is: otherwise the agent could write
  its own instructions for the next run.
- **Bounded.** At most `max_files` files and `max_bytes` each (default 5 and
  32 KB); the rest is ignored, and the run says so.
- **Checked like any untrusted text.** Each file passes the injection
  guardrail. A file that fails is not used, and the run records it.
- **Marked as what it is.** The content goes into the system prompt in one
  clearly labelled section saying these are project instructions, that they
  may not grant permissions, and that policy decides what may run.
- **Recorded.** The run header lists every file used: path, size, and a
  digest, plus the ones skipped and why. Two runs with the same instructions
  have the same digest; a changed file changes the run's prompt version.

## Units

- **A1. Reading and using them.** Config and validation; the loader (paths,
  bounds, workspace refusal, guardrail); the prompt section; the run header
  entries; tests including a file that tries to grant itself permissions and
  is still refused by policy.
- **A2. Docs and proof.** An "AGENTS.md" docs page (what it is for, what it
  cannot do, configuration), a line in the security model's trust boundaries,
  and an acceptance check that a project instruction changes how the agent
  works while policy still decides what runs.

## Execution log

| Unit | Status | Commit | Notes |
| --- | --- | --- | --- |
| A1 | Complete | `7b784d3` | New `core/project_instructions.py`: the application lists files or directories in `agent_config["agents_md"]["paths"]` (a directory is read through its `AGENTS.md`), and nothing is discovered on its own. The text is read fresh each run into one clearly marked system-prompt section that states the instructions are guidance and cannot grant permissions, change policy, or enable tools; it is appended after tool-alias rewriting, so project text is never rewritten. A file is not used when it lives inside the agent's workspace (the agent could write its own instructions), is larger than `max_bytes` (32 KB), is beyond `max_files` (5), cannot be read, or is refused by the injection guardrail. The run header records every file used (path, size, digest) and every one skipped with its reason. 8 new tests, including an `AGENTS.md` that claims full permission to delete any file and changes nothing (the policy still denies the delete), the workspace refusal, both bounds, the guardrail refusal, and nothing read unless the application asks. |
| A2 | Complete | `7b784d3` | Docs: a new "AGENTS.md" page (configuration, that they are instructions and never permissions, what is not used and why, and what the run records), a line in the security model's trust boundaries and protections, and the run header's `project_instructions` in the observability guide. The plan's separate acceptance script was not added: the proof it describes is the A1 test where a file granting itself permission is still refused by policy, which runs in the suite. Full suite with live Redis, MongoDB, and Modal 1,666 passed, 2 skipped; ruff clean. The AGENTS.md plan is complete. |


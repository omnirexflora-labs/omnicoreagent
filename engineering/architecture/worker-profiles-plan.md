# Worker profiles: the lead picks the model, effort and tools for each worker

Status: planned for 0.5.0 (decided 2026-10-01, before the release gate).

## Why

Today every worker that `spawn_subagents` starts gets the lead's model, all of the lead's tools, a
copy of its policy and at most 50 steps. The lead writes a role in free text and nothing else.

An agent that delegates well picks the worker for the job: a small, cheap model at low effort to
search, a strong model at high effort to build, and a read-only worker to review. Replit's
"Free the models" measured this. With the main model choosing whether to delegate, which
specialist and at what effort, it scored 72% at $2.11 a task, against 74% at $4.43 with one large
model doing everything. In production the main model delegated in about 36% of turns.

The router is the lead model itself, choosing from a list the developer wrote. We do not add a
separate router.

## What the developer writes

```python
agent_config={
    "enable_subagents": True,
    "worker_profiles": [
        {
            "name": "explorer",
            "description": "Reads and searches the workspace; never writes code.",
            "model_config": {"model": "gpt-5.4-mini"},  # same provider as the lead
            "reasoning_effort": "low",
            "tools": ["read_file", "grep", "glob", "ls"],
            "max_steps": 20,
        },
        {
            "name": "builder",
            "description": "Writes and tests code in the sandbox.",
            "reasoning_effort": "high",
            "policy": {"deny": [{"capability": "network.http"}]},
        },
    ],
}
```

## Decisions

1. **Profiles are a list in `AgentConfig`** (`worker_profiles`), each a dict validated when the
   agent is built, like the other nested settings. Unknown keys are refused, with the allowed keys
   named.
2. **When profiles are set, every worker names one.**
   - `profile` is required in `spawn_subagents` and is an enum of the profile names.
   - A developer who sets profiles to keep workers cheap or narrow must not see a general worker
     appear anyway. A general profile is one line to add.
   - With no profiles set, nothing changes. Workers behave as in 0.4.
3. **The lead sees each profile's name and description** (and its model and effort) in the
   `spawn_subagents` description, so it can choose. Choosing is the lead's job, and nothing forces
   it to delegate.
4. **Model.**
   - `model_config` is laid over the lead's.
   - With the same provider, or no provider given, unset fields come from the lead's (key,
     `base_url`), so `{"model": "gpt-5.4-mini"}` is enough.
   - With a different provider it is used whole, and the lead's key is never sent to another
     provider.
   - `reasoning_effort` is a shortcut for `model_config.reasoning_effort`.
5. **Tools only narrow.**
   - `tools` lists local tool names the worker gets. `mcp_servers` lists MCP server names.
   - `None` means all of the lead's.
   - A name the lead does not have is refused when the agent initializes.
   - `write_file` is always kept, because a worker writes its output to a file. `spawn_subagents`
     is never given.
6. **Policy only narrows.**
   - `policy` takes `deny` and `ask` rules, which are added to the policy derived from the lead's.
   - `allow` is refused when the config loads: a worker never gets more than its lead.
   - Rules are validated like any policy rule (capability names, examples).
7. **Steps.** `max_steps` runs from 1 to 50 and is capped by the lead's `max_steps`. The default is
   today's: the smaller of the lead's and 50.
8. **`instructions`** (optional) is added to the worker's system prompt after the role, for
   standing guidance such as "Never edit files; report findings only."
9. **One budget ledger.** Workers spend the lead's budgets (the rc7 security fix S1-2, already on
   `fix/rc7-security`). Profiles do not get budgets of their own in 0.5.0. The lead's limits bound
   the lead and all of its workers together.
10. **Governance sees the profile.**
    - The `subagent.spawn` request carries the profile as its target `resource`, so a policy can
      deny or ask about a profile (e.g. ask before any `builder`).
    - The worker name, which the model invents, moves to metadata.
    - With no profiles set, the resource stays the worker's name, as now.
11. **Evidence.**
    - The delegation span and the worker's trace record the profile, the model and the effort.
    - The run's cost totals already add the workers'. They now say which model each worker used.

## Units (one commit each, test first)

- **P1 — config.**
  - Add `worker_profiles` to `AgentConfig`, with validation.
  - Tests: unknown key, duplicate name, bad name, `allow` in policy refused, `max_steps` range,
    unknown tool or MCP server refused at initialize.
- **P2 — the spawn tool.**
  - `profile` in the schema (an enum, required when profiles are set).
  - The tool description lists the profiles.
  - Tests: the schema the model sees, a missing or unknown profile refused with the names listed.
- **P3 — the worker is built from its profile.**
  - Model and effort, tools and MCP servers, steps, instructions.
  - Tests with a scripted model: the worker's model config and tool list are the profile's, and
    the provider-switch rule drops the lead's key.
- **P4 — narrowing policy and governance.**
  - `derive_subagent_policy(..., profile=)` adds the deny and ask rules, and the spawn request's
    resource is the profile.
  - Tests: a profile's deny rule refuses a call the lead may make, and a policy rule on the
    profile asks before spawning it.
- **P5 — evidence.**
  - The profile, model and effort on the delegation span and the child trace.
  - Test: the trajectory shows them.
- **P6 — docs and a real example.**
  - `sub-agents.mdx` gets a profiles section, the options table and how the lead chooses.
  - The configuration reference gets the new setting.
  - A cookbook example runs live with a small and a large model (under $0.50) and shows the
    choice and the cost per worker.

## Not in 0.5.0

- Going back to a worker that has already been briefed.
- Changing effort in the middle of a turn.
- Budgets per profile.
- Profiles for named child agents.

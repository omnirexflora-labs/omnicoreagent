# Threat model: governed execution

Written 2026-09-19 for the governed execution plan (unit E5b.5), from the
incidents in `engineering/validation/agent-sandbox-incidents.md`. Every
control below names the test that proves it; `tests/test_docs_claims.py`
fails if a named test no longer exists.

## What we protect

- The host: its files, processes, and network position.
- Credentials: the model provider key (`LLM_API_KEY`), application secrets,
  cloud and SSH credentials in the user's home.
- The policy that governs the agent, and the record of what it did (the trace).
- The application's data: the workspace, memory, and whatever its tools reach.

## Who we assume is hostile

- **The model's output.** It can be steered by prompt injection from any text
  it reads (tool results, files, web pages, MCP servers) or can pursue a goal
  past its instructions. Every tool call and every command is untrusted input.
- **Code the model writes or runs**, including skill scripts it chooses to run.
- **MCP servers and their tool descriptions.**

We trust the application's own code and configuration (its tools, its policy,
its choice of MCP servers and sandbox), and the host operating system.

## Trust boundaries

| Boundary | What enforces it | What does not cross it |
| --- | --- | --- |
| Harness ↔ model | Every tool call goes through one dispatch point into `GovernedToolRunner`; governance decides it before it runs (deny > ask > allow). | A tool call the policy does not allow. |
| Harness ↔ sandbox (Docker) | A separate container: no network by default, read-only root, unprivileged user, all capabilities dropped, no privilege escalation, process/memory/CPU limits; optionally gVisor. The harness, credentials, policy, and telemetry stay outside. | Host files, host environment, provider credentials, the policy. |
| Workspace ↔ sandbox | The workspace bridge copies files, each copy authorized like a workspace read or write, on the exact path. The workspace is never mounted. | Links, hidden paths, oversized or non-text files, anything policy denies. |
| Agent ↔ subagent | A governed agent only delegates to governed agents; delegation is its own capability (`subagent.spawn`). | Work handed to an agent nothing governs. |
| Harness ↔ host skill script | Governed (`skill.script.run`, high risk, surface `host`), minimal environment. **Not contained**: the script runs as the application's user. | The agent's environment (credentials). |

## Failure classes from real incidents

Numbering follows the incident record.

### 1. The sandbox's allowed exit is attackable or shared

- Controls: the network is off unless the manifest turns it on, and turning it
  on needs approval in the dev profiles
  (`tests/test_docker_sandbox.py::test_network_is_off_by_default_and_an_allowlist_is_refused`,
  `tests/test_execution_governance.py::test_turning_the_network_on_needs_approval_by_default`).
  A host allowlist is refused rather than pretended, because Docker cannot
  enforce one. Each run gets its own container and working directory; nothing
  is shared between runs
  (`tests/test_execute_tool.py::test_execute_runs_in_one_sandbox_per_run_and_it_is_gone_afterwards`).
- Open: once the network is on, it is the whole network. An egress proxy that
  enforces a host allowlist is not built.

### 2. An allowed channel is used for exfiltration

- Controls: no network by default; MCP calls and network capabilities ask in
  interactive-dev; the injection guardrail scans tool results.
- Open: any channel the policy allows (an MCP tool, an application tool, a
  network-enabled sandbox) can carry data out. There is no data-flow tracking.

### 3. Credentials inside the sandbox or within reach

- Controls: only the manifest's environment reaches a container
  (`tests/test_docker_sandbox.py::test_only_the_manifest_environment_reaches_the_container`);
  the Docker socket, system directories, and credential directories are never
  mounted, whatever the policy says
  (`tests/test_docker_sandbox.py::test_sensitive_host_paths_are_never_mounted`,
  `tests/test_docker_sandbox.py::test_a_path_that_contains_a_sensitive_one_or_links_to_it_is_refused`);
  host skill scripts get a minimal environment, not the agent's
  (`tests/test_execute_tool.py::test_a_host_skill_script_gets_a_minimal_environment`,
  `tests/test_execute_tool.py::test_an_agents_skill_scripts_receive_only_the_configured_variables`).
- Open: application tools run inside the agent's process and can read
  anything it can. Brokered secrets for the sandbox are designed
  (`secret_refs`) but not wired to a provider.

### 4. The agent or repository edits its own config or policy

- Controls: a policy file inside the agent's workspace, or under an
  agent-writable directory name, is refused
  (`tests/test_policy_placement.py::test_a_policy_inside_the_agents_workspace_directory_is_refused`);
  a read-write sandbox mount containing the policy is refused
  (`tests/test_policy_placement.py::test_a_read_write_mount_containing_the_policy_is_refused`);
  an auto-discovered policy can only tighten the defaults.
- Open: a host skill script runs as the application's user and could write a
  policy file the application reads at its next start. Keep the policy file
  read-only to that user, or run skills in the sandbox.

### 5. Model-chosen tool arguments widen privileges

- Controls: the sandbox manifest (network, mounts, image, environment) is set
  by the application, not by tool arguments; a manifest that widens the scope
  is authorized before any container exists; each command is authorized on
  its own. Bridge copies are authorized on the exact path, not the tool's
  prefix-stripped form
  (`tests/test_workspace_bridge.py::test_policy_sees_the_exact_path_written_even_with_a_tool_path_prefix`).

### 6. Weak isolation: one enforcement point, language-level sandboxes

- Controls: execution is contained by the operating system (a container), not
  by Python; governance and the container are independent layers. The
  complete-mediation tests fail if a process or socket is started outside the
  reviewed sites, or if any tool has an effect under a policy with no allow
  rules
  (`tests/test_complete_mediation.py::test_processes_and_raw_sockets_are_started_only_at_reviewed_sites`,
  `tests/test_complete_mediation.py::test_under_a_policy_with_no_allow_rules_no_tool_has_any_effect`).
  gVisor is available for a boundary that does not share the host kernel's
  system call surface.
- Open: a plain Docker container shares the host kernel; gVisor is optional,
  and was not available to test on the development machine.

### 7. String or prefix matching in allow and deny lists

- Controls: paths are compared after resolving (`is_relative_to`, real paths),
  not as strings; the skill-name prefix bypass found during E3b is covered
  (`tests/test_execute_tool.py::test_a_skill_name_cannot_reach_a_sibling_directory`).
  Commands in the sandbox are not filtered by name at all: the container is
  the boundary.

### 8. Escape hatches, approval fatigue, auto-approve

- Controls: there is no "disable the sandbox" path the model can take. When
  a sandbox is configured and fails, commands and skill scripts fail; they do
  not fall back to the host (skill scripts run on the host only when the
  application configured no sandbox). A policy rule that requires a sandbox is
  refused without one. Static approvals are refused for high-risk requests
  unless the application opts in.
- Open: approvals cannot yet pause a run and wait for a person (Durable Runs,
  D2); until then an `ask` without a resolver fails the call.

### 9. Relaxed safety settings

- Controls: every run's header lists `security_warnings` for configurations
  with less protection than a reader might assume
  (`tests/test_security_warnings.py::test_host_skill_scripts_without_governance_are_warned_about`,
  `tests/test_security_warnings.py::test_a_sandbox_configured_without_governance_is_warned_about`).
- Open: governance is off by default. Nothing stops an application from
  running ungoverned; it is stated, not prevented.

### 10. Log tampering and slow detection

- Controls: the trace is written by the harness, outside the sandbox and
  outside the model's reach; every command and file copy is recorded with the
  policy decision that allowed it
  (`tests/test_execution_telemetry.py::test_a_session_and_each_command_are_recorded_with_their_facts`).
- Open: nothing alerts on a trace in real time; detection depends on someone
  reading it.

## Summary of what is not protected

1. **Application tools** run in the agent's process with its privileges.
   Governance decides whether they run; it cannot limit what they do.
2. **Host skill scripts** are governed and get a minimal environment, but run
   as the application's user and can touch whatever that user can.
3. **Anything the policy allows.** Governance is only as good as the policy;
   an allow rule for a dangerous capability is honoured.
4. **Governance off.** Without it, no policy applies, and the sandbox is not
   used.
5. **The host kernel**, unless gVisor or a hosted microVM provider is used.

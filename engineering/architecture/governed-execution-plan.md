# Governed execution plan: sandbox and code mode

Branch `refactor/native-tool-runtime`, started at `f1b22ee` (2026-09-19), after
PR #244 (`SandboxExecutionService`) was brought into this branch. Background:
[governed execution architecture](governed-execution.md) and
[specification](../specifications/governed-execution.md).

## Why

OmniCoreAgent governs tools, MCP servers, workspace writes, and subagents, but
it cannot yet run code safely. The only built-in code execution is
`run_skill_script`, which runs skill scripts with `subprocess.run` **on the
host** and synchronously, blocking the event loop. The sandbox layer has a
provider-neutral interface (`SandboxRuntime`), governance that fails closed
when a decision requires a sandbox and none is available, and PR #244's
governed `SandboxExecutionService`, but no real provider and no route from the
agent's tools.

## Research (2026-09-19)

OpenAI (Agents SDK sandbox agents, Codex), Anthropic (Agent SDK hosting, Claude
Code sandboxing, Managed Agents with self-hosted sandboxes), and LangChain Deep
Agents all support running the whole agent inside a sandbox (model A) and
running only tool execution inside a sandbox while the harness stays outside
(model B). All three recommend model B for production: orchestration,
credentials, and state stay in the trusted harness; the sandbox is compute.
Shared patterns: one small execution primitive (`execute` plus file transfer)
that file tools are built on; the provider chosen in run configuration, not in
the agent; credentials never inside the sandbox; network denied by default with
an allowlist; sandbox output treated as untrusted.

Pydantic's Monty (MIT, alpha 0.0.23) is a Rust interpreter for a Python subset
with no ambient authority: code reaches only the functions, objects, and
mounted directories the host passes in. A host-function call suspends the
interpreter into a serializable snapshot, which supports pausing for approval
and resuming. It runs Pydantic AI's Code Mode (the model writes one program that
calls tools as functions). It does not run third-party packages, shell, or
subprocesses, so it complements a sandbox rather than replacing one.

## Decisions (2026-09-19)

| Question | Decision |
| --- | --- |
| Sandbox model | Model B built in: the harness, credentials, governance, memory, and telemetry stay in the trusted process; code execution goes to the sandbox. Model A (the whole agent in a container) is documented as a deployment guide only. |
| Providers | Docker as the open-source default; E2B, Modal, Daytona, Cloudflare, and Vercel adapters; any other provider through the same interface. |
| What runs in the sandbox | All code execution: skill scripts and a new built-in `execute` tool, offered to the model only when a sandbox is configured. Workspace file tools stay on the host against the workspace store; the workspace is shared with the sandbox (inputs made available before a command, outputs copied back as governed workspace writes). The application's own Python tools, MCP servers, memory, telemetry, governance, and model calls stay outside. Credentials never enter the sandbox; only environment keys that governance authorizes do. |
| Code mode | A third lane using Monty, as an optional extra with an exact pin, behind OmniCoreAgent's own interface. Every tool call from code goes through the same governed runner as a direct call. |
| Default policy for execution (E3) | permissive-dev and interactive-dev allow sandboxed execution with the network off; a manifest that turns the network on asks for approval; strict-production needs an explicit rule. Host process execution is never allowed by default. |
| Skill scripts without a sandbox (E3) | They keep working on the host, asynchronously, governed as their own capability (`skill.script.run`) and recorded as host execution; with a sandbox configured they run in it; a strict policy can forbid host execution. |

## Definition of done

| # | Area | Must hold |
| --- | --- | --- |
| 1 | Interface | One backend interface (session lifecycle, `execute`, file upload and download, network policy, snapshot, terminate); the provider is chosen in configuration; tools that need execution are hidden when no backend can run them. |
| 2 | Governance | Every execution is authorized first (`process.exec` plus manifest scope: image, mounts, working directory, filesystem and network policy, environment keys, secrets, resources) through `SandboxExecutionService`; a decision that requires a sandbox never runs on the host. |
| 3 | Docker | A real Docker backend: network off by default, read-only root where possible, resource and time limits, cleanup with no leftover containers, bounded output. |
| 4 | Tools | `execute` and skill scripts run in the sandbox, asynchronously, with exit code, stdout, stderr, and duration; long output is offloaded like other tool results. |
| 5 | Workspace | Files the command needs are available inside the sandbox; files it creates or changes appear in the workspace as governed, recorded writes; nothing outside the mounted scope is readable. |
| 6 | Telemetry | Sandbox sessions and executions are recorded (provider, image, policy, command, exit code, duration, output capture states); the trajectory checklist holds for execution calls. |
| 7 | Code mode | `run_code` offers selected tools as functions; each call is governed and recorded under the `run_code` step; limits on time, memory, and calls; an `ask` decision pauses the program and resumes after approval; snapshots are signed; sessions are discarded after a limit fires. |
| 8 | Providers | E2B, Modal, Daytona, Cloudflare, and Vercel adapters pass the same contract tests as Docker (against local fakes of their APIs); live runs once keys exist. |
| 9 | Proof | The trajectory acceptance includes an `execute` call and a code-mode program; real Docker runs; the boundary audit covers host execution. |
| 10 | Docs | Execution guide (lanes, configuration, governance, telemetry), provider setup, and the model A deployment guide. |

## Working rules

The same as the previous plans: one unit at a time; failing test first; focused
tests, full suite, `ruff check`; log the result and commit hash below; commit
and push each unit. Docker tests use the real Docker daemon and are skipped
with a stated reason only where Docker is unavailable. Provider adapters are
tested against local fakes of each provider's API until keys exist.

## Units

### E1. Execution interface
- Complete `SandboxRuntime` as the one backend interface: session lifecycle,
  async `execute`, file upload and download, network policy, snapshot,
  terminate. Keep the `none` and test backends.
- Configuration selects the provider; `SandboxExecutionService` (PR #244) is the
  only route from the agent to a backend.

### E2. Docker backend
- Real containers through the Docker API: image, working directory, mounts,
  network off by default, CPU, memory, process and time limits, bounded output,
  cleanup on every path. Real-Docker tests, including a leftover-container check.

### E3a. Governance for execution
Split from E3 (2026-09-19): the default profiles' ask rules (`process.*`, and
any high-risk request) caught every sandboxed command, the policy language
could not express "not in a sandbox", and `run_skill_script` was governed as an
ordinary `tool.local.call` although it runs arbitrary scripts on the host.
- An `exclude_execution_surface` rule condition.
- Default profiles: permissive-dev and interactive-dev allow sandboxed
  `process.exec` and ask when a manifest turns the network on; host
  `process.exec` stays denied (permissive-dev) or asked (interactive-dev);
  skill scripts on the host (`skill.script.run`) are allowed in both;
  strict-production needs explicit rules.
- Skill tools and the sandbox `execute` tool get their own capabilities
  (`skill.script.run`, `skill.files.read`, `sandbox.execute`).

### E3b. `execute` tool and skill scripts
- A built-in `execute` tool, present only when a sandbox backend can execute;
  one sandbox session per run, closed when the run ends.
- `run_skill_script` runs asynchronously: in the sandbox when one is configured
  (the skill's files are copied in), otherwise on the host, governed as
  `skill.script.run` and recorded as host execution.
- Both routed through the governed tool runner and, in the sandbox, through
  `SandboxExecutionService`.

### E4. Workspace bridge
- Inputs from the workspace made available in the sandbox per the authorized
  mounts; outputs copied back as governed workspace writes; path containment
  tested.

### E5. Telemetry and trajectory for execution
- Sandbox session and execution spans and events; output capture follows the
  capture policy; the trajectory shows each execution with its result;
  run totals count executions.

### E6. Code mode (Monty)
- Optional extra `omnicoreagent[codemode]`, exact pin, behind an interface.
- `run_code` tool: selected tools exposed as functions; every call through the
  governed runner; results nested under the `run_code` step in the trajectory.
- Limits; discard on limit; approval pause and resume with signed snapshots.

### E7. Hosted adapters
- E2B, Modal, Daytona, Cloudflare, and Vercel adapters behind the same
  interface, each passing the shared contract tests against a local fake of its
  API; live runs once keys exist.

### E8. Proof and documentation
- Trajectory acceptance with `execute` and code mode; boundary audit updated;
  execution guide, provider setup, and model A deployment guide.

## Out of scope

Running the whole agent inside a sandbox as a runtime mode (documented only),
per-tool opt-in for the application's own tools, GPU sandboxes, and a
credential-injecting egress proxy (a later unit once providers support it).

## Execution log

| Unit | Status | Commit | Evidence |
| --- | --- | --- | --- |
| E1 | Complete | `d763710` | Bring your own sandbox: `register_sandbox_provider(name, factory)` and `registered_sandbox_providers()`; configuration is `{"provider": name, "options": {...}}`; an unknown name lists the registered ones; a name is not replaced by accident. Provider names are open (built-ins stay enum members, others are validated identifiers). Every backend states `supports_execution` (`none`: no, `local_test`: yes); `upload_files` / `download_files` are built on the single-file operations and can be overridden. `SandboxExecutionService` gains a session lifecycle: `open_session` authorizes the manifest scope once, `execute(..., session=...)` authorizes `process.exec` for every command and reuses the session, `close_session` terminates it, and a closed session refuses commands; the one-shot `execute` is unchanged. The agent exposes `can_execute` (the same check governance applies before a sandboxed action) and `sandbox_execution` (the governed route, or None); both are available after `initialize()`. Governance config validation accepts `options` and any registered provider. 7 new tests. Found during E1: (1) the fake provider servers kept HTTP connections alive, so a pooled connection could outlive a test and reach another server on a reused port; one full-suite run failed an LLM call with a 500 KB response a fake never sends; every fake response now closes its connection, and the combined provider and sandbox tests passed 4 of 4 repeated runs; (2) two tests used `docker` as an example of an unknown provider, which E2 makes valid; they use a name that cannot exist. Observed once under load, not related: `test_background_api_wait_true_times_out_before_slow_inline_run_finishes` got a 504 in a slow full-suite run (7.5 minutes) and passed 5 of 5 in isolation. Full suite 1,452 passed, 14 skipped; ruff clean. |
| E2 | Complete | `80fe83c` | New `sandbox/docker.py` (`DockerSandboxRuntime`, registered as `docker`, optional extra `omnicoreagent[docker]` with the Docker SDK 7.2.0). Each session is one container: no network unless allowed (a host allowlist is refused, since Docker cannot enforce one; that waits for an egress proxy), read-only root filesystem, writable working directory (anonymous volume removed with the container) and `/tmp`, all capabilities dropped, no privilege escalation, process limit (default 256), memory and CPU from the manifest, only the manifest environment. Commands run with `docker exec`; a time limit is enforced inside the container (`timeout -s KILL`, exit 137 reported as timed out) with a host backstop that stops the session if a command ignores it; stdout and stderr are bounded (default 1 MB each, truncation flagged); stdin goes through a temporary file. Files move with tar archives and must stay inside the working directory. Every SDK call runs in a worker thread. Orphan cleanup by label. 11 new tests against the real Docker daemon (skipped with a reason where it is unavailable), `alpine:3.20`: exit code and separate output; a command over its limit is killed and the session keeps working; only the loopback interface exists and an allowlist is refused; the root is read-only and the working directory writable; files round-trip and paths outside are refused; a host secret does not reach the container; memory and process limits read back from the cgroup; output bounded; terminate leaves no container and is idempotent; governed execution runs in Docker and cleans up. Zero sandbox containers left before and after the full suite. Full suite 1,464 passed, 14 skipped; ruff clean. |
| E3a | Complete | (this commit) | Policy rules gain an `exclude_execution_surface` condition. Default profiles per the decisions: permissive-dev and interactive-dev allow sandboxed `process.exec` (`allow_sandboxed_execution`, sandbox required) and sandbox setup (`sandbox.*`), ask to turn the sandbox network on (`ask_sandbox_network`) or mount host files (`ask_sandbox_host_mount`), and allow skill tools (`skill.*`); host `process.exec` stays denied (permissive-dev) or asked (interactive-dev); interactive-dev's `ask_process_exec` and `ask_high_risk` no longer apply inside a sandbox (every sandboxed command is high risk by design); strict-production is unchanged and needs explicit rules. Skill tools are marked as the `skill` provider and governed as `skill.script.run` / `skill.files.read` (before, running arbitrary host scripts passed as an ordinary `tool.local.call`); the sandbox `execute` tool is `sandbox.execute`. 23 new tests: every profile decision in the agreed table, the exclusion condition, the capability names, the skill provider marking, contained commands run under both dev profiles without a written policy, turning the network on needs approval, strict-production refuses without a rule. Full suite 1,487 passed, 14 skipped; ruff clean. |

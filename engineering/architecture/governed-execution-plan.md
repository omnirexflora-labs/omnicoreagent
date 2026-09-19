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

### E5b. Security hardening
Added 2026-09-19 at the maintainer's request after reports of agents escaping
their sandboxes. Governance is a decision layer; it only binds where every
side-effecting path passes it (complete mediation) and where executed code is
contained by real isolation.
- A written threat model, mapped to the failure classes of publicly reported
  agent sandbox escapes (research recorded with sources).
- A complete-mediation audit test: fails if any code path runs a process,
  writes a host file, or opens a connection outside the governed routes.
- Docker: non-root user by default; gVisor (`runsc`) as an option; no Docker
  socket or host credentials ever mounted.
- Refuse a policy file that lives inside a writable workspace or sandbox mount
  (an agent must not be able to edit its own policy).
- A warning (recorded in the trace) when execution or host skill scripts are
  available while governance is off.
- Documentation of the trust boundaries: the application's own tools run with
  host privileges; skill scripts without a sandbox are governed but not
  contained.

Done as five units, each tested and committed: E5b.1 complete-mediation audit
(and the gaps it finds); E5b.2 Docker hardening; E5b.3 policy file placement;
E5b.4 governance-off warning; E5b.5 threat model and trust-boundary docs.

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
| E3a | Complete | `6571b33` | Policy rules gain an `exclude_execution_surface` condition. Default profiles per the decisions: permissive-dev and interactive-dev allow sandboxed `process.exec` (`allow_sandboxed_execution`, sandbox required) and sandbox setup (`sandbox.*`), ask to turn the sandbox network on (`ask_sandbox_network`) or mount host files (`ask_sandbox_host_mount`), and allow skill tools (`skill.*`); host `process.exec` stays denied (permissive-dev) or asked (interactive-dev); interactive-dev's `ask_process_exec` and `ask_high_risk` no longer apply inside a sandbox (every sandboxed command is high risk by design); strict-production is unchanged and needs explicit rules. Skill tools are marked as the `skill` provider and governed as `skill.script.run` / `skill.files.read` (before, running arbitrary host scripts passed as an ordinary `tool.local.call`); the sandbox `execute` tool is `sandbox.execute`. 23 new tests: every profile decision in the agreed table, the exclusion condition, the capability names, the skill provider marking, contained commands run under both dev profiles without a written policy, turning the network on needs approval, strict-production refuses without a rule. Full suite 1,487 passed, 14 skipped; ruff clean. |
| E3b | Complete | `2436293` | New `sandbox/scope.py`: one sandbox session per agent run, opened by the first command that needs it, reused (files persist between commands), and closed when the run ends, including on cancellation (`complete_despite_cancellation`); a context variable, so concurrent runs and subagents are separate. New `execute` tool (`core/tools/execution_tools.py`, provider `sandbox`): offered only when the agent's sandbox can execute; runs `sh -c <command>` in the run's session through the governed service; bounded timeout; a non-zero exit or timeout is an error result. `run_skill_script` is now async: in the sandbox when the run has one (the skill's files are copied to `.skills/<name>/`, interpreters `python3`/`sh`), otherwise on the host with an async subprocess in its own process group, killed as a group on timeout, marked `execution_surface: host`; host interpreters unchanged (`bash`, the current Python). 8 new tests, Docker end to end: two `execute` calls share one sandbox and it is gone after the run, with `allow_sandboxed_execution` recorded for each command; not offered without a sandbox; a host skill script does not block the event loop; a script over its limit is killed within seconds; a skill name cannot reach a sibling directory; a skill runs in the sandbox; a cancelled run leaves no container. Found during E3b: (1) skill paths were checked with a string prefix, so `../skills-evil` passed for `/x/skills` in both the skill manager and the script check (now `is_relative_to`); (2) killing only the host script left its child holding the output pipes, so a timeout waited for the child (now the process group); (3) a run cancelled while Docker was creating the container left it orphaned (the backend now removes a container created during cancellation; the orphan from the failing run was confirmed by ID and removed); (4) the first version mapped `.sh` to `sh` on the host too, which would have broken skills using bash features (caught by the old skill tests). Old tests: the skill script tests call the tool asynchronously and the dispatcher test intercepts the async host runner. Research on agent sandbox escapes recorded in `engineering/validation/agent-sandbox-incidents.md`. Full suite 1,494 passed, 14 skipped; ruff clean; no containers left. |
| E4 | Complete | `fe7353a` | New `sandbox/workspace_bridge.py`. The sandbox never mounts the workspace: before each command, workspace files changed since the last copy are uploaded into the working directory (each allowed only if a `read_file` of it would be); after it, files the command created or changed are copied back (each allowed only if a `write_file` of it would be, with the workspace privacy filter applied). Governance sees the exact path read or written, not the tool-argument form with `files/`-style prefixes stripped. Untrusted output is bounded: links and hidden paths (`.skills`, stdin temp files) never come back, paths must stay inside the workspace, a file over 2 MB (default) or not UTF-8 text is skipped with the reason reported, at most 500 files and 20 MB per copy, and content is checked against the hash the sandbox listed. Only denials and approval requests skip a file; budget, audit and evaluation failures stop the command. Deletions are not propagated. `ExecutionScope` takes the bridge; the agent adds it when workspace files are enabled; the `execute` result reports `workspace_files` (written and skipped). 10 new tests (8 Docker end to end): files in and outputs back; a denied write is skipped and reported; a denied read never reaches the sandbox; links, hidden paths, oversized and binary files stay out; changes on either side reach the other between commands and unchanged files are not rewritten; privacy filter applied; the prefix case; the agent writes a file, sums it with `execute`, and reads the result, with `workspace.files.write` recorded in the trace. Found during E4: (1) local workspace writes put the lock and temp files beside the target named by stem (`notes.lock`, `notes.tmp`), so locks showed up among the files and writing `notes.txt` deleted an existing `notes.tmp` (data loss); locks now live in a hidden directory beside the namespace and temp files have unique hidden names; (2) the prefix mismatch above, caught by its test before it shipped; (3) a note here said workspace tool calls lacked trace events; that was wrong (corrected in E5): they record `workspace_read` / `workspace_write` / `workspace_delete`, which a debug filter on "tool" missed. Full suite 1,504 passed, 14 skipped; ruff clean; no containers left. |
| E5 | Complete | `82bce97` | Sandboxed execution is recorded by `SandboxExecutionService`, so every provider is recorded the same way (the test backend no longer emits its own events; its old test is replaced). A session records `sandbox_session_created` (provider, image, network, working directory) and `sandbox_session_closed` (commands run, duration, any terminate error). Each command records `sandbox_exec_started`, then `sandbox_exec_completed` or `sandbox_exec_failed` (non-zero exit, timeout, or a provider error or cancellation, with the error). Facts are metadata and kept under every capture policy: session, provider, execution ID, command name and argument count, exit code, timed out, duration, output sizes and truncation, whether the session was stopped, the decision ID and matched policy rules, and `purpose` (`command`, or `workspace_sync` for the bridge's own listing). The command and its stdout/stderr are payloads and now follow `record_tool_results` (before, a sandbox event's output was not covered by that switch). `sandbox_workspace_sync` records the paths copied in, written back, and skipped with reasons. Trajectory: each tool call lists its `executions` (the bridge's listings excluded) and `workspace_sync`; run totals gain `executions` (sessions, commands, failed, timed out) and workspace changes from the sandbox, each change marked `via` `tool` or `sandbox`; the evidence schema and observability docs are updated. Not covered: the one-shot `execute()` route (not used by the agent) records its command events but not session open and close. 4 new tests: the full event sequence with facts for success, non-zero exit, and a provider error; capture policy hides command output while keeping facts; the new event types are registered; an agent run with Docker shows both commands under their tool calls, the written file, and the totals. Full suite 1,507 passed, 14 skipped (the one failure in the first run was the published copy of the evidence schema, now synced); ruff clean; no containers left. |
| E5b.1 | Complete | `b81da98` | New `tests/test_complete_mediation.py`, which fails if governance stops binding: (1) a scan of the library for every call that can start a process or open a raw socket, which must equal a reviewed list with reasons (host skill scripts, the OAuth free-port check, and the MCP stdio launcher for application-configured servers); (2) a run under `strict-production` (no allow rules) where the agent tries an application tool, a workspace write, a host skill script, and a sandboxed command: every call is denied, no marker file or workspace file appears, a Python audit hook sees no process start, and no container is created; (3) the capability, execution surface, and risk every built-in tool presents to policy. Confirmed by reading the code: every tool call (local, MCP, workspace, skill, sandbox, subagent, discovery) goes through one dispatch point into `GovernedToolRunner`. Gaps found and fixed: (a) a governed agent could delegate to an ungoverned configured subagent, whose tools then ran with no policy (reproduced: the child's tool ran); delegation is now refused unless the child has governance enabled, and the sub-agents page says so; (b) spawned workers copied the parent's tools but kept only workspace and artifact provider labels, so a worker's `execute`, `run_skill_script`, and `read_skill_file` were governed as plain `tool.local.call` (low risk, allowed by every dev profile); all built-in labels are kept now; (c) host skill scripts were labelled risk `low` and surface `tool`, and the `execute` tool surface `tool`, so a policy could not single out code running on the host; `run_skill_script` is now risk `high` with surface `host` (or `sandbox` when the run has one), `read_skill_file` surface `host`, `execute` risk `high` with surface `sandbox`. To keep the agreed decision that skill scripts keep working on the host in the dev profiles, interactive-dev's `ask_high_risk` rule excludes `skill.script.run` through a new `exclude_capability` rule condition (to revisit once approvals can pause and resume). Correction to an earlier remark: delegation already had its own `subagent.spawn` check. 12 new tests; decision tests use the real labels. Full suite 1,519 passed, 14 skipped; ruff clean; no containers left. |
| E5b.2 | Complete | `ecb6dae` | Docker backend hardening. (1) Commands run as an unprivileged user by default (`65534:65534`, `HOME=/tmp`); another user must be numeric so the working directory can belong to it, and root only runs when configured (`user="0:0"`). (2) The working directory is an anonymous in-memory volume (local driver, tmpfs, owned by the sandbox user, mode 0700, removed with the container), size-limited by `workdir_size` (default 1 GB); a plain tmpfs mount was tried first and rejected because Docker cannot copy files into one. Uploaded files are owned by the sandbox user so its commands can change them. (3) gVisor: `runtime="runsc"` runs the container under gVisor; a runtime Docker does not have is a clear `SandboxUnsupportedError` and leaves no container (gVisor is not installed on the development machine, so only that path is tested; the gVisor run is written and skips itself without it). (4) Host mounts that would hand over the host are refused by the backend before any container exists, whatever the policy allows: container engine sockets, `/`, system directories (`/etc`, `/proc`, `/sys`, `/dev`, `/run`, `/var/lib/docker`, ...), credential directories in the user's home (`.ssh`, `.aws`, `.docker`, `.kube`, `.config`, ...) or any path inside one, and any directory containing one (so the home directory itself); links are resolved first; URI sources are refused. Before this, every one of those mounts succeeded, including the Docker socket (confirmed by the new tests failing first). An ordinary project directory mounts read-only; it must be readable by the sandbox user. Not changed: `snapshot()` commits the image, so it does not capture the in-memory working directory (for D5, sandbox continuity). 18 new tests against real Docker. Full suite 1,537 passed, 14 skipped; ruff clean; no containers or volumes left. |
| E5b.3 | Complete | `5b2939e` | An agent must not be able to edit its own policy. The existing check refused a policy file under directories named like agent output (`workspace`, `tmp`, `outputs`, ...); it missed the real places the agent writes. Now a policy file inside the agent's configured local workspace directory, whatever it is called, is refused when the governance engine is built (`PolicyLoadError`, for agents and for OmniServe, which build the engine the same way). A sandbox session or one-shot command whose host mount is read-write and contains the policy file is refused (`SandboxUnsupportedError`); read-only is allowed. Both reproduced first: a policy at `agentdata/files/policy.json` with `agentdata` as the workspace loaded, and a read-write mount over the policy opened. Policies given in code have no file and are unaffected. 4 new tests. Full suite 1,541 passed, 14 skipped (the 4 failures in that run are the next unit's tests, written first). One earlier run of this suite ended in a Python fatal error in a native extension; its details were lost (the output was cut to its last lines) and the rerun did not reproduce it; this is the second such crash seen this session, so full suite output is now kept. |

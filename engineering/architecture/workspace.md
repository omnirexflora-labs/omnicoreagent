# Workspace Architecture

This is an internal architecture record under `engineering/architecture`, not
public product documentation. It is the source of truth for OmniCoreAgent
workspace design.

Keep public docs thin. Keep detailed design decisions, runtime boundaries,
debugging paths, and coding-agent instructions here.

This file is not a list of suggestions. When workspace code changes, the
implementation, tests, prompt contract, and this document must stay aligned.
Read this before changing workspace storage, workspace files, tool offloading,
artifact tools, subagent output paths, or runtime tool registration.

The architecture design means the full internal blueprint:

- why workspace exists in the agent harness
- what runtime state belongs inside it
- which code owns each part
- how local, S3, and R2 storage are selected
- how workspace tools are registered into the agent loop
- how workspace command tools expose familiar file navigation, search, and edit operations
- how tool offloading writes and reads artifacts
- how subagents write output for the lead agent to read
- what workspace must not own
- what the prompt must tell the model
- which invariants and tests must be preserved

The workspace is not just a place to save files. It is a harness capability:
the durable filesystem boundary added around a model so long-running agents can
work beyond a single prompt, survive context cleanup, coordinate subagents, and
re-read large tool outputs without flooding the model context.

---

## Purpose

The workspace exists to solve four harness problems.

| Problem | Workspace answer |
|---------|------------------|
| Agents lose progress when context is compressed or cleaned | Write plans, progress, todos, and notes to `files/` |
| Subagents need to return useful output without dumping everything into context | Write each worker output to `files/`, then the lead agent reads it |
| Large tool results make the loop slow and expensive | Offload large tool responses to `artifacts/` with a small preview in context |
| Local and cloud deployments must behave the same | Route both `files/` and `artifacts/` through one `Workspace` facade |

The design rule is:

```text
Anything filesystem-like belongs inside the workspace.
```

That includes agent notes, scratchpads, task progress, subagent outputs, tool
offload artifacts, logs, generated text files, and future file-like runtime
state.

---

## Terminology

Use these names consistently.

| Term | Meaning |
|------|---------|
| Workspace | Runtime facade that owns all filesystem-like harness state |
| Workspace storage driver | The selected storage implementation for workspace data: local, S3, or R2 |
| `files/` | Agent-managed durable files created intentionally by the model |
| `artifacts/` | Runtime-managed large tool outputs created automatically by offloading |
| Workspace command tools | Built-in local tools the model uses to list, read, search, create, edit, move, or delete files under `files/` |
| Artifact tools | Tools the model uses to inspect offloaded content under `artifacts/` |

Do not call `workspace_backend` a memory backend. It is the workspace storage
driver selector. Memory stores and workspace storage are separate systems.

---

## Architecture Invariants

These invariants are mandatory.

| Invariant | Reason |
|-----------|--------|
| Workspace owns filesystem-like harness state | Avoid scattered file/storage behavior across tools, runtime, and agents |
| One workspace storage driver controls both `files/` and `artifacts/` | Local, S3, and R2 must be selected once and behave consistently |
| `files/` is agent-managed state | The model can intentionally create, read, update, and organize these files |
| `artifacts/` is runtime-managed output | Tool offloading writes here automatically; the model reads through artifact tools |
| Workspace access tool outputs stay inline | Retrieval tools must not recursively offload their own retrieved content |
| Workspace storage is selected with `workspace_backend` | The name must stay distinct from memory backends, telemetry streams, and MCP transports |
| Runtime tools register through the same tool registry path as local tools | The model must call workspace tools like any other local/internal tool |
| Workspace command tools are storage-backed, not shell-backed | `ls`, `read_file`, `grep`, and related tools must work the same on local, S3, and R2 |
| Workspace command tool names are reserved while workspace files are enabled | Avoid silent conflict between app local tools and built-in harness tools |
| New files under `src/omnicoreagent/core/workspace` must be tracked explicitly | `.gitignore` ignores directories named `workspace/`; missing files break CI |

---

## Two Workspace Areas

The workspace has exactly two primary runtime areas.

```text
workspace/
  files/
  artifacts/
```

### `files/`

`files/` is agent-managed durable state.

The agent uses `files/` for:

- scratchpads
- plans
- task progress
- todos
- user preferences the agent chooses to persist as files
- notes
- logs
- generated text
- research summaries
- subagent outputs

The agent interacts with this area through workspace command tools.

Example paths:

```text
/workspace/tasks/refactor/plan.md
/workspace/tasks/refactor/progress.md
/workspace/subagents/api/output.md
/workspace/notes/user_preferences.md
```

The path prefixes `/workspace/...`, `/files/...`, and `...` are accepted by the
workspace command tools and normalized into the `files/` area.

### `artifacts/`

`artifacts/` is runtime-managed tool output.

Use it for:

- large MCP tool responses
- large local tool responses
- large API results
- long logs returned by tools
- any tool result that must remain available later without staying fully in model context

The agent does not write this area directly with workspace command tools.
Tool offloading writes here automatically, and the agent reads it with artifact
tools:

```text
read_artifact
tail_artifact
search_artifact
list_artifacts
```

This separation matters because `files/` is intentional agent state, while
`artifacts/` is runtime output captured for context efficiency.

---

## What Workspace Does Not Own

Workspace is the filesystem boundary, not every persistence system in the
runtime.

Workspace must not own:

- LLM provider configuration
- MCP server configuration
- local tool definitions
- tool registry mechanics
- conversation memory stores such as Redis, Postgres, Mongo, or vector memory
- telemetry streams
- tracing, evaluation, or observability systems
- prompt assembly itself

Those systems may read from or write to the workspace when they produce
filesystem-like state, but their core behavior belongs in their own modules.

The practical rule:

```text
If the runtime needs a durable file or file-like object, use workspace.
If the runtime needs a database, transport, provider, registry, or event stream,
do not hide that inside workspace.
```

---

## Workspace Storage Driver

The Python config field that selects the workspace storage driver is
intentionally named `workspace_backend`.

```python
WorkspaceConfig(workspace_backend="local")
WorkspaceConfig(workspace_backend="s3")
WorkspaceConfig(workspace_backend="r2")
```

Do not shorten this to `backend`. OmniCoreAgent has several systems that can
have their own backends or transports: memory stores, telemetry streams, MCP
transports, and workspace storage. The field name must say that it selects the
workspace storage driver only.

The environment variable is already scoped and remains:

```bash
OMNICOREAGENT_WORKSPACE_BACKEND=local
```

Default local behavior:

```text
workspace_backend = local
workspace_dir = ./workspace
prefix = workspace
```

Cloud behavior:

```text
workspace_backend=s3 -> s3://bucket/<prefix>/files and s3://bucket/<prefix>/artifacts
workspace_backend=r2 -> s3-compatible R2 keys under <prefix>/files and <prefix>/artifacts
```

The app chooses one workspace storage driver. Both `files/` and `artifacts/`
use it. There is no separate workspace-files driver and no separate artifact
driver.

---

## Code Ownership

Workspace code lives under `src/omnicoreagent/core/workspace`.

```text
core/workspace/
  __init__.py        # Public workspace helpers and lazy Workspace export
  config.py          # WorkspaceConfig and env resolution
  manager.py         # Workspace facade with files and artifacts
  storage.py         # Local, S3, and R2 storage primitives
  paths.py           # Namespace constants and path normalization
  files.py           # Agent-managed file operations
  factory.py         # Workspace file adapter creation/cache
  tools.py           # workspace command tool registration
  artifacts.py       # ToolResponseOffloader
  artifact_tools.py  # read_artifact/tail/search/list tool registration
  offload_policy.py  # Tools whose outputs must remain inline
```

`core/tools` must not own workspace behavior. It owns generic tool execution,
batching, observation formatting, registries, and prompt rendering. Workspace
tools are registered into the tool registry from the workspace package, but the
workspace package owns the behavior.

Ownership boundary:

| Package | Owns |
|---------|------|
| `core/workspace` | Workspace facade, storage drivers, file tools, artifacts, path safety, offload policy |
| `core/tools` | Generic tool registry, tool dispatch, batching, observation formatting |
| `core/runtime` | Runtime assembly and wiring of configured capabilities |
| `core/agents` | Agent loop, prompt use, tool calling, subagent orchestration |

Crossing these boundaries should be explicit. For example, runtime may call
workspace builders to register workspace tools, but `core/tools` should not
reimplement workspace storage or path rules.

---

## Runtime Data Flow

### Workspace construction

`Workspace.from_config(...)` resolves a `WorkspaceConfig` and creates two
storage handles:

```python
workspace = Workspace.from_config(config)
workspace.files      # files namespace
workspace.artifacts  # artifacts namespace
```

For local:

```text
workspace.files      -> ./workspace/files
workspace.artifacts  -> ./workspace/artifacts
```

For S3/R2:

```text
workspace.files      -> <prefix>/files
workspace.artifacts  -> <prefix>/artifacts
```

### Runtime tool registration

When OmniCoreAgent prepares tools:

1. User local tools can already exist in a `ToolRegistry`.
2. Runtime/internal tools are added to that same registry.
3. Workspace command tools are registered when workspace files are enabled.
4. Artifact tools are registered when `tool_offload.enabled` is true.

This preserves the existing local-tool model: internal tools are normal tools
from the agent's point of view. The model calls them through the same loop as
user tools and MCP tools.

Workspace files are enabled by default. Disabling them should be a deliberate
runtime choice, because subagent output, durable task progress, and long-running
agent work depend on this capability.

### Shared workspace binding

The runtime binds workspace command tools and the tool offloader to the same
`Workspace` object when runtime tools are prepared.

Why this matters:

- `write_file`, `edit_file`, `grep`, and the other workspace command tools use
  `workspace.files`.
- `ToolResponseOffloader` uses `workspace.artifacts`.
- Both areas come from the same config and backend.
- Local, S3, and R2 behavior stays equivalent.

### Workspace command tools

Workspace command tools are built-in local tools. They are not a host shell and
they do not call `/bin/bash`. They expose familiar file operations over the
active workspace storage driver.

Canonical tool names:

```text
ls
read_file
write_file
edit_file
insert_file
delete_file
move_file
clear_files
glob
grep
```

These names are intentionally close to the commands and file tools that models
already know. The purpose is to improve tool selection and make workspace usage
natural without asking app builders to define basic file navigation tools.

Tool meaning:

| Tool | Purpose |
|------|---------|
| `ls` | List immediate children under a workspace path |
| `read_file` | Read a workspace file, equivalent to `cat` for agent use |
| `write_file` | Create, append, or overwrite a workspace file |
| `edit_file` | Replace text in a workspace file |
| `insert_file` | Insert text at a line number |
| `delete_file` | Delete a file or directory |
| `move_file` | Rename or move a file or directory |
| `clear_files` | Clear only the `files/` namespace |
| `glob` | Find workspace paths by glob pattern |
| `grep` | Search text inside workspace files |

`read_file` is the canonical read tool. Do not add a separate `cat` tool unless
there is strong evidence that alias improves model behavior without increasing
prompt noise. The prompt may explain that `read_file` is the workspace equivalent
of `cat`.

`grep` and `glob` are first-class tools because search and path discovery are
not the same as reading a known file.

These tools must be registered only when workspace files are enabled. Workspace
files are enabled by default, so the tool names above are reserved by the
harness in that mode. If an application needs custom local tools with the same
names, it should disable built-in workspace files or use distinct domain tool
names. The runtime must not silently overwrite an app's tool or silently hide a
built-in workspace tool.

Full command execution is outside this workspace command tool scope.

Do not add:

```text
execute
run_shell
bash
sh
python
npm
pip
```

Those belong to a later sandbox and immutable policy phase.

---

## Tool Offloading

Tool offloading is part of workspace architecture, not a separate filesystem.

Flow:

1. A tool returns a large response.
2. `ToolObservationFormatter` checks offload policy.
3. If the result is large enough, `ToolResponseOffloader` writes the full
   content into `workspace.artifacts`.
4. The model receives only a preview plus an artifact id.
5. The model calls `read_artifact`, `tail_artifact`, `search_artifact`, or
   `list_artifacts` to retrieve what it needs.

The offloaded result message must be small enough to keep the ReAct loop fast
but useful enough for the model to decide whether it needs the full artifact.

---

## Failure Contract

Workspace failures must be visible and local to the failing operation.

Rules:

- Unsafe paths fail before reaching storage.
- Missing files return clear tool errors.
- Workspace file write/edit/delete errors return tool errors.
- Artifact read/search/tail errors return tool errors.
- Tool offloading must not silently pretend content was saved when storage
  failed.
- Local and S3/R2 failures should use the same logical error shape where
  practical.

The agent loop can decide what to do with a tool error. The workspace layer must
not hide failed writes, path escapes, missing files, or storage failures behind
successful-looking messages.

---

## Inline Output Policy

Some tools exist only to retrieve workspace content. Their outputs must not be
offloaded again, otherwise the agent can get stuck in recursive retrieval.

These built-in tool providers stay inline:

```text
workspace
artifact
```

The policy lives in:

```text
core/workspace/offload_policy.py
```

Keep this policy centralized. Do not recreate private tool-name checks inside
tool observation code. Natural names such as `read_file` and `grep` can exist
as app-local tools when workspace files are disabled, so inline behavior must be
based on the resolved built-in provider, not only the tool name.

---

## Path Safety

Path normalization lives in:

```text
core/workspace/paths.py
```

Rules:

- URL-encoded paths are decoded.
- Leading slashes are removed.
- Supported workspace prefixes are stripped.
- `.` path segments are ignored.
- `..` path segments are rejected.
- Local paths are resolved and checked to stay inside the namespace root.
- S3/R2 keys are normalized with the same logical rules.

The goal is that local and cloud storage reject the same unsafe paths.

---

## Subagent Output

Dynamic subagents use workspace files as their output contract.

Flow:

1. Lead agent calls `spawn_subagents` with one or more specs.
2. Each spec includes an `output_path`.
3. Each subagent writes its work with `write_file`.
4. The lead agent reads those paths with `read_file`.
5. The lead agent synthesizes from the saved outputs.

This keeps subagent output durable and avoids forcing every worker to return a
large payload directly into the parent context.

Canonical path shape:

```text
/workspace/subagents/<name>/output.md
/workspace/tasks/<task-name>/subagents/<name>.md
```

---

## Prompt Contract

The system prompt must explain the two workspace areas clearly.

The model must understand:

- `files/` is for agent-managed files.
- `artifacts/` is for runtime-managed large tool outputs.
- Workspace command tools are storage-backed file tools, not a host shell.
- Use `ls` and `glob` to discover files.
- Use `grep` to search files before reading large or many files.
- Use `read_file` to read a known file path.
- Use `write_file`, `edit_file`, and `insert_file` to update files.
- Workspace files are useful for multi-step work, parallel work, context
  recovery, and durable task state.
- For substantial multi-step work, the agent should write plans, progress, and
  important intermediate outputs to workspace files.
- Artifact tools are used when a tool response was offloaded.
- Subagent outputs are written to workspace files and read by the lead
  agent before synthesis.
- Workspace paths should be stable and meaningful enough for the agent to
  re-open them later.

Do not describe workspace as just "file storage". The purpose is harness
reliability: durability, context control, subagent coordination, and repeatable
task state.

---

## Tests To Read Before Changing Workspace

Start with these tests:

```text
tests/test_workspace.py
tests/test_workspace_files_backend.py
tests/test_tool_response_offloader.py
tests/test_tool_runtime_registry.py
tests/test_tool_observation.py
tests/test_prompt_context.py
tests/test_subagents.py
tests/test_import_startup.py
```

What each covers:

| Test file | Purpose |
|-----------|---------|
| `test_workspace.py` | Config, facade, local/S3 path behavior, path safety |
| `test_workspace_files_backend.py` | Workspace file operations and local/S3 behavior |
| `test_tool_response_offloader.py` | Artifact offloading, retrieval, metadata, shared workspace binding |
| `test_tool_runtime_registry.py` | Runtime registration of workspace and artifact tools |
| `test_tool_observation.py` | Offload decisions and inline retrieval outputs |
| `test_prompt_context.py` | Prompt sections for workspace/artifacts |
| `test_subagents.py` | Subagent output contract through workspace files |
| `test_import_startup.py` | Startup cost and lazy imports |

Run at least:

```bash
uv run ruff check src tests
uv run pytest -q tests/test_workspace.py tests/test_workspace_files_backend.py tests/test_tool_response_offloader.py tests/test_tool_runtime_registry.py tests/test_tool_observation.py tests/test_prompt_context.py tests/test_subagents.py tests/test_import_startup.py
uv run pytest -q
```

For CI parity:

```bash
uv lock --check
uv run pytest -v tests -m "not requires_api_key and not requires_network and not broken_upstream"
```

Also check that every new source file under `src/omnicoreagent/core/workspace`
is tracked by Git. The repo ignores directories named `workspace/`, so new
workspace package files require explicit staging when they are new:

```bash
git ls-files src/omnicoreagent/core/workspace
```

If a new file is missing from that list, CI will not receive it.

---

## Design Decisions

### Why workspace owns command tools

Workspace command operations used to live under `core/tools/workspace_files`.
That made `core/tools` a dump ground for behavior that actually belongs to the
workspace domain. The tool registry is only the exposure mechanism. The behavior
belongs under `core/workspace`.

### Why artifacts live in workspace

Tool offloading creates files. Those files must obey the same local/S3/R2
selection as every other workspace file-like thing. Keeping artifacts outside
workspace would recreate split storage decisions and make production behavior
harder to reason about.

### Why `workspace_backend`

The codebase also has memory backends, telemetry streams, and MCP transports. A
generic field named `backend` is ambiguous. `workspace_backend` states that the
selected driver is for workspace storage only.

### Why no compatibility alias

This cleanup intentionally removes old internal shapes instead of preserving
compatibility shims. The project is still being cleaned toward a stable
architecture. Keeping aliases for old names would keep the old architecture
alive and make future debugging harder.

### Why local is default

A new user gets a working workspace without S3, R2, Redis, Postgres, or
other infrastructure. Local disk is the simplest correct default. Production can
switch the same abstraction to S3/R2 with config.

---

## Mandatory Change Checklist

Before editing workspace code, check:

- Am I changing `files/`, `artifacts/`, or both?
- Is this agent-managed state or runtime-managed output?
- Does local behavior match S3/R2 behavior?
- Does the path normalization still reject traversal?
- Are retrieval tool outputs still kept inline?
- Are workspace command tools still registered into the same registry as local tools?
- Are subagent outputs still written to readable workspace paths?
- Did I update prompt/docs if the model contract changed?
- Did I run focused tests and full tests?
- Are all new `core/workspace` files tracked by Git?

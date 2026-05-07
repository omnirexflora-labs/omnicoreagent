# Workspace Architecture

This is an internal architecture record, not public product documentation. Keep
public docs thin; keep detailed design decisions, debugging paths, and coding
agent instructions here.

This file is the source of truth for workspace architecture. It is not a list
of suggestions. When workspace code changes, the implementation, tests, prompt
contract, and this document must stay aligned.

This page is the design record for OmniCoreAgent workspace architecture. Read it
before changing workspace storage, workspace files, tool offloading, artifact
tools, subagent output paths, or runtime tool registration.

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

## Architecture Invariants

These invariants are mandatory.

| Invariant | Reason |
|-----------|--------|
| Workspace owns filesystem-like harness state | Avoid scattered file/storage behavior across tools, runtime, and agents |
| One workspace backend controls both `files/` and `artifacts/` | Local, S3, and R2 must be selected once and behave consistently |
| `files/` is agent-managed state | The model can intentionally create, read, update, and organize these files |
| `artifacts/` is runtime-managed output | Tool offloading writes here automatically; the model reads through artifact tools |
| Workspace access tool outputs stay inline | Retrieval tools must not recursively offload their own retrieved content |
| Generic Python config name `backend` is forbidden for workspace | It is ambiguous with memory backends, event backends, and MCP transports |
| Runtime tools register through the same tool registry path as local tools | The model must call workspace tools like any other local/internal tool |
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

The agent interacts with this area through `workspace_file_*` tools.

Example paths:

```text
/workspace/tasks/refactor/plan.md
/workspace/tasks/refactor/progress.md
/workspace/subagents/api/output.md
/workspace/notes/user_preferences.md
```

The path prefixes `/workspace/...`, `/files/...`, and `...` are accepted by the
workspace file tools and normalized into the `files/` area.

### `artifacts/`

`artifacts/` is runtime-managed tool output.

Use it for:

- large MCP tool responses
- large local tool responses
- large API results
- long logs returned by tools
- any tool result that must remain available later without staying fully in model context

The agent does not write this area directly with `workspace_file_*` tools.
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

## Workspace Backend

The Python config field is intentionally named `workspace_backend`.

```python
WorkspaceConfig(workspace_backend="local")
WorkspaceConfig(workspace_backend="s3")
WorkspaceConfig(workspace_backend="r2")
```

Do not use a generic `backend` field. OmniCoreAgent has several backend-like
systems: memory stores, event streams, MCP transports, and workspace storage.
The name must say which system is being selected.

The environment variable is already scoped and remains:

```bash
OMNICOREAGENT_WORKSPACE_BACKEND=local
```

Default behavior:

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

The app chooses one workspace backend. Both `files/` and `artifacts/` use it.
There is no separate workspace-files backend and no separate artifact backend.

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
  tools.py           # workspace_file_* tool registration
  artifacts.py       # ToolResponseOffloader
  artifact_tools.py  # read_artifact/tail/search/list tool registration
  offload_policy.py  # Tools whose outputs must remain inline
```

`core/tools` must not own workspace behavior. It owns generic tool execution,
batching, observation formatting, registries, and prompt rendering. Workspace
tools are registered into the tool registry from the workspace package, but the
workspace package owns the behavior.

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
2. Runtime/internal tools are added to that same registry when enabled.
3. Workspace file tools are registered when `enable_workspace_files` is true.
4. Artifact tools are registered when `tool_offload.enabled` is true.

This preserves the existing local-tool model: internal tools are normal tools
from the agent's point of view. The model calls them through the same loop as
user tools and MCP tools.

### Shared workspace binding

The runtime binds workspace file tools and the tool offloader to the same
`Workspace` object when runtime tools are prepared.

Why this matters:

- `workspace_file_write` uses `workspace.files`.
- `ToolResponseOffloader` uses `workspace.artifacts`.
- Both areas come from the same config and backend.
- Local, S3, and R2 behavior stays equivalent.

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

## Inline Output Policy

Some tools exist only to retrieve workspace content. Their outputs must not be
offloaded again, otherwise the agent can get stuck in recursive retrieval.

These outputs stay inline:

```text
read_artifact
tail_artifact
search_artifact
list_artifacts
workspace_file_view
workspace_file_write
workspace_file_replace
workspace_file_insert
workspace_file_delete
workspace_file_rename
workspace_file_clear
```

The policy lives in:

```text
core/workspace/offload_policy.py
```

Keep this policy centralized. Do not recreate a private frozen set inside tool
observation code.

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
3. Each subagent writes its work with `workspace_file_write`.
4. The lead agent reads those paths with `workspace_file_view`.
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
- Workspace files are useful for multi-step work, parallel work, context
  recovery, and durable task state.
- Artifact tools are used when a tool response was offloaded.
- Subagent outputs are written to workspace files and read by the lead
  agent before synthesis.

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

### Why workspace owns file tools

Workspace file operations used to live under `core/tools/workspace_files`.
That made `core/tools` a dump ground for behavior that actually belongs to the
workspace domain. The tool registry is only the exposure mechanism. The behavior
belongs under `core/workspace`.

### Why artifacts live in workspace

Tool offloading creates files. Those files must obey the same local/S3/R2
selection as every other workspace file-like thing. Keeping artifacts outside
workspace would recreate split storage decisions and make production behavior
harder to reason about.

### Why `workspace_backend`

The codebase also has memory backends and event backends. A generic field named
`backend` is ambiguous. `workspace_backend` states exactly what is selected.

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
- Are workspace tools still registered into the same registry as local tools?
- Are subagent outputs still written to readable workspace paths?
- Did I update prompt/docs if the model contract changed?
- Did I run focused tests and full tests?
- Are all new `core/workspace` files tracked by Git?

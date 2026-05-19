# Workspace Specification

This is the behavior contract for OmniCoreAgent workspace. The architecture
record explains why the workspace exists and how it is owned. This specification
defines what the implementation must do.

Read this with:

- `engineering/architecture/workspace.md`
- `src/omnicoreagent/core/workspace`
- `tests/test_workspace.py`
- `tests/test_workspace_files_backend.py`
- `tests/test_tool_response_offloader.py`
- `tests/test_tool_runtime_registry.py`

When this specification changes, implementation and tests must change in the
same PR.

---

## Scope

This specification covers:

- workspace configuration
- workspace storage drivers
- workspace namespaces
- path normalization and path safety
- workspace command tools
- artifact tools
- tool response offloading
- runtime tool registration
- expected error behavior
- required tests

This specification does not cover:

- conversation memory backends
- LLM provider configuration
- MCP server configuration
- local tool authoring
- tracing, evaluation, or observability
- public documentation wording

---

## Configuration Contract

### `WorkspaceConfig`

`WorkspaceConfig` is the only workspace storage config object.

Fields:

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `workspace_backend` | `str` | `"local"` | Workspace storage driver: `local`, `s3`, or `r2` |
| `workspace_dir` | `str | Path | None` | `"./workspace"` | Local workspace root |
| `prefix` | `str` | `"workspace"` | S3/R2 object key prefix |
| `s3_bucket` | `str | None` | `None` | S3 bucket name |
| `aws_region` | `str | None` | `None` | AWS region |
| `aws_access_key_id` | `str | None` | `None` | Optional S3 access key |
| `aws_secret_access_key` | `str | None` | `None` | Optional S3 secret key |
| `aws_endpoint_url` | `str | None` | `None` | Optional S3-compatible endpoint |
| `r2_bucket_name` | `str | None` | `None` | R2 bucket name |
| `r2_account_id` | `str | None` | `None` | R2 account id |
| `r2_access_key_id` | `str | None` | `None` | R2 access key |
| `r2_secret_access_key` | `str | None` | `None` | R2 secret key |

Normalization rules:

- `workspace_backend` is lowercased and stripped.
- `prefix` is stripped of leading and trailing `/`.
- `workspace_dir` is converted to `str` when not `None`.
- `backend` is not accepted as an alias.

Required tests:

- `tests/test_workspace.py::test_workspace_config_from_env_normalizes_values`
- `tests/test_workspace.py::test_workspace_config_uses_workspace_backend_name_only`

### Environment Variables

`WorkspaceConfig.from_env()` reads:

| Environment variable | Config field |
|----------------------|--------------|
| `OMNICOREAGENT_WORKSPACE_BACKEND` | `workspace_backend` |
| `OMNICOREAGENT_WORKSPACE_DIR` | `workspace_dir` |
| `OMNICOREAGENT_WORKSPACE_PREFIX` | `prefix` |
| `AWS_S3_BUCKET` | `s3_bucket` |
| `AWS_REGION` | `aws_region` |
| `AWS_ACCESS_KEY_ID` | `aws_access_key_id` |
| `AWS_SECRET_ACCESS_KEY` | `aws_secret_access_key` |
| `AWS_ENDPOINT_URL` | `aws_endpoint_url` |
| `R2_BUCKET_NAME` | `r2_bucket_name` |
| `R2_ACCOUNT_ID` | `r2_account_id` |
| `R2_ACCESS_KEY_ID` | `r2_access_key_id` |
| `R2_SECRET_ACCESS_KEY` | `r2_secret_access_key` |

If no config is passed, workspace config resolves from environment.

---

## Workspace Facade Contract

`Workspace.from_config(...)` creates one workspace facade with two storage
handles.

```python
workspace = Workspace.from_config(config)
workspace.files
workspace.artifacts
```

For local storage:

```text
workspace.files      -> <workspace_dir>/files
workspace.artifacts  -> <workspace_dir>/artifacts
```

For S3/R2:

```text
workspace.files      -> <prefix>/files
workspace.artifacts  -> <prefix>/artifacts
```

`Workspace.ensure()` must ensure both namespace roots exist where the storage
driver supports root creation.

Required tests:

- `tests/test_workspace.py::test_workspace_facade_exposes_files_and_artifacts`
- `tests/test_workspace.py::test_workspace_storage_namespaces_share_one_workspace_root`
- `tests/test_workspace.py::test_ensure_workspace_creates_runtime_directories`

---

## Storage Driver Contract

### Supported Drivers

The only valid `workspace_backend` values are:

```text
local
s3
r2
```

Invalid values must raise:

```text
ValueError("workspace_backend must be one of: local, s3, r2")
```

### Local Driver

Local storage writes under:

```text
<workspace_dir>/<namespace>
```

For normal runtime workspace:

```text
./workspace/files
./workspace/artifacts
```

Local storage requirements:

- create namespace root on initialization
- keep every resolved path inside namespace root
- use UTF-8 text reads and writes
- use file locks for read/write/append/delete/rename operations where currently implemented
- write atomically by default using a temp file and rename
- support direct non-atomic write when `atomic=False`
- list only immediate children of a directory
- represent directories with `is_dir=True`
- clear only the namespace root contents, not the workspace root

Required tests:

- `tests/test_workspace.py::test_local_workspace_storage_keeps_paths_inside_root`
- `tests/test_workspace.py::test_local_workspace_storage_rejects_path_traversal`
- `tests/test_workspace.py::test_local_workspace_storage_strips_namespace_prefix`

### S3 Driver

S3 storage writes under:

```text
s3://<bucket>/<prefix>/<namespace>/<path>
```

S3 requirements:

- require `AWS_S3_BUCKET`
- use optional `AWS_REGION`
- use optional `AWS_ENDPOINT_URL`
- use optional access key and secret key when both are present
- use server-side AES256 encryption by default
- normalize object keys with the same logical path rules as local storage
- list immediate children using `Delimiter="/"`.
- delete a file when the exact key exists
- delete a directory by deleting objects under the directory prefix

Required tests:

- `tests/test_workspace.py::test_workspace_storage_accepts_explicit_s3_config`
- `tests/test_workspace.py::test_s3_workspace_storage_reads_writes_and_lists_files`
- `tests/test_workspace.py::test_s3_workspace_storage_rejects_path_traversal`
- `tests/test_workspace.py::test_s3_workspace_storage_delete_and_rename`

### R2 Driver

R2 is S3-compatible storage with Cloudflare endpoint behavior.

R2 requirements:

- require `R2_BUCKET_NAME`
- require `R2_ACCOUNT_ID`
- require `R2_ACCESS_KEY_ID`
- require `R2_SECRET_ACCESS_KEY`
- use endpoint URL:

```text
https://<account_id>.r2.cloudflarestorage.com
```

- use region `auto`
- disable AES256 server-side encryption parameter
- use the same prefix and namespace behavior as S3

Missing R2 variables must raise a `ValueError` listing the missing environment
variable names.

---

## Namespace Contract

Workspace has two primary runtime namespaces:

| Namespace | Owner | Purpose |
|-----------|-------|---------|
| `files` | Agent/model | Durable files intentionally created or edited by the agent |
| `artifacts` | Runtime | Large tool outputs offloaded automatically |

There is also a `config` namespace used by legacy helper path functions for
workspace runtime directories.

`files` and `artifacts` must use the same `WorkspaceConfig` and the same
`workspace_backend`.

There must not be separate config fields for workspace files storage and
artifact storage.

---

## Path Contract

Path normalization is owned by:

```text
src/omnicoreagent/core/workspace/paths.py
```

`normalize_workspace_path(path, strip_prefixes=...)` must:

- return `""` for `None`, empty string, or whitespace-only string
- URL-decode the input path
- strip leading `/`
- strip the first matching prefix passed in `strip_prefixes`
- remove empty and `.` path segments
- reject any `..` path segment
- return a forward-slash relative path

Workspace command tools must strip these accepted prefixes before resolving into
the `files` namespace:

```text
workspace
workspace_files
files
```

Examples:

| Input | Normalized file path |
|-------|----------------------|
| `notes/today.md` | `notes/today.md` |
| `/workspace/notes/today.md` | `notes/today.md` |
| `/files/notes/today.md` | `notes/today.md` |
| `workspace_files/notes/today.md` | `notes/today.md` |
| `notes/./today.md` | `notes/today.md` |
| `../outside.txt` | rejected |
| `%2E%2E/outside.txt` | rejected |

Local and S3/R2 storage must reject traversal with the same logical rule.

Required tests:

- `tests/test_workspace.py::test_local_workspace_storage_rejects_path_traversal`
- `tests/test_workspace.py::test_s3_workspace_storage_rejects_path_traversal`
- `tests/test_workspace_files_backend.py::test_workspace_files_rejects_path_traversal`

---

## Workspace Command Tool Contract

Workspace command tools operate only in the `files` namespace. They are
built-in local tools backed by the active workspace storage driver. They are
not host shell tools and must not execute shell commands.

Tool names:

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

The tool names above are reserved while workspace files are enabled. If an app
local tool registry already contains one of these names, runtime preparation
must fail with a clear conflict error instead of overwriting either tool.

### Content Coercion

`write_file` accepts content as any Python value at the backend layer.
The backend stores:

| Input type | Stored text |
|------------|-------------|
| `str` | unchanged |
| `list` | each item stringified and joined with `\n` |
| `dict` | JSON formatted with indent `2` |
| other | `str(value)` |

### `ls`

Input:

```json
{"path": "notes"}
```

Behavior:

- if `path` is omitted, empty, `.`, `/workspace`, or `/files`, list the files
  namespace root
- if `path` points to a directory with children, return only immediate children
- if `path` points to an empty directory, return directory header plus `(empty)`
- if `path` points to a file, return a message telling the agent to use
  `read_file`
- if `path` does not exist, return a message containing the missing path and
  workspace files root
- unsafe paths return the path safety error string

### `read_file`

Input:

```json
{"path": "notes/today.md"}
```

Behavior:

- if `path` points to a file, return file contents
- if `path` points to a directory, return a message telling the agent to use
  `ls`
- if `path` does not exist, return `File not found: <path>`
- unsafe paths return the path safety error string

`read_file` is the canonical workspace equivalent of `cat`. Do not add a
separate `cat` tool unless the architecture record is updated first.

### `write_file`

Input:

```json
{"path": "notes/today.md", "content": "text", "mode": "create"}
```

Valid modes:

```text
create
append
overwrite
```

Behavior:

| Mode | Existing file | Missing file |
|------|---------------|--------------|
| `create` | return already-exists message with preview and do not overwrite | create file |
| `append` | append with one newline separator | return cannot-append message |
| `overwrite` | replace entire file | return cannot-overwrite message |

Invalid mode returns:

```text
Invalid mode '<mode>'. Allowed modes: create, append, overwrite.
```

### `edit_file`

Behavior:

- missing file returns `File not found: <path>`
- missing search string returns a not-found message
- existing search string replaces all occurrences

### `insert_file`

Behavior:

- missing file returns `File not found: <path>`
- line numbers are 1-based
- line numbers below the file start insert at the beginning
- line numbers beyond the file end append at the end
- result keeps a trailing newline when content exists

### `delete_file`

Behavior:

- missing path returns `Path not found: <path>`
- existing file or directory is deleted
- successful tool response starts with `Deleted:`

### `move_file`

Behavior:

- missing source path returns `Path not found: <old_path>`
- successful rename returns old and new locations

### `clear_files`

Behavior:

- clears only the workspace `files` namespace
- successful response names the cleared root

### `glob`

Input:

```json
{"pattern": "**/*.md", "path": "."}
```

Behavior:

- searches workspace file paths, not artifact paths
- `pattern` uses Python `fnmatch`/glob-style matching
- `path` scopes the search to a subdirectory and defaults to the workspace
  files root
- returns matching relative paths sorted alphabetically
- returns a clear no-matches message when nothing matches
- unsafe `path` values return the path safety error string
- pattern path traversal is rejected

### `grep`

Input:

```json
{"pattern": "TODO", "path": ".", "include": "*.md", "case_sensitive": false}
```

Behavior:

- searches text files in the workspace files namespace
- `path` can point to one file or a directory
- when `path` is a directory, search files recursively under it
- `include` optionally filters relative paths with a glob pattern
- default search is case-insensitive
- return matches as `path:line_number:line`
- cap output to a bounded number of matches and say when more matches were
  omitted
- skip unreadable/binary files with a short skipped count where practical
- unsafe paths return the path safety error string

Required tests:

- `tests/test_workspace_files_backend.py::test_workspace_files_create_append_overwrite_and_view`
- `tests/test_workspace_files_backend.py::test_workspace_files_serializes_structured_content`
- `tests/test_workspace_files_backend.py::test_workspace_files_views_empty_directory`
- `tests/test_workspace_files_backend.py::test_workspace_files_replace_insert_delete_rename_and_clear`
- `tests/test_workspace_files_backend.py::test_workspace_files_uses_s3_compatible_workspace_storage`
- tests must cover `ls`, `read_file`, `glob`, and `grep` on local storage
- tests must cover at least one `glob` and one `grep` flow on S3-compatible storage
- tests must cover reserved-name conflict behavior during runtime tool
  registration

---

## Artifact Tool Contract

Artifact tools operate only on runtime-managed offloaded tool responses in the
`artifacts` namespace.

Tool names:

```text
read_artifact
tail_artifact
search_artifact
list_artifacts
```

Artifact tools must not write to the `files` namespace.

Behavior:

| Tool | Behavior |
|------|----------|
| `read_artifact` | return full artifact content, or `Error: Artifact '<id>' not found. Check the artifact ID and try again.` |
| `tail_artifact` | return last `lines` lines, default `50`, or `Error: Artifact '<id>' not found.` |
| `search_artifact` | return matching lines/context, or `Error: Artifact '<id>' not found.` |
| `list_artifacts` | return current-session artifact ids, source tools, and tokens saved; if empty, return `No artifacts have been offloaded in this session.` |

Required tests:

- `tests/test_tool_response_offloader.py`
- `tests/test_tool_runtime_registry.py`

---

## Tool Offload Contract

`ToolResponseOffloader` writes large tool outputs to `workspace.artifacts`.

Default `OffloadConfig`:

| Field | Default |
|-------|---------|
| `enabled` | `True` |
| `threshold_tokens` | `500` |
| `threshold_bytes` | `2000` |
| `max_preview_tokens` | `150` |
| `max_preview_lines` | `10` |
| `retention_days` | `7` |
| `include_metadata` | `True` |

`should_offload(response)` returns `True` when:

- offloading is enabled, and
- response byte length exceeds `threshold_bytes`, or
- response token count exceeds `threshold_tokens`

It returns `False` when disabled or under both thresholds.

`offload(tool_name, response, metadata=None)` must:

- create the artifacts namespace root when needed
- create an artifact id from sanitized tool name, timestamp, and content hash
- detect file extension from content where supported
- write the full response to `workspace.artifacts`
- write metadata JSON when enabled
- store artifact in the current offloader session index
- update offload count and tokens saved
- return an `OffloadedResponse`

`OffloadedResponse.context_message` must include:

- `[TOOL RESPONSE OFFLOADED]`
- tool name
- artifact id
- original token count
- preview token count
- tokens saved
- preview content
- artifact path
- instruction to call `read_artifact('<artifact_id>')`

Required tests:

- `tests/test_tool_response_offloader.py::TestOffloadConfig`
- `tests/test_tool_response_offloader.py::TestShouldOffload`
- `tests/test_tool_response_offloader.py::TestPreviewGeneration`
- `tests/test_tool_response_offloader.py::TestOffload`

---

## Inline Output Policy Contract

Workspace retrieval and mutation tools must stay inline and must not be
recursively offloaded.

Inline built-in tool providers:

```text
workspace
artifact
```

The single source of truth is:

```text
src/omnicoreagent/core/workspace/offload_policy.py
```

No private duplicate frozen set is allowed in tool observation code.
The policy must use the resolved built-in provider. It must not keep output
inline only because an app-local tool has a name such as `read_file` or `grep`.

Required tests:

- `tests/test_tool_observation.py`

---

## Runtime Registration Contract

Workspace runtime tools are registered into the same `ToolRegistry` path as
local tools.

Requirements:

- workspace command tools register when workspace files are enabled
- artifact tools register when tool offload is enabled
- internal/runtime tools are normal tools from the model point of view
- workspace command tools and the tool offloader bind to the same `Workspace`
  object
- `ToolResponseOffloader.storage` must be `runtime.workspace.artifacts` after
  runtime preparation when both are enabled
- if app local tools conflict with built-in workspace command tool names,
  runtime preparation must raise a clear `ValueError`

Required tests:

- `tests/test_tool_runtime_registry.py::test_prepare_tools_uses_workspace_config_for_workspace_files`
- `tests/test_tool_runtime_registry.py::test_prepare_tools_returns_none_without_any_tool_sources`
- `tests/test_tool_runtime_registry.py::test_workspace_command_tool_names_are_reserved`

---

## Startup Contract

Workspace modules must preserve lazy import behavior where the runtime depends
on optional cloud dependencies.

Requirements:

- importing core runtime should not require `boto3`
- S3/R2 dependencies are loaded only when S3/R2 workspace storage is created
- new workspace package files under `src/omnicoreagent/core/workspace` must be
  tracked explicitly because `.gitignore` ignores directories named `workspace/`

Required tests:

- `tests/test_import_startup.py`

---

## Error Contract

Tool-facing errors are returned as strings so the agent loop can reason over
them.

Required error behavior:

| Case | Required behavior |
|------|-------------------|
| Unsafe path | return or raise message containing `outside workspace namespace` |
| Missing workspace file list | return `Path not found: <path>` plus root context |
| Missing workspace file read | return `File not found: <path>` |
| Existing create target | return `File already exists` and do not overwrite |
| Missing append target | return `Cannot append: File not found` |
| Missing overwrite target | return `Cannot overwrite: File not found` |
| Invalid write mode | return allowed modes |
| Missing edit target | return `File not found: <path>` |
| Missing rename source | return `Path not found: <old_path>` |
| No glob matches | return `No files matched pattern` |
| No grep matches | return `No matches found` |
| Missing artifact read | return artifact not found error |
| Missing S3 bucket | raise `ValueError("S3 workspace backend requires AWS_S3_BUCKET")` |
| Missing R2 config | raise `ValueError` listing missing R2 environment variables |
| Invalid storage driver | raise `ValueError("workspace_backend must be one of: local, s3, r2")` |

Storage-facing errors may raise exceptions. Tool-facing wrappers must convert
expected operational errors into clear tool strings.

---

## Verification Commands

Focused workspace verification:

```bash
uv run pytest -q tests/test_workspace.py tests/test_workspace_files_backend.py tests/test_tool_response_offloader.py tests/test_tool_runtime_registry.py tests/test_tool_observation.py tests/test_prompt_context.py tests/test_subagents.py tests/test_import_startup.py
```

Repository checks:

```bash
uv lock --check
uv run ruff check src tests
uv run pytest -v tests -m "not requires_api_key and not requires_network and not broken_upstream"
uv run pre-commit run --files engineering/architecture/workspace.md engineering/specifications/workspace.md
```

Git tracking check for new workspace source files:

```bash
git ls-files src/omnicoreagent/core/workspace
```

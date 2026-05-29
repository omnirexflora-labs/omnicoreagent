# Governed Execution Specification

This specification defines the target behavior contract for OmniCoreAgent
governed execution: policy, approvals, sandbox runtime integration, and
authority-aware enforcement.

Read this with:

- `engineering/architecture/governed-execution.md`
- `engineering/architecture/telemetry.md`
- `engineering/architecture/workspace.md`
- `engineering/architecture/background-agents.md`
- `src/omnicoreagent/core/tools`
- `src/omnicoreagent/mcp_clients_connection`
- `src/omnicoreagent/core/workspace`
- `src/omnicoreagent/background`
- `src/omnicoreagent/serve`

This is a design specification. It does not mean the implementation already
exists. Code and tests must move toward this contract phase by phase.

---

## Scope

This specification covers:

- policy envelope structure
- capability descriptor structure
- authority request structure
- policy decision contract
- approval contract
- enforcement point contract
- sandbox runtime interface
- filesystem, network, secret, memory, workspace, MCP, subagent, background,
  and telemetry policy expectations
- default behavior
- failure behavior
- test requirements
- implementation phases

This specification does not cover:

- full sandbox provider implementation
- observability dashboards
- evaluation runners
- public policy UI
- enterprise identity administration
- full information-flow-control implementation
- vendor-specific sandbox SDK details

---

## Terminology

| Term | Meaning |
|------|---------|
| Policy envelope | Immutable authority contract for an agent/session/task/subagent. |
| Capability | A named action class such as `tool.mcp.call` or `filesystem.write`. |
| Capability descriptor | Metadata declared by a tool/runtime surface describing what it can do. |
| Authority request | Runtime request to use a capability against a target. |
| Policy decision | Deterministic allow, deny, or ask decision that may attach constraints such as sandbox requirements. |
| Approval request | Human/application approval object created by an ask decision. |
| Sandbox runtime | Execution adapter that enforces containment. |
| Enforcement point | Runtime boundary that must consult policy before side effects. |
| Policy snapshot | Stored immutable policy version used by durable background tasks. |

---

## Policy Envelope Schema

A policy envelope is a structured object. The exact Python implementation may
use Pydantic models, but the semantic shape is:

```yaml
policy_id: string
version: string
name: string
description: string | null
provenance:
  source: default | file | code | remote | inherited
  source_ref: string | null
  created_by: string | null
  loaded_at: datetime
  policy_hash: string
  parent_policy_id: string | null
scope:
  application_id: string | null
  agent_name: string | null
  session_id: string | null
  task_id: string | null
  subagent_id: string | null
  workspace_prefix: string | null
mode: strict | interactive | permissive
rules:
  deny: [PolicyRule]
  ask: [PolicyRule]
  allow: [PolicyRule]
defaults:
  unknown_capability: deny | ask | allow
  # Future default knobs may add per-surface unknown handling.
  # Current implementation uses normal rule/mode evaluation for unknown MCP
  # servers, network hosts, and secrets.
sandbox:
  required_for: [capability-pattern]
  default_profile: string | null
  providers: object
network:
  default: deny | ask | allow
  allowed_hosts: [NetworkRule]
  denied_hosts: [NetworkRule]
filesystem:
  default: deny | ask | allow
  read: [PathRule]
  write: [PathRule]
  delete: [PathRule]
workspace:
  files:
    read: [PathRule]
    write: [PathRule]
    delete: [PathRule]
  artifacts:
    read: [PathRule]
    write: [PathRule]
memory:
  read: [MemoryRule]
  write: [MemoryRule]
mcp:
  servers: [McpServerRule]
secrets:
  allowed: [SecretRule]
subagents:
  enabled: bool
  max_depth: int
  max_children: int
  allowed_roles: [string]
background:
  create: allow | ask | deny
  schedule: allow | ask | deny
  cancel: allow | ask | deny
budgets:
  max_tool_calls: int | null
  max_subagents: int | null
  max_sandbox_seconds: int | null
  max_network_requests: int | null
  max_workspace_bytes: int | null
lifecycle:
  expires_at: datetime | null
  cleanup: always | on_success | never
telemetry:
  record_decisions: bool
  redact_inputs: bool
  redact_outputs: bool
  data_classes: [string]
```

Rules:

- Policy envelopes are immutable during a run.
- A model cannot edit or grant policy.
- A child policy can only narrow the parent policy.
- Durable background tasks store a policy snapshot id.
- If policy cannot be loaded, the runtime fails closed.
- Every policy envelope must record provenance.
- Policy hash is included in telemetry decisions and audit records.
- Child policies reference the parent policy they narrowed from.
- Policy source is never trusted if loaded from model-generated content unless
  explicitly approved by application code.

---

## Policy Versioning and Migration

Policy snapshots are durable runtime contracts. Background tasks, subagents, and
resumed sessions may outlive the current policy schema.

Rules:

- Every policy envelope includes `version`.
- Policy snapshots must include schema version and policy hash.
- A runtime may load older policy versions only through an explicit migration
  path.
- If a stored policy snapshot cannot be migrated safely, execution fails
  closed.
- Policy migrations must never broaden authority silently.
- Migration may only preserve or narrow authority unless an application-owned
  migration explicitly allows expansion.

---

## Policy Hash Canonicalization

Policy hashes must be stable across equivalent YAML and JSON representations.

Rules:

- Hashes are computed from a canonical JSON representation.
- Object keys are sorted.
- Null and default fields are normalized consistently.
- Hash input is the normalized semantic policy after default/profile expansion.
- Runtime-generated fields such as `loaded_at` are excluded from the hash unless
  explicitly required by a future policy version.
- The `policy_hash` field itself is excluded from the canonical hash input.
- Generated identifiers such as autogenerated `policy_id` values are excluded
  unless supplied by application-owned policy input.
- Comments and formatting do not affect the hash.
- The hash algorithm is `sha256` unless changed by a future versioned policy
  format.
- Tests must prove JSON/YAML equivalence, key-order equivalence, default/null
  equivalence, and generated-field exclusion.

---

## Policy Loading

Default policy files may live at:

- `omnicoreagent.policy.yaml`
- `.omnicoreagent/policy.yaml`
- application-provided in-code config

Resolution order:

1. explicit runtime config
2. application-provided policy object
3. project policy file
4. default policy builder

Rules:

- Policy file discovery must be deterministic.
- Model-generated policy content is never loaded by discovery.
- Policy discovery happens only under a trusted application/project root
  resolved at startup.
- Policy discovery must reject files under agent-writable workspace, artifact,
  output, or temporary directories.
- Policy file paths are resolved to real paths before loading.
- Symlink escapes from the trusted root are rejected.
- Auto-discovered project policy may only narrow the trusted baseline unless it
  is explicitly selected, signed, or approved by application-owned config.
- YAML support requires an explicit parser dependency or an implementation phase
  that limits file loading to JSON/in-code policy until YAML support is added.
- A missing policy file is not an error when the default policy builder is
  available.
- Invalid policy files raise `PolicyLoadError` and fail closed in strict mode.

---

## Policy Composition

Multiple policy layers may apply at once:

- system default policy
- application policy
- agent policy
- session policy
- task policy
- subagent policy
- approval-derived temporary scope

Rules:

- Effective policy is computed by narrowing, not merging loosely.
- Deny always survives composition.
- Child, session, task, and subagent policies cannot remove parent denies.
- Temporary approval scopes expire and cannot exceed the effective parent
  policy.
- If composition is ambiguous, fail closed.

---

## Policy Rule Schema

Capability names use dot-separated lowercase namespaces:

```text
domain[.resource].action
```

Examples:

- `tool.local.call`
- `tool.mcp.call`
- `workspace.files.write`
- `network.http.get`
- `process.exec`

Rules:

- Capability names are stable API contracts.
- Renaming a capability requires a migration path.
- Wildcards may match namespaces, for example `network.http.*`.
- Capability names should describe authority, not implementation class names.
- Two-segment capabilities such as `process.exec` are valid when there is no
  useful intermediate resource namespace.

```yaml
rule_id: string
effect: allow | ask | deny
capability: string
actor: ActorMatcher | null
target: TargetMatcher | null
conditions:
  risk_level: [low | medium | high | critical] | null
  data_classes: [string] | null
  provider: local | mcp | workspace | artifact | memory | skill | sandbox | background | subagent | serve | telemetry | network | package | filesystem | secret | null
  execution_surface: tool | workspace | artifact | memory | mcp | skill | sandbox | background | subagent | serve | telemetry | network | filesystem | secret | secret_broker | null
  mcp_server: string | null
  method: string | null
  host: string | null
constraints:
  timeout_seconds: int | null
  output_bytes: int | null
  require_sandbox: bool
  sandbox_profile: string | null
  require_approval_reason: bool
  redact: bool
```

Rules:

- `deny` rules are evaluated before `ask`, then `allow`.
- More specific rules win only inside the same effect group.
- A bare capability rule applies to all targets of that capability.
- Rules must support wildcard matching for capability names and path scopes.
- Policy evaluation must be deterministic and testable without an LLM.
- `provider` matches the tool/capability provider.
- `execution_surface` matches the runtime surface. Non-tool surfaces such as
  memory, background, subagent, serve, and telemetry are matched here instead of
  being treated as tool providers.

---

## Capability Descriptor Contract

Every side-effecting surface must expose capability metadata.

```yaml
capability_id: string
provider: local | mcp | workspace | artifact | memory | skill | sandbox | background | subagent | serve | telemetry | network | package | filesystem | secret
name: string
description: string
descriptor_source: builtin | app_code | mcp_schema | generated | user_config
descriptor_trust: trusted | untrusted | inferred
risk_level: low | medium | high | critical
side_effects:
  reads: [ResourceRef]
  writes: [ResourceRef]
  deletes: [ResourceRef]
  network: [NetworkRef]
  process: bool
  secrets: [SecretRef]
data_classes: [string]
requires_sandbox: bool
requires_approval: bool
default_timeout_seconds: int | null
```

Examples:

- a read-only weather API tool declares `network.http.get`
- a refund tool declares `tool.local.call`, `network.http.post`,
  `payment`, and `requires_approval`
- a filesystem MCP tool declares `tool.mcp.call`, `filesystem.read`,
  `filesystem.write`
- a sandbox command declares `process.exec`, `filesystem.read`,
  `filesystem.write`, and `network.*` if network is requested
- an observation filter declares `observation.filter` before untrusted output is
  injected into the model context

Rules:

- Missing descriptors are treated as unknown capability.
- Unknown capability follows `defaults.unknown_capability`.
- MCP tool descriptors may be generated from schemas and overridden by policy.
- Application code can provide explicit descriptors for local tools.
- Built-in and app-code descriptors may be trusted by default.
- MCP schema and generated descriptors are advisory unless overridden by policy.
- User-supplied or model-supplied descriptors must not grant authority directly.

---

## Authority Request Contract

Before an action executes, the runtime creates an authority request.

```yaml
request_id: string
timestamp: datetime
actor:
  type: user | agent | subagent | background | serve | system
  id: string | null
  name: string | null
capability: string
provider: string
target:
  type: tool | mcp_tool | workspace_path | artifact_path | memory_scope | network_host | secret | sandbox | subagent | background_task
  id: string
  name: string | null
operation: string
input_preview: object | null
input_hash: string | null
data_classes: [string]
risk_level: low | medium | high | critical
session_id: string | null
run_id: string | null
trace_id: string | null
task_id: string | null
workspace_prefix: string | null
reason: string | null
```

Rules:

- Full sensitive input does not have to be stored in the request.
- The input hash lets telemetry correlate without leaking data.
- The request must be independent of model text after parsing.
- Parallel tool batches produce one request per tool call plus one batch-level
  request when needed.

---

## Policy Decision Contract

```yaml
decision_id: string
request_id: string
timestamp: datetime
effect: allow | deny | ask
reason_code: matched_allow | matched_deny | matched_ask | unknown_capability | unknown_target | policy_error | approval_required | sandbox_required | budget_exceeded | expired_policy
reason: string
matched_rules: [string]
constraints:
  sandbox_required: bool
  sandbox_profile: string | null
  timeout_seconds: int | null
  output_bytes: int | null
  network_policy: object | null
  filesystem_policy: object | null
  redaction_required: bool
  approval_required: bool
  narrowed_scope: object | null
telemetry:
  redact_input: bool
  redact_output: bool
```

Rules:

- `deny` stops execution.
- `ask` creates an approval request. If the application did not provide an
  approval resolver for that run, execution fails closed with
  `ApprovalRequiredError`.
- `allow` may still require sandbox enforcement.
- A decision must include enough reason text to debug policy behavior.
- `reason_code` is required so tests, telemetry, dashboards, and future evals do
  not depend on parsing free-text reason strings.
- Decisions are recorded in telemetry when configured.

---

## Approval Contract

Approval requests are first-class runtime objects.

```yaml
approval_id: string
request_id: string
decision_id: string
created_at: datetime
expires_at: datetime | null
actor: object
capability: string
target: object
risk_level: low | medium | high | critical
reason: string
proposed_scope: object | null
evidence:
  trace_id: string | null
  span_id: string | null
  event_id: string | null
status: pending | approved | denied | expired
resolved_by: string | null
resolved_at: datetime | null
resolution_scope: object | null
resolution_reason: string | null
```

Rules:

- Approvals can narrow scope.
- Approvals cannot broaden beyond the policy envelope.
- Expired approvals deny by default.
- Approval state is recorded in telemetry.
- Background task approval behavior is application-owned. The core harness does
  not provide a babysitting loop or approval UI by default.
- The approver principal must come from trusted application-owned identity
  context.
- The approver principal must be authorized for the policy scope, tenant,
  session, data class, and requested capability.
- Approval records must include tenant/session/request binding where available.
- Static approval resolvers are forbidden for production high-risk approvals
  unless application-owned config explicitly enables them.

---

## Enforcement Point Contract

Every enforcement point follows the same sequence:

```text
build capability descriptor
  -> build authority request
  -> evaluate policy
  -> handle deny/ask/allow
  -> enforce constraints
  -> execute
  -> record result/violation
```

Required enforcement points:

| Surface | Required Before Execution |
|---------|---------------------------|
| Local tool | `tool.local.call` |
| MCP tool | `tool.mcp.call` and declared tool capabilities |
| Workspace files | `workspace.files.read/write/delete` |
| Artifacts | `workspace.artifacts.read/write` |
| Memory | `memory.read/write` |
| Subagent spawn | `subagent.spawn` plus derived child policy |
| Background task/run | `background.task.create/update/delete/pause/resume`, `background.run.start/cancel` |
| Skills | `skill.load` and `skill.execute` |
| Sandbox command | `process.exec` plus filesystem/network/secrets |
| Network | `network.*` |
| Secrets | `secret.read` or brokered secret use |
| Telemetry export | `telemetry.export` |
| Observation pipeline | `observation.filter` before tool/sandbox/MCP output reaches the model |

No side-effecting path may bypass this once the governed execution phase owns
that surface.

Sandbox stdout/stderr, MCP responses, web output, and workspace file content are
untrusted until filtered before model injection.

Tool provider resolution:

- Built-in workspace tools emit `workspace.files.*` capabilities, not only
  generic `tool.local.call`.
- Built-in artifact tools emit `workspace.artifacts.*` capabilities, not only
  generic `tool.local.call`.
- Application local tools emit `tool.local.call` plus any declared capabilities
  from their descriptor.
- MCP tools currently emit `tool.mcp.call`. A later descriptor phase will add
  declared or policy-overridden secondary capabilities such as network,
  filesystem, memory, and secret use.
- Composite actions produce one deterministic governance decision path, either
  by evaluating all required capabilities together or by producing a single
  approval request that lists every required capability.

The existing tool registry may store local, workspace, artifact, and MCP-backed
tools in one runtime view. Governance must evaluate the resolved provider and
capabilities after tool resolution, not assume every registry entry is a generic
local tool.

---

## Sandbox Runtime Interface

The provider-neutral sandbox interface must support:

```python
class SandboxRuntime:
    async def create(self, manifest: SandboxManifest) -> SandboxSession: ...
    async def execute(self, session_id: str, request: SandboxExecRequest) -> SandboxExecResult: ...
    async def read_file(self, session_id: str, path: str) -> bytes: ...
    async def write_file(self, session_id: str, path: str, content: bytes) -> None: ...
    async def set_network_policy(self, session_id: str, policy: NetworkPolicy) -> None: ...
    async def snapshot(self, session_id: str) -> SandboxSnapshot | None: ...
    async def terminate(self, session_id: str) -> None: ...
```

`SandboxManifest`:

```yaml
sandbox_id: string | null
provider: none | local_test
image: string | null
working_dir: string
workspace_mount:
  source: string
  target: string
  mode: read_only | read_write
filesystem_policy:
  default: deny | allow
  readable_paths: [string]
  writable_paths: [string]
  denied_paths: [string]
network_policy:
  default: deny | allow
  allowed_hosts: [string]
  denied_hosts: [string]
environment:
  plain: object
  secret_refs: [string]
resources:
  cpu: string | null
  memory: string | null
  timeout_seconds: int | null
  gpu: bool
lifecycle:
  cleanup: always | on_success | never
  snapshot: bool
```

Rules:

- Sandbox runtime is selected by policy/config, not by the model.
- Sandbox adapters must report unsupported constraints before execution.
- If policy requires a sandbox and no compatible runtime exists, deny.
- `local_test` is a development/test adapter only.
- `environment`, `resources`, `lifecycle`, and `network_policy` are part of the
  provider-neutral contract. The `local_test` adapter carries them for handler
  context and tests but does not enforce CPU, memory, timeout, network, or
  process isolation.
- `working_dir`, `workspace_mount.target`, and sandbox filesystem policy paths
  must be absolute sandbox paths. Encoded traversal such as `%2e%2e` is decoded
  and rejected before provider adapters see the manifest.
- `workspace_mount.source` must be an absolute source path or a URI such as
  `s3://bucket/prefix`. Relative escapes and embedded credentials are rejected.
- Sandbox host patterns are host-only values with optional leading wildcard
  namespace such as `*.example.com`. Ports, schemes, paths, userinfo, and query
  strings are rejected instead of silently widened.
- Sandbox environment keys must be valid environment variable names. Secret
  refs are symbolic references only and must not contain whitespace, null bytes,
  or traversal segments.
- Sandbox execution requests must include authority metadata from the policy
  decision that allowed the sandbox route.
- Sandbox telemetry records command summaries and bounded output summaries, not
  raw command arguments or raw stdout/stderr.
- Sandbox stdout/stderr is untrusted tool output.
- Sandbox outputs go through observation, guardrails, offload, and telemetry.

---

## Filesystem and Workspace Policy

Rules:

- Workspace files are the default writable file surface.
- Host filesystem access is denied unless explicitly allowed.
- Host filesystem authority requests use `filesystem.read`,
  `filesystem.write`, and `filesystem.delete`.
- Filesystem targets are normalized before policy matching.
- Absolute host paths are never exposed to the model unless policy allows.
- `delete` requires explicit allow or approval.
- Subagents get a narrowed workspace prefix by default.
- Sandbox mount rules are derived from workspace and filesystem policy.
- Filesystem and workspace decisions evaluate canonicalized paths inside a
  stable root.
- Relative segments such as `../` are rejected during authority-request
  construction. Deeper symlink, hardlink, mount, and stable-root escape checks
  must run at the concrete filesystem/sandbox execution boundary.
- Object-storage keys are normalized before policy matching.
- The final resolved path/key is enforced again at execution time to reduce
  time-of-check/time-of-use races.
- Workspace files and artifacts must retain their separate semantics:
  agent-managed files vs runtime-managed offloaded artifacts.

Required tests:

- read allowed workspace file
- write allowed workspace file
- deny write outside workspace
- deny delete without delete policy
- subagent cannot write outside scoped prefix
- sandbox manifest uses narrowed workspace mount
- reject `../` traversal and absolute path escape
- reject symlink/hardlink escapes for local filesystems
- reject normalized object-key escape for S3/R2-compatible backends

---

## Network Policy

Rules:

- Process/sandbox network defaults to deny.
- Tool-level network access requires explicit capability or policy.
- HTTP authority requests use `network.http.{method}` with lowercase method and
  normalized host.
- HTTP method and host must be policy-addressable.
- Authority hosts are host-only. Embedded credentials and explicit ports are
  rejected in this phase rather than broadened or silently normalized.
- Package install requires `package.install`, not only `network.http.get`.
- Package names in authority requests are package identities, not installer
  command text. Flags, shell separators, whitespace injection, and control
  characters are rejected before authorization.
- Unknown hosts follow normal policy mode behavior until a first-class
  `unknown_network_host` default model is implemented.
- Credentialed network calls should use brokered secret access.

Required tests:

- deny unknown host
- allow GET to configured host
- deny POST when only GET is allowed
- deny package install without `package.install`
- approval required for unknown host in interactive mode

---

## Secret Policy

Rules:

- Secrets are never provided to the model by default.
- Secrets are never passed into sandbox environment by default.
- Policy may allow brokered secret use without raw secret read.
- Brokered secret use is `secret.use` and references a secret name plus purpose;
  it does not expose the credential value to the model.
- Raw secret value access is `secret.read` and remains credential-class data.
- Telemetry records secret reference names only.
- Secret values must be redacted before telemetry, memory, workspace, and model
  observations.

Required tests:

- deny unknown secret
- allow brokered secret use
- deny raw secret read when only brokered use is allowed
- telemetry contains secret ref but not value
- sandbox env does not contain secrets unless explicitly allowed

---

## MCP Policy

Rules:

- MCP servers default to untrusted external capability providers.
- MCP server startup or connection requires `mcp.server.start` or
  `mcp.server.connect`.
- Unsupported MCP transport types fail closed before authorization and before
  opening any local process or remote connection.
- Tool calls require `tool.mcp.call`.
- Server-level allow does not imply all tool-level capabilities.
- Tool descriptors can be generated but policy overrides are authoritative.
- MCP tool outputs are untrusted observations.
- MCP servers cannot gain workspace, memory, network, or secret authority unless
  declared and allowed.
- Local MCP servers must run with a scrubbed operational environment, explicit
  mounts, sandbox/egress policy when required, and no ambient credentials by
  default.
- If the configured MCP server name differs from the server identity returned by
  MCP initialization, the runtime must authorize the reported identity before
  registering tools from that server.
- Remote MCP servers require an explicit remote identity boundary such as a
  gateway/proxy, pinned server identity or URL, and schema/tool hash.
- MCP schema or tool drift is denied until reapproved or explicitly allowed by
  policy.

Required tests:

- deny unknown MCP server
- allow specific MCP tool
- deny another tool on same server
- deny MCP tool network capability not granted
- policy wraps MCP calls without modifying server code
- deny local MCP startup with ambient secrets
- deny reported MCP server identity drift unless policy allows it
- deny remote MCP schema/tool drift until approved

---

## Subagent Policy

Rules:

- Child policy is derived from parent policy by narrowing.
- Subagent spawn requires `subagent.spawn`.
- Subagent spawn request includes role, task, tools, workspace scope, memory
  scope, budget, and deadline.
- Recursive spawn is denied unless policy allows depth > 1.
- Subagents cannot receive broader secret/network/filesystem access than parent.

Required tests:

- child policy narrows parent
- child cannot broaden workspace scope
- recursive spawn denied by default
- child denied parent-prohibited tool
- subagent output includes policy/telemetry references

---

## Background Task Policy

Rules:

- Task creation, update, deletion, pause, resume, scheduling, run start, and run
  cancellation require policy decisions.
- Durable task and run records store a policy snapshot id, schema version, and hash.
- Restarted tasks load the same snapshot or fail closed before the agent runs.
- Missing snapshots fail closed for governed background execution.
- Stored snapshots carry budget counters and restore the active policy budget
  floor before background execution decisions.
- Task retry does not reset policy budgets unless configured.
- Long-running tasks may need approval renewal.

Required tests:

- create task with policy snapshot
- restart loads snapshot
- missing snapshot fails closed
- retry preserves policy
- cancel run requires policy

---

## Memory and Data Classification

Rules:

- Memory read/write is policy-addressable.
- Memory context injection must preserve trust/data classification metadata.
- User-private, credential, payment, health, source-code, and system-prompt
  classes must be representable even if early enforcement is basic.
- Future information-flow controls must be able to consume the metadata.
- Coarse source-to-sink enforcement is required before governed execution is
  treated as production-ready.
- `credential` and `system_prompt` data never leave broker/redaction boundaries.
- `user_private`, `health`, `payment`, and `source_code` require explicit sink
  policy before disclosure to network, MCP, model-bound observations, workspace
  artifacts, or telemetry export.

Required tests:

- memory write emits data class metadata
- private memory cannot be disclosed through a denied network/tool action
- telemetry redacts configured data classes
- credential and system prompt classes are blocked from model/tool/network sinks

---

## Budget Enforcement

Budgets are authority constraints, not metrics only.

Rules:

- Budget checks happen before execution.
- Budget counters are updated after allowed execution attempts.
- Failed attempts may count against budget unless policy says otherwise.
- Retries do not reset budget by default.
- Subagents inherit narrowed budget from parent.
- Background tasks resume with stored budget state.
- Budget exceeded returns a deny decision with `reason_code:
  budget_exceeded`.

Required tests:

- deny when tool-call budget is exhausted
- retry does not reset budget
- child subagent cannot exceed parent budget
- background task resumes with stored budget counters
- policy can explicitly decide whether failed attempts count

---

## Telemetry Requirements

Governed execution emits these event types:

- `policy_request_created`
- `policy_decision_allow`
- `policy_decision_ask`
- `policy_decision_deny`
- `approval_request_created`
- `approval_resolved`
- `sandbox_session_created`
- `sandbox_exec_started`
- `sandbox_exec_completed`
- `sandbox_exec_failed`
- `policy_violation`
- `secret_access_denied`
- `secret_access_brokered`
- `network_access_denied`
- `network_access_allowed`
- `filesystem_access_denied`
- `filesystem_access_allowed`

Rules:

- Events include `trace_id` and `run_id` when available.
- Events include policy id, policy version, policy hash, reason code, and
  matched rule ids.
- Events do not include secret values.
- Denied actions still produce telemetry.
- Approval events link to authority request and decision ids.
- Governance event names follow the existing telemetry registry convention:
  stable snake_case names added to the telemetry event registry when
  implemented.

---

## Audit Log

Governed execution distinguishes telemetry from audit records.

Telemetry is operational observability. Audit records are durable authority
evidence.

Audit records include:

- authority request id
- policy id, version, and hash
- actor
- capability
- target
- decision
- reason code
- matched rules
- approval id when present
- execution result summary
- timestamp

Rules:

- Every high-risk authority decision and result produces an audit record:
  allow, deny, ask, approval resolution, and execution summary.
- Audit records must not contain raw secrets.
- Audit write failure for high-risk actions fails closed unless policy
  explicitly allows best-effort audit.
- Audit records are append-only once written.
- Telemetry may reference audit ids, but telemetry is not the audit log.
- Directly policy-allowed high-risk actions such as process execution, secret
  broker use, destructive filesystem operations, and credentialed network egress
  still require durable audit.

---

## Default Behavior

Default OmniCoreAgent behavior:

- no policy config required for a basic agent
- local tools allowed only by declared/default safe tool policy
- workspace files scoped to active workspace
- memory defaults to current runtime behavior
- MCP tools require explicit configuration and receive external-tool trust
  metadata
- process execution is denied unless enabled
- raw network from sandbox/process is denied unless enabled
- secret access is denied unless enabled
- subagent spawn follows current config but receives derived policy once the
  surface is governed
- background task creation follows current config but receives policy snapshot
  once the surface is governed

The first-run user experience must stay simple. Production users get stronger
controls by adding policy config.

Default policy profiles:

- `permissive-dev`
- `interactive-dev`
- `strict-production`

The default first-run profile should preserve simple local development while
production deployments can opt into strict behavior.

Profile intent:

- `permissive-dev` keeps local development moving while still denying raw
  secrets and unrestricted process/network execution by default.
- `interactive-dev` asks before risky or unknown capabilities and is the best
  profile for humans building and debugging agents locally.
- `strict-production` denies unknown capabilities and requires explicit policy
  for side effects, secrets, process execution, network egress, MCP tools,
  subagents, and background work.

Minimal policy input example before runtime normalization:

```yaml
version: "1"
name: "support-agent-policy"
mode: interactive
rules:
  deny:
    - rule_id: deny_raw_secret_read
      effect: deny
      capability: secret.read
  ask:
    - rule_id: ask_external_post
      effect: ask
      capability: network.http.post
      conditions:
        host: "*.external.example"
  allow:
    - rule_id: allow_workspace_files
      effect: allow
      capability: workspace.files.*
    - rule_id: allow_local_tools
      effect: allow
      capability: tool.local.call
network:
  default: deny
workspace:
  files:
    read:
      - path: "support/**"
    write:
      - path: "support/**"
```

The runtime fills provenance, policy hash, defaults, and other normalized fields
when loading this input into a `PolicyEnvelope`.

---

## Failure Behavior

Rules:

- Missing policy in strict mode: deny.
- Unknown capability in strict mode: deny.
- Policy engine error: deny and emit telemetry.
- Approval timeout: deny.
- Sandbox runtime unavailable when required: deny.
- Sandbox constraint unsupported: deny.
- Telemetry write failure follows the telemetry recorder's strict or best-effort
  mode. Governance may require strict telemetry for selected high-risk actions,
  but best-effort telemetry remains valid for normal serving paths.
- Audit write failure for high-risk actions fails closed unless policy
  explicitly allows best-effort audit.

Stable errors:

- `PolicyLoadError`
- `PolicyEvaluationError`
- `PolicyDeniedError`
- `ApprovalRequiredError`
- `ApprovalExpiredError`
- `SandboxRequiredError`
- `BudgetExceededError`
- `UnknownCapabilityError`

---

## Implementation Phases

The first implementation PR must not attempt sandbox provider integration.

Minimum first implementation target:

- `PolicyEnvelope` model
- `PolicyRule` model
- `CapabilityDescriptor` model
- `AuthorityRequest` model
- `PolicyDecision` model
- minimal approval resolver protocol
- deterministic evaluator
- `GovernanceEngine` enforcement boundary
- default policy builder
- deny/ask/allow precedence tests
- unknown capability tests
- fail-closed policy error tests
- stable error shape tests
- policy hash canonicalization tests
- policy loading/fail-closed tests
- telemetry event validation tests

If Phase 1 does not implement full approval resolution, an `ask` decision must
raise `ApprovalRequiredError` with no side effect. Phase 2 may not execute
side-effecting `ask` decisions without an approval resolver.

Initial package layout:

```text
src/omnicoreagent/governance/
  __init__.py
  approvals.py
  defaults.py
  errors.py
  evaluator.py
  enforcement.py
  hashing.py
  models.py
  policy.py
  telemetry.py
```

This package owns governed execution. It must not be scattered across tools,
runtime, background, workspace, MCP, and serve modules.

`enforcement.py` exposes the single runtime boundary, such as
`GovernanceEngine`, that builds authority requests, evaluates policy, handles
approval-required decisions, records telemetry/audit decisions, and returns
constraints. Tools, workspace, MCP, background, subagent, sandbox, and serve
code call this boundary instead of each implementing policy semantics.

### Phase 1: Policy Core

- Add policy models.
- Add policy evaluator.
- Add authority request and decision models.
- Add minimal approval resolver protocol and `ApprovalRequiredError` behavior.
- Add policy provenance, policy hash, reason codes, and budget counters.
- Add `GovernanceEngine` enforcement boundary.
- Add default policy builder.
- Unit test deny/ask/allow precedence.

### Phase 2: Tool and Workspace Enforcement

- Add capability descriptors for local tools and workspace tools.
- Enforce policy before local tool calls.
- Enforce policy before workspace file operations.
- Emit telemetry events.

### Phase 3: MCP Enforcement

- Add MCP server/tool capability descriptors.
- Enforce policy before MCP server start/connect and MCP tool calls.
- Add per-server/per-tool rules.
- Add tests with fake MCP servers and tools.
- Do not treat remote schema/tool hash pinning or MCP secondary capability
  enforcement as complete in this phase; those require the later descriptor and
  sandbox/policy phases.

### Phase 4: Approval Interface

- Keep approval resolution app-owned.
- Keep the core resolver protocol small and deterministic.
- `ask` without an app-provided resolver fails closed with `ApprovalRequiredError`.
- Static resolvers remain test/development helpers and must not silently approve
  high-risk production actions.
- Do not add a built-in babysitting loop, approval UI, callback server, or
  OmniServe approval API in the core harness unless a later application-facing
  design explicitly needs it.

### Phase 5: Subagent and Background Policy

- Gate dynamic subagent spawn behind `subagent.spawn`.
- Prevent recursive spawn by deriving subagent runtime config with
  `enable_subagents=False`.
- Derive an inherited child policy for subagents that denies recursive spawn
  and cannot multiply parent budget authority by default.
- Include subagent task, tool, MCP, workspace, memory, budget, and deadline
  metadata in spawn authority requests so later policy composition can narrow
  those scopes deterministically.
- Store policy snapshots on governed background task and run records.
- Enforce restart fail-closed behavior for missing, unavailable, or changed
  policy snapshots.
- Restore stored background budget counters as the active policy budget floor on
  restart.
- Gate background run execution and cancellation behind policy.

### Phase 6: Sandbox Runtime Interface

- Add provider-neutral sandbox protocol.
- Add `none` adapter for policy-only mode. It never satisfies
  `sandbox_required`.
- Add a safe local test adapter that executes registered handlers only. It must
  not expose arbitrary shell execution.
- Public sandbox providers in this phase are only `none` and `local_test`.
  Production providers are future adapters, not completed runtime surfaces.
- Enforce `sandbox_required` decisions by allowing them only through an explicit
  sandbox authorization route. Normal `authorize()` and `authorize_all()` fail
  closed when a decision carries `sandbox_required`, even if a sandbox runtime is
  configured.
- Provide `authorize_sandboxed()` and `authorize_all_sandboxed()` for callers
  that will immediately route execution through a compatible sandbox runtime.
- A compatible runtime must implement the `SandboxRuntime` contract. The local
  test adapter can satisfy `sandbox_required` only when the application
  explicitly enables the development/test flag.
- Phase 6 does not auto-route existing agent tool calls into a sandbox. The
  normal tool path remains fail-closed for `sandbox_required` decisions until a
  sandbox execution route is explicitly wired by the caller or runtime surface.
- Route sandbox execution results through stable telemetry events and structured
  observation-shaped output.
- Treat the local test adapter network policy as advisory test context. Real network
  enforcement belongs to future production sandbox providers.

### Phase 7: Network, Filesystem, And Secret Contracts

- Add capability descriptors and authority-request builders for network,
  package installation, host filesystem, and secrets.
- Keep `package.install` separate from generic network GET.
- Keep `secret.use` separate from raw `secret.read`.
- Normalize HTTP method/host and filesystem paths before policy matching.
- Reject embedded credentials in network URLs/hosts before authorization.
- Decode and reject encoded filesystem traversal before policy matching.
- Normalize sandbox manifest network, filesystem, environment, and secret
  reference policy fields before any provider adapter sees them.
- Ensure default profiles deny or ask for secret/network/package capabilities
  unless explicitly configured.

### Phase 8: Provider Adapters

- Add runtime selection/factory support for built-in `none` and `local_test`
  adapters before external providers.
- External provider adapters must plug into the same factory/config path.
- Evaluate and add external adapters only after the interface is stable.
- Candidate adapters: OpenSandbox, OpenShell, E2B, Vercel, Modal, Daytona,
  Runloop, Cloudflare.

---

## Acceptance Criteria

The governed execution track is complete when:

- every governed surface has a capability descriptor
- every governed side effect creates an authority request
- policy decisions are deterministic and tested
- deny/ask/allow precedence is tested
- policy provenance, version, hash, and migration behavior are tested
- policy reason codes are stable enough for tests and future evals
- budget exceeded decisions are deterministic
- approvals are first-class runtime objects
- sandbox runtime is an adapter, not the policy engine
- secrets are brokered/redacted
- network and filesystem scopes are policy-addressable
- observation filtering gates untrusted output before model injection
- subagents and background tasks cannot broaden authority
- telemetry records policy decisions and violations
- audit records exist for every high-risk authority decision and result
- docs and examples explain how app builders configure policy without making
  the first agent hard to run

---

## Documentation Boundary

Internal engineering docs define architecture and contracts.

Public docs should explain:

- how to enable governed execution
- how to define a policy
- how to register capability descriptors
- how approvals work
- how to choose sandbox providers later

Public docs must not expose unfinished provider promises as completed features.

---

## Remaining Non-Goals

This design prepares the runtime for stronger authority control, but it does
not solve every security problem in the first implementation phase.

Not solved in this governance/sandbox phase:

- full information-flow control across all model/tool/memory paths
- production sandbox provider integration
- sandbox UI
- enterprise policy administration
- vendor observability dashboards
- automatic proof that a third-party MCP server is safe

Sandbox providers are optional adapters. They are not required for the first
policy implementation PR.

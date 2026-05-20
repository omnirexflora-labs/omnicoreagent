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
| Policy decision | Deterministic allow, deny, ask, or require-sandbox decision. |
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
  unknown_mcp_server: deny | ask | allow
  unknown_network_host: deny | ask | allow
  unknown_secret: deny | ask | allow
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

---

## Policy Rule Schema

```yaml
rule_id: string
effect: allow | ask | deny
capability: string
actor: ActorMatcher | null
target: TargetMatcher | null
conditions:
  risk_level: [low | medium | high | critical] | null
  data_classes: [string] | null
  tool_provider: local | mcp | workspace | artifact | skill | sandbox | null
  mcp_server: string | null
  method: string | null
  host: string | null
  path: string | null
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

---

## Capability Descriptor Contract

Every side-effecting surface must expose capability metadata.

```yaml
capability_id: string
provider: local | mcp | workspace | artifact | memory | skill | sandbox | background | subagent | serve
name: string
description: string
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

Rules:

- Missing descriptors are treated as unknown capability.
- Unknown capability follows `defaults.unknown_capability`.
- MCP tool descriptors may be generated from schemas and overridden by policy.
- Application code can provide explicit descriptors for local tools.

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
- `ask` creates an approval request and pauses execution.
- `allow` may still require sandbox enforcement.
- A decision must include enough reason text to debug policy behavior.
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
- Background tasks that need approval must enter a paused state.

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
| Background task | `background.task.create/schedule/cancel` |
| Skills | `skill.load` and `skill.execute` |
| Sandbox command | `process.exec` plus filesystem/network/secrets |
| Network | `network.*` |
| Secrets | `secret.read` or brokered secret use |
| Telemetry export | `telemetry.export` |

No side-effecting path may bypass this once the governed execution phase owns
that surface.

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
provider: none | local | docker | opensandbox | openshell | e2b | vercel | modal | daytona | runloop | cloudflare
image: string | null
working_dir: string
workspace_mount:
  source: string
  target: string
  mode: read_only | read_write
filesystem_policy: object
network_policy: object
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
- Sandbox stdout/stderr is untrusted tool output.
- Sandbox outputs go through observation, guardrails, offload, and telemetry.

---

## Filesystem and Workspace Policy

Rules:

- Workspace files are the default writable file surface.
- Host filesystem access is denied unless explicitly allowed.
- Absolute host paths are never exposed to the model unless policy allows.
- `delete` requires explicit allow or approval.
- Subagents get a narrowed workspace prefix by default.
- Sandbox mount rules are derived from workspace and filesystem policy.
- Workspace files and artifacts must retain their separate semantics:
  agent-managed files vs runtime-managed offloaded artifacts.

Required tests:

- read allowed workspace file
- write allowed workspace file
- deny write outside workspace
- deny delete without delete policy
- subagent cannot write outside scoped prefix
- sandbox manifest uses narrowed workspace mount

---

## Network Policy

Rules:

- Process/sandbox network defaults to deny.
- Tool-level network access requires explicit capability or policy.
- HTTP method and host must be policy-addressable.
- Package install requires `package.install`, not only `network.http.get`.
- Unknown hosts follow `defaults.unknown_network_host`.
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
- Tool calls require `tool.mcp.call`.
- Server-level allow does not imply all tool-level capabilities.
- Tool descriptors can be generated but policy overrides are authoritative.
- MCP tool outputs are untrusted observations.
- MCP servers cannot gain workspace, memory, network, or secret authority unless
  declared and allowed.

Required tests:

- deny unknown MCP server
- allow specific MCP tool
- deny another tool on same server
- deny MCP tool network capability not granted
- policy wraps MCP calls without modifying server code

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

- Task creation and scheduling require policy decisions.
- Durable task stores a policy snapshot id.
- Restarted tasks load the same snapshot or fail closed.
- Task retry does not reset policy budgets unless configured.
- Long-running tasks may need approval renewal.

Required tests:

- create task with policy snapshot
- restart loads snapshot
- missing snapshot fails closed
- retry preserves policy
- cancel requires policy

---

## Memory and Data Classification

Rules:

- Memory read/write is policy-addressable.
- Memory context injection must preserve trust/data classification metadata.
- User-private, credential, payment, health, source-code, and system-prompt
  classes must be representable even if early enforcement is basic.
- Future information-flow controls must be able to consume the metadata.

Required tests:

- memory write emits data class metadata
- private memory cannot be disclosed through a denied network/tool action
  once data-flow enforcement is active
- telemetry redacts configured data classes

---

## Telemetry Requirements

Governed execution emits these event types:

- `policy.request.created`
- `policy.decision.allow`
- `policy.decision.ask`
- `policy.decision.deny`
- `approval.request.created`
- `approval.resolved`
- `sandbox.session.created`
- `sandbox.exec.started`
- `sandbox.exec.completed`
- `sandbox.exec.failed`
- `policy.violation`
- `secret.access.denied`
- `secret.access.brokered`
- `network.access.denied`
- `network.access.allowed`
- `filesystem.access.denied`
- `filesystem.access.allowed`

Rules:

- Events include `trace_id` and `run_id` when available.
- Events include policy id and matched rule ids.
- Events do not include secret values.
- Denied actions still produce telemetry.
- Approval events link to authority request and decision ids.

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

---

## Failure Behavior

Rules:

- Missing policy in strict mode: deny.
- Unknown capability in strict mode: deny.
- Policy engine error: deny and emit telemetry.
- Approval timeout: deny.
- Sandbox runtime unavailable when required: deny.
- Sandbox constraint unsupported: deny.
- Telemetry write failure must not grant authority. The action may continue
  only if policy explicitly allows telemetry best-effort mode.

---

## Implementation Phases

### Phase 1: Policy Core

- Add policy models.
- Add policy evaluator.
- Add authority request and decision models.
- Add default policy builder.
- Unit test deny/ask/allow precedence.

### Phase 2: Tool and Workspace Enforcement

- Add capability descriptors for local tools and workspace tools.
- Enforce policy before local tool calls.
- Enforce policy before workspace file operations.
- Emit telemetry events.

### Phase 3: MCP Enforcement

- Add MCP server/tool capability descriptors.
- Enforce policy around MCP tool calls.
- Add per-server/per-tool rules.
- Add tests with fake MCP tools.

### Phase 4: Approval Interface

- Add approval resolver protocol.
- Add static resolver and callback resolver.
- Add paused/denied behavior.
- Add OmniServe approval API design later if needed.

### Phase 5: Subagent and Background Policy

- Derive child policy for subagents.
- Store policy snapshot for background tasks.
- Enforce restart fail-closed behavior.

### Phase 6: Sandbox Runtime Interface

- Add provider-neutral sandbox protocol.
- Add `none` and local test adapter.
- Enforce sandbox-required decisions.
- Route sandbox outputs through telemetry and observations.

### Phase 7: Provider Adapters

- Evaluate and add adapters only after the interface is stable.
- Candidate adapters: OpenSandbox, OpenShell, E2B, Vercel, Modal, Daytona,
  Runloop, Cloudflare.

---

## Acceptance Criteria

The governed execution track is complete when:

- every governed surface has a capability descriptor
- every governed side effect creates an authority request
- policy decisions are deterministic and tested
- deny/ask/allow precedence is tested
- approvals are first-class runtime objects
- sandbox runtime is an adapter, not the policy engine
- secrets are brokered/redacted
- network and filesystem scopes are policy-addressable
- subagents and background tasks cannot broaden authority
- telemetry records policy decisions and violations
- docs and examples explain how app builders configure policy without making
  the first agent hard to run

# Governed Execution Architecture

This is an internal architecture record under `engineering/architecture`, not
public product documentation.

Governed execution is the authority layer of OmniCoreAgent. It decides what an
agent, subagent, tool, MCP server, skill, background task, workspace operation,
or sandboxed process is allowed to do before the action reaches the outside
world.

The next maturity step for OmniCoreAgent is not "add a sandbox". A sandbox is
only one enforcement surface. The production primitive is:

```text
policy decides authority
  -> approvals resolve uncertainty
  -> sandbox/runtime enforces containment
  -> telemetry records evidence
  -> workspace stores outputs
```

## Research Grounding

This design takes inspiration from current agent security systems and recent
research, while keeping OmniCoreAgent's own architecture and product boundary.

- Anthropic's Claude Code sandboxing separates permission prompts from OS-level
  sandboxing and emphasizes that filesystem and network isolation must be used
  together. See https://www.anthropic.com/engineering/claude-code-sandboxing.
- Claude Code permissions use allow, ask, and deny rules, with deny taking
  precedence. They also document that application-level file rules do not cover
  arbitrary subprocesses, which is why OS-level sandboxing is still needed. See
  https://code.claude.com/docs/en/permissions.
- Claude Managed Agents added self-hosted sandboxes and MCP tunnels, showing the
  market direction: managed orchestration with execution routed into
  customer-owned or self-hosted sandbox boundaries. See
  https://claude.com/blog/claude-managed-agents-updates.
- OpenAI's Agents SDK added sandbox-aware orchestration and native sandbox
  execution while allowing developers to bring providers such as E2B, Modal,
  Vercel, Daytona, Runloop, Cloudflare, and others. See
  https://openai.com/index/the-next-evolution-of-the-agents-sdk/.
- NVIDIA OpenShell sits between agents and infrastructure with out-of-process
  policy enforcement, sandboxed execution, egress control, declarative policies,
  a gateway, and credential/inference routing. See
  https://developer.nvidia.com/blog/run-autonomous-self-evolving-agents-more-safely-with-nvidia-openshell/
  and https://github.com/NVIDIA/OpenShell.
- Alibaba OpenSandbox focuses on a unified sandbox API, multi-language SDKs,
  lifecycle APIs, Docker/Kubernetes runtimes, command/filesystem/code
  interpreter operations, network controls, and stronger isolation options such
  as gVisor, Kata Containers, and Firecracker microVM. See
  https://github.com/alibaba/OpenSandbox.
- Vercel Sandbox shows the provider pattern for isolated execution:
  Firecracker microVMs, per-sandbox filesystems and networks, fast startup,
  snapshots, SDK control, and firewall policies for egress. See
  https://vercel.com/docs/vercel-sandbox and
  https://vercel.com/docs/vercel-sandbox/concepts/firewall.
- AgentBound argues for declarative access control around MCP servers without
  requiring MCP server modifications. This is directly relevant because
  OmniCoreAgent treats MCP tools as external capability providers. See
  https://www.lucadigrazia.com/papers/fse2026.pdf.
- GAAP focuses on deterministic private-data disclosure control through data
  flow tracking and permission specifications. This matters because a sandbox
  can contain processes but cannot by itself decide whether private memory or
  user data may be disclosed to a model or remote service. See
  https://arxiv.org/abs/2604.19657.
- AgentWard frames autonomous agent security as a lifecycle problem across
  initialization, input processing, memory, decision-making, and execution. That
  matches OmniCoreAgent's harness shape better than a single "before tool call"
  check. See https://arxiv.org/abs/2604.24657.
- OWASP's agentic security work identifies risks such as tool misuse, identity
  and privilege abuse, unexpected code execution, memory/context poisoning,
  insecure inter-agent communication, cascading failures, and rogue agents. See
  https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/.

## Core Thesis

Production agents need dynamic capability and immutable authority.

```text
the runtime may discover and activate capabilities
the model may choose actions
the policy envelope must not be mutated by the model
```

The agent can request a tool call, MCP call, workspace write, shell command,
network fetch, secret access, subagent spawn, background task, or memory write.
The governed execution layer turns that request into a deterministic authority
decision before anything happens.

## Non-Goals

This architecture does not implement:

- a full sandbox provider
- dashboards or observability UI
- evaluation runners
- a replacement for telemetry
- a replacement for workspace storage
- user-facing policy authoring UI
- enterprise identity administration
- full information-flow-control implementation in the first phase
- arbitrary shell access by default

Sandbox providers and UI surfaces are adapters. Policy is the base runtime
invariant.

## System Position

Governed execution sits between the agent harness and every side-effecting
surface.

```text
model output
  -> tool call parser / subagent planner / background scheduler
  -> capability request
  -> PolicyEnvelope
       -> allow
       -> deny
       -> ask for approval
       -> require sandbox
       -> require redaction
       -> require narrower scope
  -> executor
       -> local tool / MCP tool / workspace / memory / sandbox / background / subagent
  -> telemetry evidence
  -> observation pipeline
  -> model
```

The policy envelope must be outside the model prompt. Prompt instructions may
shape what the model tries. They do not grant authority.

## Ownership Boundaries

| Layer | Owns | Does Not Own |
|-------|------|--------------|
| Policy envelope | authority decisions, capability matching, approvals, deny/ask/allow, scope narrowing | process isolation |
| Sandbox runtime | filesystem/network/process containment and execution | business authority |
| Workspace | file/state surface for notes, artifacts, offloads, outputs | policy decisions |
| Telemetry | evidence of requests, decisions, approvals, execution, errors | authorization |
| Guardrails | prompt/tool-output safety screening | authority |
| Tool registry | tool metadata and execution | global permissions |
| MCP client | external MCP tool loading/calling | trust in MCP servers |
| OmniServe | HTTP/SSE boundary and request identity | policy semantics |

## Capability Model

Every action is expressed as a capability request.

Examples:

- `tool.local.call`
- `tool.mcp.call`
- `filesystem.read`
- `filesystem.write`
- `filesystem.delete`
- `workspace.files.read`
- `workspace.files.write`
- `workspace.artifacts.read`
- `workspace.artifacts.write`
- `memory.read`
- `memory.write`
- `network.http.get`
- `network.http.post`
- `process.exec`
- `package.install`
- `secret.read`
- `observation.filter`
- `observation.inject`
- `subagent.spawn`
- `background.task.create`
- `background.task.cancel`
- `serve.response.stream`

Capability requests include actor, target, scope, inputs, declared risk, data
classes, and requested lifecycle. The policy engine evaluates the request
against an immutable policy envelope.

## Policy Envelope

The policy envelope is the runtime authority contract for one application,
agent, session, background task, or subagent.

It contains:

- provenance: source, source reference, creator, loaded time, policy hash, and
  parent policy id
- identity scope
- allowed capabilities
- denied capabilities
- approval rules
- sandbox requirements
- network rules
- filesystem/workspace rules
- memory rules
- secret access rules
- MCP server rules
- subagent delegation rules
- budgets and rate limits
- lifecycle and expiry
- data classification rules
- telemetry redaction rules

Policy snapshots are durable runtime contracts. Each snapshot carries a schema
version and policy hash. A runtime may load older policy versions only through
an explicit migration path, and migrations must never broaden authority
silently. If a policy snapshot cannot be migrated safely, execution fails
closed.

Policy rules are deterministic and ordered by precedence:

```text
deny -> ask -> allow
```

Deny wins. Ask requires app-owned external authority. Without a resolver, the
runtime fails closed with a stable approval-required error. Allow may still
attach constraints such as sandbox profile, timeout, output limit, redaction, or
workspace destination.

## Trust Zones

OmniCoreAgent must treat every input and capability source as belonging to a
trust zone.

| Zone | Examples | Default |
|------|----------|---------|
| User request | direct app user message | trusted for intent, not for authority |
| System/domain instructions | app-owned prompts and config | trusted |
| Local tools | application Python functions | trusted only by declared capability |
| MCP tools | external MCP servers | untrusted until policy-scoped |
| Skills | packaged Python/Bash/Node capabilities | untrusted until signed/scoped in later phases |
| Workspace files | agent-written state and artifacts | mixed trust |
| Memory | session history and summaries | mixed trust |
| Web/tool output | fetched pages, API output, documents | untrusted |
| Sandbox process output | stdout, stderr, files | untrusted until inspected |

Trust zone metadata must move with observations and telemetry. A future
evaluation system should be able to see whether a failure came from untrusted
tool output, poisoned memory, an overbroad MCP capability, or missing approval.

## Enforcement Points

Governed execution must eventually intercept these surfaces:

1. local tool execution
2. MCP tool execution
3. workspace file reads/writes/deletes
4. artifact reads/writes
5. memory reads/writes/summarization
6. context injection from memory/workspace/tool output
7. subagent spawn and subagent tool access
8. background task creation, retry, cancel, and schedule
9. skill loading and skill execution
10. process/shell/code execution
11. network egress
12. secret access
13. OmniServe request/session boundaries
14. telemetry export
15. model-bound observation filtering and redaction

After the policy core exists, the first enforcement phases should start with
the surfaces that can cause external side effects: tools, MCP, workspace,
process, network, secrets, subagents, and background tasks. Observation
filtering must also be governed because sandbox stdout/stderr, MCP responses,
web output, and workspace file content are untrusted until filtered before
model injection.

## Sandbox Runtime

The sandbox runtime is an adapter boundary, not the policy engine.

The agent harness and orchestration loop do not run inside the sandbox by
default.

The sandbox is a disposable execution worker used for commands, code,
filesystem operations, network-restricted execution, and other contained side
effects. The harness remains outside the sandbox so policy evaluation, retries,
checkpointing, approvals, telemetry, and recovery survive sandbox failure.

OmniCoreAgent should define a provider-neutral `SandboxRuntime` interface with
adapters for:

- `none`: no external sandbox, only policy checks
- `local`: local restricted execution for development
- future Docker or process sandbox
- future OpenSandbox adapter
- future OpenShell adapter
- future E2B, Vercel, Modal, Daytona, Runloop, Cloudflare adapters

The runtime interface should support:

- create sandbox session
- attach workspace mount or workspace sync
- execute command
- run code
- read/write files inside sandbox
- set network policy
- set environment variables
- inject approved secrets through a broker
- stream stdout/stderr/events
- snapshot or checkpoint when supported
- terminate and cleanup

Sandbox adapters must not decide whether an action is allowed. They only
enforce the decision and report evidence.

## Filesystem and Workspace Boundary

OmniCoreAgent already has workspace storage across local, S3, and R2. Governed
execution must treat workspace as the allowed state surface.

Rules:

- agent-visible file operations default to workspace scope
- host filesystem reads/writes are denied unless explicitly allowed
- sandbox filesystem mounts are derived from workspace policy
- destructive operations require explicit policy or approval
- workspace artifacts and workspace files keep separate purposes but share the
  same storage backend
- subagents receive scoped workspace prefixes unless the parent policy grants
  broader access

## Network Boundary

Network access must be policy controlled independently of tool permission.

Rules:

- default network is deny for sandboxed process execution
- MCP servers get explicit server-level and tool-level network trust metadata
- HTTP methods and domains should be policy-addressable
- package installs are separate from generic network access
- credentialed network calls should go through a secret or service broker
- outbound calls to unknown domains require approval or deny

OpenShell and Vercel both show why egress control is central: without network
policy, a compromised agent can exfiltrate local or workspace data.

## Secret Boundary

Secrets are not environment variables casually handed to agent processes.

Rules:

- secrets are never visible to the model by default
- secrets are never placed into sandbox environment by default
- tools request secret capabilities by name and purpose
- policy grants scoped secret use, not raw secret read, where possible
- brokers inject credentials into external calls when needed
- telemetry records the secret reference, not the secret value
- prompt/tool output redaction runs before persistence

## Data Classification

Governed execution should support data classes from the beginning, even if
early enforcement is simple.

Initial classes:

- `public`
- `internal`
- `user_private`
- `credential`
- `payment`
- `health`
- `source_code`
- `system_prompt`
- `tool_output_untrusted`

GAAP shows that data disclosure cannot be solved only by sandboxing. OmniCoreAgent
needs data classification in telemetry, memory, workspace, and policy decisions
so future information-flow controls can be added without replacing the model.

## Policy Composition

Multiple policy layers may apply at once:

- system default policy
- application policy
- agent policy
- session policy
- task policy
- subagent policy
- approval-derived temporary scope

The effective policy is computed by narrowing authority, not by loosely merging
permissions.

Rules:

- deny always survives composition
- child, session, task, and subagent policies cannot remove parent denies
- approval-derived scopes are temporary and cannot exceed the effective parent
  policy
- ambiguous composition fails closed
- policy provenance and policy hashes must be retained through composition

## Audit Log

Telemetry records operational evidence. Governed execution also needs durable
authority evidence.

Audit records are the durable record of policy-relevant authority decisions.
They include:

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

Every high-risk authority decision and result must produce audit records:
allow, deny, ask, approval resolution, and execution summary. Audit records must
not contain raw secrets. Audit write failure for high-risk actions fails closed
unless policy explicitly allows best-effort audit.

## Approval Layer

Approvals are deterministic runtime objects, not chat messages.

Approval requests include:

- action id
- actor
- requested capability
- target
- reason
- risk level
- proposed narrowed scope
- timeout
- evidence links

Approval results include:

- approved or denied
- approver id
- effective scope
- expiry
- audit reason

Approval resolvers are app-owned extensions. The core harness only exposes the
small resolver contract and fails closed when no resolver exists. Production
high-risk approvals cannot come from unauthenticated strings or model output.
The core runtime must not add a built-in babysitting loop, approval UI, callback
server, or OmniServe approval workflow by default.

Approvals may come from:

- application-owned resolver
- CLI prompt implemented by the app
- application-owned API/dashboard
- static policy default
- future human review UI

## Subagents

Subagents are scoped workers, not authority expansion.

A subagent policy is derived from parent policy by narrowing:

- role
- task
- allowed tools
- MCP servers
- workspace prefix
- memory scope
- sandbox profile
- network scope
- secret scope
- budget
- deadline
- return contract

Subagents cannot grant themselves new authority. Recursive subagent spawning is
denied unless the parent policy explicitly allows it with depth and budget
limits.

## Background Tasks

Background tasks must carry policy across time.

Every durable task stores:

- policy snapshot id
- task capability scope
- sandbox profile
- workspace scope
- memory scope
- allowed tools/MCP
- budget and expiry
- approval state

If a task resumes after restart, it resumes under the stored policy snapshot
or fails closed if that policy can no longer be resolved.

## MCP Tools

MCP tools are external capability providers. They need explicit policy because
they may access host files, network, APIs, browser state, credentials, or other
systems.

OmniCoreAgent should model:

- MCP server start/connect authority
- MCP server identity
- MCP server transport
- tool names and schemas
- declared capabilities
- allowed methods/actions
- workspace and memory access
- network trust
- secret access
- approval requirements
- schema and tool hash drift

AgentBound's MCP-focused access-control model is important here: enforcement
must be possible even when the MCP server itself is not modified.

Local MCP servers must not run with ambient host authority by default. They need
a scrubbed operational environment, explicit mounts, and sandbox or egress
controls when their declared capabilities require them. If a server reports a
different identity during initialization than the configured identity used to
start it, the reported identity must be authorized before its tools are
registered. Remote MCP servers need pinned identity and schema/tool drift
checks, usually through a gateway or proxy boundary.

Unsupported MCP transports fail closed. The runtime must never interpret an
unknown transport as stdio or any other side-effecting transport.

## Telemetry Integration

Governed execution must emit telemetry for:

- capability request created
- policy decision returned
- approval requested
- approval resolved
- sandbox session created
- sandbox command started/completed/failed
- network denied/allowed
- filesystem denied/allowed
- secret denied/used by broker
- policy snapshot attached
- policy violation
- sandbox cleanup

Telemetry records must include evidence references without leaking secrets or
private payloads.

## Default Developer Experience

OmniCoreAgent must stay easy for new users.

Defaults:

- first agent works without configuring a policy file
- default policy allows safe in-process agent operations
- workspace files remain scoped to the configured workspace
- dangerous process/network/secret capabilities are denied or ask-by-default
- no arbitrary shell/code execution is enabled by default
- advanced users can pass explicit policy config

This keeps the harness usable while giving production app builders the control
surface they need.

## Phase Order

1. Policy data model and decision engine
2. Capability descriptors for local tools, MCP tools, workspace, memory,
   subagents, background tasks, and skills
3. Enforcement adapters for local tool, MCP, workspace, subagent, and
   background task surfaces
4. Approval resolver interface
5. Telemetry events for decisions and violations
6. Sandbox runtime interface with a no-op/local adapter
7. Network/filesystem/secret policy contracts
8. Provider adapters after the interface is stable
9. Public docs and examples

Policy comes before sandbox provider integration because sandboxing without
authority policy only contains some execution risks. It does not answer what
the agent is allowed to do.

## Acceptance Principle

This track is not complete when a command runs inside a sandbox.

It is complete when every side-effecting OmniCoreAgent surface can answer:

- who requested this action
- what authority was requested
- why it was allowed, denied, or paused
- what scope was granted
- what enforcement mechanism was used
- what evidence was recorded
- what cleanup happened

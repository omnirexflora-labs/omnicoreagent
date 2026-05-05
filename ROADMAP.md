# 🗺️ OmniCoreAgent — Public Roadmap

> **Vision**: Make OmniCoreAgent the definitive production runtime for autonomous AI agents — where agents are governed, monetizable, sandboxed, and observable by default.

> **Status**: Updated February 2026

---

## ✅ What We've Shipped (v0.1 → v0.3.8)

| Capability | Status |
|------------|--------|
| OmniCoreAgent (ReAct core) | ✅ Shipped |
| Multi-Tier Memory (Redis, Postgres, MongoDB, SQLite, In-Memory) | ✅ Shipped |
| Runtime Memory Switching | ✅ Shipped |
| Context Engineering (dual-layer + tool offloading) | ✅ Shipped |
| MCP Client (stdio, SSE, Streamable HTTP, OAuth) | ✅ Shipped |
| Dynamic Subagents (core harness orchestration) | ✅ Shipped |
| Local Tools (ToolRegistry) | ✅ Shipped |
| Agent Skills (polyglot packages) | ✅ Shipped |
| Workspace Memory (S3, R2, Local) | ✅ Shipped |
| Sub-Agents | ✅ Shipped |
| Background Agents | ✅ Shipped |
| Workflows (Sequential, Parallel, Router) | ✅ Shipped |
| BM25 Tool Retrieval | ✅ Shipped |
| Guardrails (prompt injection protection) | ✅ Shipped |
| Observability (Opik tracing + metrics) | ✅ Shipped |
| OmniServe (REST/SSE production API) | ✅ Shipped |
| Unified Workspace Directory | ✅ Shipped (v0.3.8) |
| New Documentation Site | ✅ Shipped (v0.3.8) |

---

## 🚀 Roadmap

### Phase 1: Agent Runtime & Sandbox (v0.4.x)
> *"Agents should run like processes — isolated, privileged, governed."*

**Why it matters**: OpenClaw went viral because agents could *do things* on your system. But it also exposed 21,000+ instances to RCE vulnerabilities. We do it right — with isolation.

#### 🔒 OmniRuntime — Sandboxed Code Execution
- [ ] **Execution sandbox** — isolated environment for agent-generated code (Python, Bash, Node.js)
- [ ] **Micro-VM isolation** — option for untrusted code execution via Firecracker/gVisor
- [ ] **Container sandbox** — lightweight Docker-based isolation for standard use
- [ ] **Privilege levels** — `read-only`, `scoped`, `full-access` per agent
- [ ] **File system policies** — define which paths an agent can read/write/execute
- [ ] **Network policies** — control outbound access (allowlist domains, block by default)
- [ ] **Resource limits** — CPU, memory, execution time caps per agent run
- [ ] **Sandbox providers** — native support for E2B, Daytona, Modal as execution backends

#### 🛡️ Permission System
- [ ] **Tool-level permissions** — approve/deny specific tools per agent or per session
- [ ] **Human-in-the-loop gates** — require approval before executing destructive actions
- [ ] **Action audit log** — immutable log of every tool call, file access, and network request
- [ ] **Policy-as-code** — YAML/JSON policy files that define agent boundaries

---

### Phase 2: OmniGate — Agent Monetization (v0.5.x)
> *"Every agent should be deployable as a paid service, with zero payment code."*

**Why it matters**: The x402 protocol makes micropayments native to HTTP. OmniGate makes agent monetization a one-line config change.

#### 💰 OmniGate Core
- [ ] **Execution boundary** — gate that wraps `agent.run()` to enforce session-based access
- [ ] **Session management** — immutable sessions with `turn_limit`, `ttl_seconds`, `payment_hash`
- [ ] **Turn accounting** — atomic turn deduction only on successful output (crash = no charge)
- [ ] **Dev Mode / Enforced Mode** — toggle between free local dev and paid production
- [ ] **x402 integration** — verify payment via x402 facilitator before session creation

#### 🔗 OmniGate Developer Experience
- [ ] **One-line enablement** — `enable_omnigate=True` on any agent
- [ ] **Session status API** — `turns_remaining`, `ttl_remaining`, `session_status`
- [ ] **OmniServe integration** — paid agents exposed via REST/SSE with built-in session flow
- [ ] **Agent marketplace prep** — metadata schema for publishing agents (name, description, pricing)
- [ ] **Multi-agent session propagation** — sessions flow across dynamic and explicit sub-agent calls

---

### Phase 3: Agent Evaluations & Testing (v0.6.x)
> *"You can't ship what you can't measure."*

**Why it matters**: No major framework has solved agent evaluation well. This is a gap we can own — making it trivial to test, benchmark, and validate agent behavior.

#### 📊 OmniEval — Evaluation Framework
- [ ] **Task-based benchmarks** — define expected outcomes, measure agent success rate
- [ ] **Tool call accuracy** — did the agent call the right tool with the right args?
- [ ] **Conversation quality scoring** — automated scoring of response relevance and helpfulness
- [ ] **Regression testing** — re-run evaluation suites on every code change
- [ ] **Cost tracking** — tokens used, latency, and dollar cost per evaluation run
- [ ] **Eval datasets** — curated datasets for common agent tasks (search, code, data analysis)

#### 🧪 Testing Primitives
- [ ] **Mock tool backends** — simulate tool responses for deterministic testing
- [ ] **Replay mode** — record and replay agent sessions for debugging
- [ ] **Snapshot testing** — compare agent behavior across model versions
- [ ] **CI/CD integration** — `omnicoreagent eval run` command for pipelines

---

### Phase 4: OmniCoder — CLI Coding Agent (v0.7.x)
> *"A coding agent powered by OmniCoreAgent's harness runtime — your terminal copilot."*

**Why it matters**: Developer tools are the highest-signal distribution channel. An open-source CLI coding agent built on OmniCoreAgent puts the framework in every developer's hands.

#### 💻 OmniCoder
- [ ] **CLI interface** — `omnicoder "refactor auth module"` from any terminal
- [ ] **Codebase understanding** — file tree analysis, dependency resolution, symbol search
- [ ] **Multi-file editing** — coordinated changes across multiple files
- [ ] **Git-aware** — automatic branch creation, commits, and PR descriptions
- [ ] **Sandboxed execution** — test changes in OmniRuntime before applying
- [ ] **Interactive mode** — ask clarifying questions, show diffs, request approval
- [ ] **Powered by OmniCoreAgent harness** — automatic task decomposition for complex refactors
- [ ] **Language support** — Python, TypeScript, Go, Rust (extensible via skills)

---

### Phase 5: Personal Agent & Agent OS (v0.8.x)
> *"Your AI that knows you, runs locally, and acts on your behalf."*

**Why it matters**: OpenClaw proved the demand — personal agents that manage your inbox, calendar, and daily life are inevitable. We build the secure, extensible version.

#### 🧠 OmniPersonal — Personal Agent Runtime
- [ ] **Local-first** — runs on user's machine, data never leaves their control
- [ ] **Messaging interface** — interact via WhatsApp, Telegram, Discord, or native CLI
- [ ] **Proactive scheduling** — autonomous tasks on intervals (leveraging Background Agents)
- [ ] **Personal knowledge base** — learn from interactions, documents, and preferences over time
- [ ] **Cross-app automation** — email, calendar, file management, smart home via skills

#### 🖥️ Agent Operating System Primitives
- [ ] **Agent registry** — discover, install, and manage agents like packages
- [ ] **Inter-agent communication** — agents call agents with standardized message passing
- [ ] **Shared memory** — agents read/write to shared workspace for collaboration
- [ ] **Agent lifecycle management** — start, stop, pause, resume, and scale agents
- [ ] **Priority scheduling** — high-priority agents preempt lower-priority ones
- [ ] **System dashboard** — web UI showing all running agents, resource usage, and logs

---

### Phase 6: Enterprise & Scale (v1.0)
> *"The production-grade release."*

#### 🏢 Enterprise Features
- [ ] **Multi-tenancy** — per-tenant agent isolation, config, and billing
- [ ] **SSO / RBAC** — role-based access control for agent management
- [ ] **Compliance logging** — SOC 2, HIPAA-ready audit trails
- [ ] **Horizontal scaling** — distributed agent execution across multiple nodes
- [ ] **Agent versioning** — deploy, rollback, and A/B test agent versions
- [ ] **SLA guarantees** — latency, uptime, and throughput commitments

#### 🌍 Ecosystem
- [ ] **Agent Marketplace** — publish and discover paid/free agents
- [ ] **Plugin SDK** — third-party extensions for memory backends, tool providers, LLM providers
- [ ] **OmniCloud** — managed hosting for agents (optional SaaS offering)

---

## 🏆 Competitive Positioning

| Capability | OmniCoreAgent | OpenAI SDK | LangGraph | CrewAI | OpenClaw |
|------------|:---:|:---:|:---:|:---:|:---:|
| Runtime memory switching | ✅ | ❌ | ❌ | ❌ | ❌ |
| 5 memory backends | ✅ | ❌ | ❌ | ❌ | ❌ |
| MCP native (stdio + HTTP + SSE) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Context engineering | ✅ | ❌ | 🔶 | ❌ | ❌ |
| Multi-agent orchestration | ✅ | 🔶 | ✅ | ✅ | ❌ |
| Background agents | ✅ | ❌ | ❌ | ❌ | ✅ |
| Sandboxed execution | 🔜 | ✅ | ❌ | ❌ | 🔶 |
| Agent monetization (x402) | 🔜 | ❌ | ❌ | ❌ | ❌ |
| Built-in evaluations | 🔜 | ❌ | 🔶 | ❌ | ❌ |
| CLI coding agent | 🔜 | ❌ | ❌ | ❌ | ❌ |
| Personal agent runtime | 🔜 | ❌ | ❌ | ❌ | ✅ |
| Agent OS primitives | 🔜 | ❌ | ❌ | ❌ | 🔶 |
| Model-agnostic (9+ providers) | ✅ | ❌ | ✅ | ✅ | ✅ |
| Production API server | ✅ | ❌ | ✅ | ❌ | 🔶 |

✅ = shipped &nbsp;&nbsp; 🔶 = partial &nbsp;&nbsp; 🔜 = planned &nbsp;&nbsp; ❌ = not available

---

## 📅 Estimated Timeline

| Phase | Target | Headline Feature |
|-------|--------|------------------|
| **v0.4.x** | Q1 2026 | OmniRuntime (Sandbox + Permissions) |
| **v0.5.x** | Q2 2026 | OmniGate (Agent Monetization via x402) |
| **v0.6.x** | Q2–Q3 2026 | OmniEval (Evaluation Framework) |
| **v0.7.x** | Q3 2026 | OmniCoder (CLI Coding Agent) |
| **v0.8.x** | Q4 2026 | OmniPersonal (Personal Agent + Agent OS) |
| **v1.0** | Q1 2027 | Enterprise + Marketplace |

---

## 🤝 Contributing

We welcome contributions to any roadmap item! Check the [Contributing Guide](CONTRIBUTING.md) or open a [Discussion](https://github.com/omnirexflora-labs/omnicoreagent/discussions) to propose ideas.

**High-impact areas for contributors:**
- Sandbox execution backends (E2B, Daytona, Modal integrations)
- Evaluation datasets and benchmarks
- MCP integrations and project-owned tool examples
- Agent skills for personal automation
- OmniCoder language support

---

<p align="center">
  <strong>Building the cognitive runtime for the age of autonomous agents</strong>
</p>

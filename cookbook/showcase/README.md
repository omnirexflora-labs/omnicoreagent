# 🏆 Showcase: Full Production Applications

> **Real-world applications** built with OmniCoreAgent. Complete with Docker, monitoring, and production configurations.

These aren't simple examples — they're **fully functional applications** that demonstrate what you can build.

---

## 🎯 Featured Applications

| Application | Description | Status |
|-------------|-------------|--------|
| **[OmniAudit (omniavelis)](./omniavelis)** | Autonomous Multi-Agent Claims Audit System | ✅ Ready |
| **[DevOps Copilot](./devops_copilot_agent)** | AI-Powered DevOps Automation | ✅ Ready |
| **[Deep Code Agent](./deep_code_agent)** | Code Analysis with Sandbox Execution | 🚧 Coming Soon |

---

## 🔬 OmniAudit (omniavelis)

**Healthcare Claims Auditing Pipeline** built for [Avelis](https://avelishealth.com/)

```
[Claim] → RuleCheckerAgent → EvidenceAgent → AuditSynthAgent → AppealAgent
             │                    │                │                │
        Violations          Policy Evidence    Audit Packet    Provider Letter
```

**What It Does**:
- Detects billing errors (duplicates, non-covered codes, threshold breaches)
- Cites policy evidence for every finding
- Generates professional appeal letters
- Full audit trail with timestamps

**OmniCoreAgent Features Used**:
- ✅ Multi-agent workflows
- ✅ Memory summarization
- ✅ Context management
- ✅ Guardrails for audit integrity
- ✅ Event streaming

```bash
cd cookbook/showcase/omniavelis
uv sync
uv run test_audit_pipeline.py
```

---

## 🔧 DevOps Copilot

**Production-Grade DevOps Automation** with full observability stack

**Features**:
- 🖥️ Rich CLI for interactive DevOps tasks
- 🐳 Docker containerized with all DevOps tools
- 📊 Prometheus metrics + Grafana dashboards
- 🔒 Rate limiting and security controls
- 📝 Full audit logging

**OmniCoreAgent Features Used**:
- ✅ Memory with summarization
- ✅ Context management for long sessions
- ✅ Prompt injection guardrails
- ✅ Agent metrics tracking
- ✅ Event and memory router switching

```bash
cd cookbook/showcase/devops_copilot_agent
make up          # Start Redis, Prometheus, Grafana
make cli         # Launch interactive CLI
make grafana     # Open monitoring dashboard
```

---

## 🚧 Deep Code Agent (Coming Soon)

**Code Analysis and Execution Agent** with sandboxed execution

This showcase is being upgraded to demonstrate the full power of OmniCoreAgent. Check back soon!

---

## 🚀 What You'll Learn

These showcase applications demonstrate:

1. **Multi-Agent Pipelines** - Chaining agents for complex workflows
2. **Production Deployment** - Docker, docker-compose, Makefiles
3. **Observability** - Prometheus metrics, Grafana dashboards
4. **Security** - Rate limiting, audit logging, guardrails
5. **CLI Design** - Rich interactive command-line interfaces
6. **Memory Management** - Summarization, context management

---

**Ready to build your own?** Start with [Getting Started](../getting_started) to learn the fundamentals.

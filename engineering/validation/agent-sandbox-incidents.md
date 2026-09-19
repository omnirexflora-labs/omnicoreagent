# Agent sandbox and harness incidents (research, 2026-09-19)

Research for the governed execution plan's security hardening unit (E5b).
Sources were fetched on 2026-09-19; OpenAI's own posts returned HTTP 403, so
their content comes from The Hacker News and Fortune coverage.

## Incidents

| # | Incident | Failure mode | Sources |
| --- | --- | --- | --- |
| 1 | OpenAI evaluation agents (ExploitGym, July 2026) broke out through zero-days in the JFrog Artifactory package proxy (the sandbox's allowed exit), used its shared cache as a message board between about 1,200 agents, faked tool-call transcripts, and breached Hugging Face production (instance-metadata credentials, environment secrets, template injection). Detected about a week later. | Allowed exit attackable and shared; credentials reachable; relaxed safety settings; log tampering; slow detection | [OpenAI](https://openai.com/index/hugging-face-model-evaluation-security-incident/), [Hugging Face timeline](https://huggingface.co/blog/agent-intrusion-technical-timeline), [METR](https://metr.org/blog/2026-08-26-openai-hugging-face-incident-investigation/), [JFrog](https://jfrog.com/blog/jfrog-and-openai-collaboration-on-zero-day-security-findings/), [The Hacker News](https://thehackernews.com/2026/07/openai-says-its-own-ai-models-escaped.html) |
| 2 | smolagents LocalPythonExecutor escapes (CVE-2025-5120, CVE-2025-9959) | Python-level sandbox (weak isolation) | [GHSA-6v92-r5mx-h5fx](https://github.com/advisories/GHSA-6v92-r5mx-h5fx), [JFrog](https://research.jfrog.com/vulnerabilities/smolagents-local-python-sandbox-escape-jfsa-2025-001434277/) |
| 3 | Claude Code bypassed a denylist with `/proc/self/root/usr/bin/npx`, then asked to disable its sandbox; ran wget through the dynamic linker to evade an exec-based gate (Ona, March 2026) | Name-based denylist; sandbox escape hatch; approval fatigue; single-syscall enforcement | [Ona](https://ona.com/stories/how-claude-code-escapes-its-own-denylist-and-sandbox) |
| 4 | Cursor "DuneSlide" (CVE-2026-50548/50549): injected content set a tool's `working_directory` to a system path, which was added to the sandbox's writable list; symlink check failed open | Model-chosen arguments widened sandbox policy; fail-open check | [The Hacker News](https://thehackernews.com/2026/07/critical-cursor-flaws-could-let-prompt.html) |
| 5 | Cursor CurXecute (CVE-2025-54135) and Codex CLI (CVE-2025-61260): the agent or repository wrote its own MCP/config files, which ran without approval | Agent or repository controls its own config and policy | [Tenable](https://www.tenable.com/blog/faq-cve-2025-54135-cve-2025-54136-vulnerabilities-in-cursor-curxecute-mcpoison), [GHSA-xrxf-jgv3-qmrm](https://github.com/advisories/GHSA-xrxf-jgv3-qmrm) |
| 6 | Gemini CLI: an "always allow" on `grep` let `grep ...; env \| curl ...` run (Tracebit, 2025) | Prefix allowlist; auto-approve | [Tracebit](https://tracebit.com/blog/code-exec-deception-gemini-ai-cli-hijack) |
| 7 | Claude code interpreter exfiltration through the allowed api.anthropic.com host with an attacker's key (October 2025) | Allowed network channel used for exfiltration | [Embrace The Red](https://embracethered.com/blog/posts/2025/claude-abusing-network-access-and-anthropic-api-for-data-exfiltration/) |
| 8 | Supabase MCP: a `service_role` key let an injected support ticket read and leak private tokens (July 2025) | Over-privileged credentials in reach | [General Analysis](https://generalanalysis.com/blog/supabase-mcp-blog), [Supabase](https://supabase.com/blog/defense-in-depth-mcp) |
| 9 | Replit agent deleted a production database during a code freeze (July 2025) | Production credentials in reach; no hard gate on destructive actions | [Fortune](https://fortune.com/2025/07/23/ai-coding-tool-replit-wiped-database-called-it-a-catastrophic-failure/) |

Unverified reports (not relied on): OpenAI agents coordinating on an external
wiki (TechCrunch, 2026-09-04), and a later compromise of OpenAI's own
infrastructure mentioned by METR.

## Failure classes

1. The sandbox's allowed exit is itself attackable or shared (#1).
2. An allowed channel is used for exfiltration (#6, #7, #8).
3. Credentials inside the sandbox or within reach (#1, #8, #9).
4. The agent or repository edits its own config or policy (#4, #5).
5. Model-chosen tool arguments widen privileges (#4).
6. Weak isolation: language-level sandboxes, one enforcement point (#2, #3).
7. String or prefix matching in allowlists and denylists (#3, #6).
8. Escape hatches, approval fatigue, auto-approve (#3, #5, #6).
9. Relaxed safety settings and goal fixation (#1, #3, #9).
10. Log tampering and slow detection (#1).

Found in OmniCoreAgent while this research ran (fixed in E3b): skill paths
were checked with a string prefix, so `../skills-evil` passed for
`/x/skills` (class 7).

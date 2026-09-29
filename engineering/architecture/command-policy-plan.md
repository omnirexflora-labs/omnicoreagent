# Plan: policy rules on the text of a shell command

Recording real footage of 0.4.3 (2026-09-29) needed a rule "deny `rm -rf`" and
found none can be written: every command reaches the policy as `sh` (the
`execute` tool runs `sh -c <text>`), and the request carries only the program
name and argument count. Checking it found a worse gap: **a person asked to
approve a command on the host sees `sh` and an argument count, not the command,
and the approval is not bound to the command text** (`request_digest` has no
command). The research behind this plan, with sources, is
`command-policy-research.md` next to it: how Claude Code, Codex, Gemini CLI,
NVIDIA OpenShell and others do it, the known bypasses, and the design below.

## Decisions

1. **Command rules narrow an intent; the sandbox is the boundary.** Claude Code's
   own docs say its command rules are not a security boundary, and every
   denylist that relied on text was bypassed (their CVEs). On the host, command
   rules are the main control, so defaults there stay conservative (ask). The
   docs say this plainly.
2. **Parse, don't match strings.** `tree-sitter` + `tree-sitter-bash` (MIT, wheels
   for 3.12–3.14, 1.5 MB), as core dependencies: governance must not change
   meaning with what is installed. `bashlex` is GPL and unmaintained; `shlex`
   cannot see `$(...)`.
3. **Deny and ask: any sub-command, anywhere.** Nested in `$(...)`, `<(...)`,
   heredocs, loops, functions, `sh -c '...'` (to depth 3), `xargs`, `find -exec`,
   and behind wrappers (`sudo`, `env`, `timeout`, `nohup`, `command`, `exec`).
4. **Allow must be proven.** A command allow applies only when every sub-command
   is plain (literal program and arguments, no expansion or substitution, no
   redirect to a file, no leading variables unless listed) and each matches an
   allow rule. Opaque commands (parse error, `eval`/`source`, piping into a
   shell, a dynamic program, over 10,000 characters) are never allowed by a
   command rule; they fall to the other rules and the mode.
5. **Approvals show and bind the command.** The approval request carries the
   sub-commands a person is approving (program and arguments), and the digest
   includes the exact command text, so approving `ls` cannot approve anything
   else. A pending host approval from 0.4.4 or earlier is asked again after the
   upgrade (safe; said in the upgrade notes).
6. **No change for existing policies.** A policy without `command` rules decides
   exactly as before. An unknown key in a rule becomes a clear policy load error
   instead of a bare `TypeError`.
7. **Rules carry examples** (`match` / `not_match`), checked when the policy
   loads, as Codex does: the cheapest guard against a rule that does not mean
   what its author thinks.

## Units

- **C1** · `governance/commands.py`: `parse_command(argv) -> ParsedCommand`
  (sub-commands with argv, program, path kind, plain, redirects, env names, how
  it was reached; opaque with reasons; digest). Tests: the parse-level table.
- **C2** · The `command` field on rules (`program`, `prefix`, `args_any`,
  `redirect`, `env`), `examples` checked at load, unknown keys as load errors.
- **C3** · Evaluation: any-deny, any-ask, all-allow, opaque; `command_opaque`
  reason code; reasons name the program and rule, never the arguments.
- **C4** · Wiring: `_sandbox_authority_request` (every command passes through it:
  host `local`, every sandbox provider, skill scripts, Harbor) parses once; the
  approval request shows the sub-commands; `request_digest` binds the text.
- **C5** · Tests at the end: the 17 ordinary cases and 27 bypass attempts of the
  research's section 6, each asserting deny/ask/never-allow; a differential test
  that runs the real `sh -c` with every program shimmed to log its argv, and
  asserts what really ran is within what the parser saw or the parse was opaque;
  and an end-to-end run of a real agent on the host, asked to `rm -rf`, denied
  by a `command` rule by name.
- **C6** · Docs: `reference/policy.mdx` (the field, semantics, limits, the
  "not a boundary" paragraph, `git -c`/`-C` caveats), `execution.mdx`,
  `approvals.mdx` (what an approver now sees), upgrade notes.

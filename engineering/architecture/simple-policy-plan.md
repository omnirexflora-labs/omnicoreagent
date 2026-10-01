# A simple policy: the sandbox is the boundary, rules decide when to ask

Status: proposed for 0.5.0 (2026-10-01). It needs the maintainer's yes on the three decisions at
the end before any code changes.

## Why

Every release gate since rc4 has found a new way past our shell-command rules, most recently
`>& file`. The cause is the design, not the individual bugs. We try to make a static parse of bash
*prove* what a command will do, in both directions: a deny rule must catch every spelling, and an
allow rule must prove the command harmless. Bash is too large for that, so `commands.py` grows
with every report (593 lines: wrappers, `xargs`, `find -exec`, `sh -c` three deep, redirect forms,
env assignments) and still misses things.

The tools people trust do not do this.

- **OpenShell (NVIDIA)** does not parse commands.
  - Its policy covers files, network and processes, enforced by the kernel (Landlock, seccomp) and
    a proxy that is the only way out.
  - A blocked connection goes to a person, who approves it while the agent runs.
- **Codex CLI** splits the two jobs: "the sandbox controls what Codex can do; the approval policy
  controls when it must ask".
  - Command rules are prefix rules (`prefix_rule`), with `match`/`not_match` examples checked at
    load.
  - Only a plain chain of words is split; anything with redirects, substitution, variables or globs
    is one invocation, which no rule auto-allows.
- **Claude Code** works the same way.
  - The OS sandbox (bubblewrap, Seatbelt) plus a network proxy is the boundary.
  - Its docs say a deny rule "isn't a security boundary around the program".

A survey of our own providers (2026-10-01) found the opposite problem on the sandbox side: we
ask people to approve things that nothing enforces.

| Provider | Network off | Host allow-list | Filesystem path lists | Workspace mount |
|---|---|---|---|---|
| docker | enforced (`network_mode=none`) | refused | **ignored** | enforced (read-only honoured) |
| e2b | enforced, checked from inside | refused | **ignored** | **ignored** |
| daytona | enforced, checked from inside | **sent as hostnames to an API that takes IPs** | **ignored** | **ignored** |
| modal | enforced, **not checked** | enforced | **ignored** | **ignored** |
| vercel | enforced, **not checked** | refused | **ignored** | **ignored** |
| http | forwarded, trusted | forwarded | **not sent** | **not sent** |
| local | none (refused unless `allow`) | refused | refused unless `allow` | n/a |

Other gaps from the survey:

- `readable_paths`, `writable_paths` and `denied_paths` are approved and then applied by no
  provider.
- `denied_hosts`, `resources.timeout_seconds` and `gpu` are silently ignored by most providers.
- A policy's `network.*` rules never apply to traffic from inside a sandbox.

## The design

Three layers, each doing one job.

### 1. The sandbox: what a command can touch (the boundary)

- **Settings that are enforced:**
  - `network`: `"off"` (the default) or `"on"`, or a list of hosts on providers that enforce one.
  - The workspace mount and whether it is read-only, where the provider supports a mount.
  - Resources.
- **Each provider declares what it enforces.** A manifest asking for anything the chosen provider
  does not enforce is **refused when the agent is built**, naming the provider and the setting.
  Nothing is silently ignored, and nothing is approved that will not be applied.
- **Every provider that says the network is off is checked from inside**, as e2b and daytona are
  today. This is added for modal and vercel.
- **The filesystem path lists are removed.** No provider can apply them. What a sandboxed command
  can reach is the sandbox's own disk plus the workspace mount, and the docs say exactly that.
- **`local` is not a sandbox and is never treated as one.** Commands there are host commands, and
  the default profiles ask or refuse them, as now.

### 2. Capability rules: what the agent's own tools may do (unchanged)

Allow, ask or deny on capabilities, with path, host and tool targets. These decide calls the
runtime makes itself (`read_file`, `http_request`, MCP tools, spawning workers), where the target
is exact. They have held through every gate, with the rc7 fixes for folders and links. No change.

### 3. Command rules: when to ask about a shell command (made small)

- **One matcher, `prefix`.** It lists the first words, each a literal or a list of alternatives,
  with a decision: `allow`, `ask` or `deny`. `program: "rm"` stays as shorthand for `prefix: ["rm"]`.
- **`examples` stay** (`match` and `not_match`, checked when the policy loads).
- **Removed:** `args_any`, `redirect`, `env`, and program globs.
- **Splitting.** Only a plain chain of words joined by `&&`, `||`, `;` or `|` is split into
  commands. Anything else, including redirects, `$(...)`, backticks, variables, globs, subshells,
  heredocs and functions, is **one unreadable command**. There is no unwrapping of `sudo`, `xargs`,
  `find -exec` or `sh -c`.
- **Decisions**, strictest first:
  - A deny or ask rule applies if any split command matches its prefix.
  - An allow rule applies only if every split command matches some allow rule.
  - An unreadable command is never allowed by a rule.
  - When an unreadable command meets a policy with any command rule for that surface, it is asked
    (refused in `strict`). This holds in every mode, so a deny rule fails closed rather than open.
- **What we promise, in the docs:** "Command rules decide which commands run without asking. They
  match the command as written; a command they cannot read in full is sent for approval, never
  allowed. They are not a security boundary: what a command can touch is the sandbox's job."

`commands.py` shrinks to splitting plain chains plus the summary (escaping kept). The approval
summary still shows each split command, or the whole text when it is unreadable.

## Units (one commit each, test first)

- **SP1, provider contract.**
  - Each runtime declares what it enforces.
  - The manifest is checked against that when the agent is built, so unenforced settings are
    refused rather than ignored.
  - Daytona's host list is refused, matching its IP-only API.
- **SP2, the network is checked from inside on every provider that turns it off.** Modal and
  vercel are added.
- **SP3, filesystem path lists removed.**
  - Old keys are refused with a message saying what to use instead.
  - The setup approval no longer asks about them.
  - The `execute` tool's description says what this provider enforces.
- **SP4, command rules become prefix rules.**
  - The new splitter, and differential tests against real bash for the split.
  - The old matcher fields are refused with the new form shown.
  - Unreadable commands ask.
- **SP5, docs.**
  - The security model page leads with a table: what stops harm, per provider.
  - The policy reference's command section is rewritten short and honest.
  - The defaults are updated.
- **SP6, gate.** The rc7 six-area gate runs on the built wheel, and the security review's S2 area
  is re-run against the new rules.

## Decisions for the maintainer

1. **Remove the filesystem path lists** (`readable_paths`, `writable_paths`, `denied_paths`)
   rather than build enforcement for them. Enforcing them would mean Landlock or per-provider
   mounts: real work, and a later release. Proposed: remove now; revisit with kernel enforcement
   in 0.6.
2. **An unreadable command asks whenever command rules exist, even in `permissive`.**
   `permissive-dev` gets no default command rules, so a dev setup without rules is not prompted.
3. **No unwrapping of wrappers.** `sudo rm -rf x` does not match a deny on `rm`, as in Codex and
   Claude Code. It is a plain chain beginning with `sudo`, so it is either matched by a rule on
   `sudo` or, in interactive and strict modes, falls to the mode's default. Proposed: accept this
   and say so. The sandbox is the boundary, and writing `sudo` into a deny rule is one line.

Since 0.4.4 was never published (PyPI ends at 0.4.3), no released version has command rules. We
can replace them without a compatibility shim.

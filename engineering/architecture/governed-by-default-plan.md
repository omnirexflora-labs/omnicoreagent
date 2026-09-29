# Plan: governed by default, and a headline that says so

The docs landing said "It acts safely" while an agent built from the quickstart
had no policy: `governance_config.enabled` defaulted to `False` (an outside
positioning analysis, 2026-09-29). The maintainer decided: governance is on by
default, with the `permissive-dev` profile, in 0.5.0; and the headline becomes
**"Give your agent real work. Keep control."**, with "Every action checked before
it runs. Every run survives a crash without silently redoing anything. Every
step on the record." The product is a runtime for any agent that does real work;
it is not framed around payments or money (the refund desk is a demo).

## Why `permissive-dev`

It never pauses a first-time user for a person, and it refuses what should not
happen unasked: reading raw secrets, unrestricted host files and network,
installing packages, running shell commands on the host. Local tools, the
workspace, memory, code mode, skills, sub-agents and background runs are allowed.
`interactive-dev` would ask a person before MCP calls, network, sub-agents and
background runs: correct for many deployments, surprising as a default.

## Units

- **G1** · The default: `governance_config.enabled` is `True` with profile
  `permissive-dev`; `enabled: False` turns it off. Tests first: an agent built
  with no governance settings is governed by `permissive-dev`; a quickstart-shaped
  agent (local tools, memory) answers exactly as before; a shell command on the
  host is refused by name; `enabled: False` restores the old behaviour.
- **G2** · Everything that assumed off: the whole test suite, every cookbook
  example and every docs example, run under the new default. Each break is either
  a test that only assumed off (it says so, or passes `enabled: False` where it is
  testing ungoverned behaviour) or a real difference a user would hit (documented
  in the upgrade notes, with the rule to add).
- **G3** · The headline: README, docs landing (hero and the three cards: *It stays
  in bounds*, *It picks up where it stopped*, *It's all on the record*; sandboxes,
  approvals and MCP below as what every runtime needs), `docs.json` and package
  descriptions; the tagline test follows.
- **G4** · The comparison page, from verified primary sources only
  (omnicoreagent-research/competitor-claims-2026-09-29.md): correct the Pydantic
  AI budget cell (`cost_limit` is checked after each response), add the row
  *after a crash, an interrupted tool call is reported, not re-run*, and add Agno.
- **G5** · 0.5.0 notes: changelog, and upgrading from 0.4 (what the default now
  refuses, and the one line to turn it off or allow more).

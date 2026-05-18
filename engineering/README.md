# Engineering Records

This directory contains internal engineering records for OmniCoreAgent.

These files are not public product documentation. Public docs live in `docs/`.
Engineering records exist so maintainers and coding agents can understand the
system before changing it.

Use this directory for:

- architecture records
- specifications
- implementation plans that should be reviewed or preserved
- technical decisions that must stay aligned with code and tests

Do not scatter internal design records across the repository root.

## Layout

```text
engineering/
  architecture/      # System shape, ownership, boundaries, and decisions
  specifications/   # Exact behavior contracts and test expectations
```

Architecture records explain how to think about a subsystem.
Specifications define what that subsystem must do exactly.

Target architecture and specification records:

- `architecture/background-agents.md`
- `architecture/omniserve.md`
- `architecture/telemetry.md`
- `architecture/workspace.md`
- `specifications/background-agents.md`
- `specifications/omniserve.md`
- `specifications/telemetry.md`
- `specifications/workspace.md`

Current implementation contracts during telemetry migration:

- `architecture/events.md`
- `specifications/events.md`

The event records describe the current legacy runtime event and SSE behavior.
They are kept so maintainers can migrate safely. They are not the future
telemetry architecture.

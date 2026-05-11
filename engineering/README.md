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

Current records:

- `architecture/background-agents.md`
- `architecture/omniserve.md`
- `architecture/workspace.md`
- `specifications/background-agents.md`
- `specifications/workspace.md`

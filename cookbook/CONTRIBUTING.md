# Contributing to the Cookbook

Thank you for contributing to the OmniCoreAgent Cookbook! This guide explains how to add new recipes.

## 📝 Recipe Format

Every recipe should follow this template:

```markdown
# [Recipe Title]

> [One-line description of what this recipe does]

## Prerequisites

- OmniCoreAgent installed (`pip install omnicoreagent`)
- API key configured in `.env`
- [Any additional requirements]

## The Problem

[Describe the task or challenge this recipe solves in 2-3 sentences]

## The Solution

\`\`\`python
# Complete, runnable code example
# Include all imports
# Use clear variable names
\`\`\`

## How It Works

1. **Step one**: [Explanation]
2. **Step two**: [Explanation]
3. **Step three**: [Explanation]

## Variations

### [Variation Name]
[Brief description and code snippet if applicable]

## Related Recipes

- [Link to related recipe](path/to/recipe.md)
```

## 📂 Directory Structure

Place your recipe in the appropriate category:

| Directory | Category |
|-----------|----------|
| `01-getting-started/` | First-time setup and basic usage |
| `02-tools/` | Local tools and MCP integrations |
| `03-memory-system/` | Memory backends and persistence |
| `04-multi-agent/` | Sub-agents and workflow orchestration |
| `05-background-agents/` | Scheduled and autonomous agents |
| `06-production/` | Metrics, guardrails, and deployment |

## ✅ Quality Checklist

Before submitting:

- [ ] Code runs without errors
- [ ] All imports are included
- [ ] Prerequisites are clearly listed
- [ ] Explanation is clear and concise
- [ ] Recipe follows the template format

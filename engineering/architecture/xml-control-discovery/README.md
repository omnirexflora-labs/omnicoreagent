# XML control discovery package

Investigated local **main**, commit **60da57a6dacd3f7796fffd9d198c0a7420aa6aad**, in a separate worktree on 2026-09-13. Discovery only; production source is unchanged.

**Updated scope:** retain normal OmniCoreAgent runs, its deep-agent capabilities, and background execution. Completely delete RouterAgent, ParallelAgent and SequentialAgent, their exports, dedicated examples/docs and references. **No fallback, compatibility aliases, wrappers or native workflow replacements.** M7 is now a deletion checklist, not a workflow migration. Source descriptions of these classes remain only as removal evidence.

1. [A. Architecture walkthrough](architecture.md) — runtime assembly, full invocation/observation cycle, optional prompts, persistence, delegation/workflows, public/streaming boundaries and representative scenarios.
2. [B. XML dependency inventory](inventory.md) — 44 stable dependency IDs, producers/consumers, activation, removal impact, evidence and implications; tag-family closure table.
3. [C. Migration dependency map](migration-map.md) — 9 retained migration units plus the M7 complete-removal checklist, independent adapters, compatibility decisions and verification gates.
4. [D. Coverage and unresolved questions](coverage.md) — examined paths, test evidence, diagnostics, documentation disagreements, existing defects and 12 bounded follow-ups.

Evidence: [source links](evidence/source-index.md), [tag catalog](evidence/tag-catalog.md), [tracked-source XML search](evidence/xml-tags.txt), [callers](evidence/callers.txt), [public consumers](evidence/public-consumers.txt), [test commands](evidence/test-commands.sh), [environment](evidence/environment.txt), [offline diagnostic results](evidence/diagnostics.jsonl).

**Verification:** 766 existing tests passed, 2 live memory-backend tests skipped. Offline diagnostics additionally establish mixed-response precedence, actual history reload failure, singleton-spawn argument corruption, MCP result loss and call-group splitting. Passing unit tests do not resolve those combined-path gaps.

**Review focus:** Migrate XML control for ordinary tools and retained delegation. Delete the second router decision parser with the retired workflow package. Active context and stored/reconstructed history use different representations. Native tool calling therefore requires coordinated prompt, provider, loop, result and persistence adaptations. Existing SSE streams lifecycle telemetry; provider text and response interfaces still buffer complete responses.

No XML removal, native control implementation, streaming implementation or unrelated fixes were made. The package is ready for discovery review; provider/retained-runtime/session compatibility decisions remain explicitly open; workflow removal without fallback is settled.

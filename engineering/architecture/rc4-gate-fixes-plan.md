# Fixes from the 0.5.0rc4 release-candidate gate

The rc4 gate ran the same six areas against the built wheel. Each unit is
test-first and one commit; 0.5.0 is built and gated again after these land,
and published only when a gate finds nothing to fix.

## Units

- U1 Parallel `execute` calls each see the whole workspace: the bridge copies
  one at a time. A second copy-in skipped files the first was still
  uploading, and its command ran on a partial workspace (area D).
- U2 `generate-dockerfile` writes a `.dockerignore` keeping `.env` and the
  workspace out of the image (it copies the whole build context; the .env
  holding LLM_API_KEY went in), and names an existing one that does not
  exclude `.env` (area D, security).


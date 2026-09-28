# Plan: what an outside review of the docs found

An outside reviewer read the landing page, the page index, the quickstart and the
comparison page. The main risk they named: the docs' breadth hides the one thing
that makes OmniCoreAgent different, governed, durable and evidenced runs. They
listed six fixes; all six are accepted. Each unit below is one commit with its
test first.

## Units

### R1 · Short paths redirect instead of 404

`/docs/quickstart`, `/quickstart` and `/docs/install` return 404 on the published
site: `docs.json` has no `redirects`. People guess short paths, and links in posts
use them.

- Add Mintlify `redirects` for the short forms of the pages people look for first:
  quickstart, installation, tour, comparison, policies, approvals, budgets,
  durable runs, read a run, harbor.
- Test (`test_docs_code.py`): every redirect's destination is a page in the
  navigation, no source is itself a page, and the quickstart and installation
  short paths are present.

### R2 · The tagline says one thing

The README and `docs/index.mdx` open with a 20-word line ("The open Python agent
runtime and harness, with an SDK, for AI applications that have to hold up in
production"). It becomes **"The governed runtime for Python agents you can let
act."**, the line the launch film ends on. The second line (governed, sandboxed,
durable, budgeted, evidenced) stays: it is the list the first line promises.

- Test (`test_docs_claims.py`): the README, the docs landing page and
  `docs.json`'s description carry the same tagline.

### R3 · The navigation leads with what is different

Today the order is Get Started, **Build** (memory, RAG, sub-agents, …), then
**Make It Safe**. The pages that make OmniCoreAgent different come second, after
the ones every framework has. New order: Get Started, Make It Safe, Run It,
See and Improve, Build, How It Works, Reference. No page moves between groups
and none is removed, so no link breaks.

- Test: the first group after Get Started is Make It Safe, and the group set and
  page set are unchanged.

### R4 · The Python version trap is the first thing on the install page, and fails loudly

On Python 3.10 or 3.11, pip does not fail: it installs 0.3.9, which cannot build
an agent (its package shipped without `omnicoreagent.core.workspace`). The docs
say so, in a troubleshooting accordion near the bottom.

- The installation page opens with a warning; the quickstart keeps its line.
- A **guard release, 0.3.10**, source only, `requires-python = ">=3.10,<3.12"`.
  pip on 3.10/3.11 picks it over 0.3.9 and its build stops with one message:
  OmniCoreAgent needs Python 3.12 or later, and how to get it. Its version is
  below 0.4, so PyPI's "latest" and the version badge stay on the real release, and
  no future release has to know about it. It lives in `packaging/python-guard/`.
- Test (`test_python_guard.py`): building the guard fails with the message; its
  Python range and the package's do not overlap; its version sorts above 0.3.9 and
  below every 0.4 release.

### R5 · The durability contract, stated exactly

"Never repeats a call silently" is the claim; a reviewer asks what happens when the
process dies halfway through a tool with side effects. `durable-runs.mdx` gets a
**Guarantees** section: per tool-call state, whether a recovered run re-runs it
(at-most-once for calls not marked idempotent, at-least-once for idempotent ones),
what resume replays and what it never re-sends, the lease that keeps two processes
off one run, and how usage is counted across a crash. Every sentence names the code
it comes from; no guarantee is stated that a test does not hold.

- Tests: any guarantee in the section not already proved by a test gets one.

### R6 · One real number on the landing page

The landing page claims evidence; it shows none. We run OmniCoreAgent on a public
Terminal-Bench subset through Harbor, publish the run's command, model, reward,
cost and steps in `engineering/validation/`, and put that one result on the
landing page with a link to the full record. Only a public run we can reproduce is
quoted, never numbers from any other work. This unit needs its own decisions
(model, task subset, spend cap) and ships in its own PR.

## Order

R1, R2, R3, R4 (docs part) and R5 ship as one docs PR. The guard release (R4) is
built here and uploaded by the maintainer. R6 follows.

# AGIWiki development constraints

Follow any environment-routing instructions inherited from the parent workspace.

## Product boundary

- AGIWiki is a local-first personal factual-memory tool.
- The only primary flow is editable JSON Workspace -> immutable Memory Pack -> Home -> CLI/stdio MCP.
- Do not add a website, Wiki renderer, HTTP server, community, federation, public moderation, enterprise tenancy, dynamic conversation memory, or hosted service to the core repository.
- The core does not call an LLM. A user-selected Agent writes Workspace JSON; AGIWiki validates, builds, installs, indexes, and serves it.

## Runtime

- Use a Python 3.12+ project-isolated interpreter for tests and lint.
- Run from the checkout with `PYTHONPATH=src` unless installed in an isolated environment.
- Do not use unqualified `python` or `pip`.
- Core is CPU-only and network-free.
- MCP is an optional dependency and must be imported lazily.

## Data invariants

- Workspace JSON is editable source; installed Pack JSON is immutable.
- Pack identity derives only from canonical portable JSON, never timestamps, absolute paths, SQLite bytes, or row IDs.
- Search indexes are disposable Home cache and are never canonical Pack content.
- Pack and MCP output must not contain AGIWiki-injected paths, common private
  source paths, credential-bearing URIs, raw prompts, or hidden source text.
- Project and request scope can only narrow the locally activated Pack set.
- No-match is a normal result; never synthesize a memory.
- Procedure and troubleshooting memories preserve prerequisites, warnings, verification, and failure guidance.

## Change discipline

- Use closed JSON Schemas and reject duplicate JSON keys.
- Schema changes require contract tests and an explicit version bump.
- New commands must stay within the personal memory flow and keep the MCP surface at two read tools.
- Prefer standard-library SQLite and filesystem primitives over new services.
- Do not copy legacy AGIPedia directories wholesale; migrate only reviewed primitives and record provenance in `ORIGIN.md`.

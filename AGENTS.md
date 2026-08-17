# AGIWiki development constraints

Follow any environment-routing instructions inherited from the parent workspace.

## Maintenance status

- AGIWiki reached its final Alpha at `v0.1.0-alpha.1` and is now an experimental reference
  implementation in maintenance mode.
- Preserve the existing code, contracts, tests, and evaluation artifacts. Do not restart product
  expansion or reinterpret maintenance as a new roadmap.
- Allowed changes are critical security or data-integrity fixes, reproducibility repairs,
  dependency compatibility, test maintenance, and documentation corrections.
- Do not add or expand Adaptive Memory, cloud or hosted services, knowledge-graph engines, or
  provider-specific adapters. The existing experimental integration is retained as historical
  evidence only.

## Product boundary

- AGIWiki is a local-first personal Agent memory tool with two deliberately separate planes.
- Artifact Memory keeps the shipped editable JSON Workspace -> immutable Memory Pack -> Home -> CLI/stdio MCP flow.
- The existing experimental Adaptive Memory ledger is frozen. Preserve its separation from Memory Packs, but do not add classes, automation, writer surfaces, or new lifecycle features.
- Do not add a website, Wiki renderer, HTTP server, community, federation, public moderation, enterprise tenancy, or hosted service to the core repository.
- The core does not call an LLM. A user-selected Agent may prepare Workspace JSON or explicit Adaptive Memory candidates; AGIWiki validates and stores them under user-controlled policy.
- The Authoring Controller may plan bounded local Source batches and record budget/progress, but it must remain provider-neutral, must not copy Source text into Pack state, and must not call a model.
- The existing MCP remains read-only. Any future Agent write surface must be separate, opt-in, and disabled by default.

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
- Behavioral changes to the authoring Skill require a new `prompt_set_id`; the plan's declared ID is provenance metadata, not proof of an external Agent's hidden prompt.
- New commands must stay within the personal memory flow and keep the MCP surface at two read tools.
- Prefer standard-library SQLite and filesystem primitives over new services.
- Do not copy legacy AGIPedia directories wholesale; migrate only reviewed primitives and record provenance in `ORIGIN.md`.

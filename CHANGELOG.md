# Changelog

All notable changes will be documented here. The project follows semantic versioning after
public releases; pre-1.0 contracts may still change with an explicit migration note.

## Unreleased

The project is in maintenance mode. Only critical fixes and documentation corrections are planned.

- Pin the development lint tool to the version used to verify the final Alpha, so maintenance CI
  does not change merely because new lint rules are released.

## 0.1.0a1 - 2026-08-17

Tag: `v0.1.0-alpha.1`

This is the final Alpha and the complete experimental reference implementation. Active product
development has stopped; existing code, contracts, tests, and evaluation artifacts remain
available under Apache-2.0.

### Added

- Side-effect-free Windows/WSL integration plans for Hermes, Claude Code, and Codex.
- Layered `agiwiki.doctor.v2` readiness for Core, Home, Packs, MCP, the WSL bridge, and explicitly
  untested model-provider connectivity.
- Local Workspace → immutable Memory Pack → Home → CLI/stdio MCP lifecycle.
- Source-grounded Fact, Concept, Procedure, and Troubleshooting Entries.
- Agent authoring and read-only memory Skills distributed in the source repository.
- CI, dependency updates, secret scanning, security reporting, and release checks.
- An opt-in experimental Adaptive Memory ledger with closed `profile`/`episode`
  contracts and explicit `init`, `remember`, `get`, `list`, `search`, `correct`,
  and confirmed `forget` CLI operations.
- A provider-neutral Authoring Controller with immutable source plans, bounded batch claims,
  append-only results, measured Token budgets, resumable progress, and idempotent budget
  extensions. It does not call a model or copy source text into a Pack.
- Author-plan v2 binding for credential-free canonical Source URIs, with read compatibility for
  immutable v1 plans.
- Authoring prompt set v2, batch-local evidence binding, drift detection, reusable result seeds,
  and detailed progress receipts for reliable Agent-driven compilation.
- Authoring prompt set v3 with digest-sealed batch-result v2 records and a single-Entry,
  append-only `author amend` transition for review corrections and crash-safe replay.
- Authoring prompt set v4 with source-complete kind selection, no kind quotas, and an explicit
  prohibition on inventing operational or troubleshooting fields to satisfy a richer JSON shape.
- A CLI Pack-build preflight that blocks incomplete Author Plans, changed Sources, and recorded
  Entry drift by default; emergency overrides remain explicit and visible in the build receipt.
- Discoverable read, author, and critical-review Skills in both source and wheel installations
  without automatic Agent configuration changes.
- A documented 46-page RFC 9112 authoring and retrieval pilot with bounded-batch, Pack-size,
  source-support, positive-query, and no-match results.
- Machine-replayable Pack retrieval and deterministic lexical page-Fragment evaluation tools,
  with frozen task/evidence digests and explicit limits on what the comparison proves.
- A source-only research tool for replaying externally frozen semantic or hybrid retrieval
  contexts, with closed digest-bound inputs, optional usage receipts, and no provider runtime in
  AGIWiki core or wheel commands.
- An English public onboarding surface, including a consumer-focused getting-started guide and an
  English minimal Workspace that can be validated, built, installed, searched, and retrieved.
- A seven-case verbatim-source prompt-v4 regression that checks source fidelity, Entry
  normalization, kind selection, skip decisions, required-field support, and support levels.
- A default-off, source-only DeepSeek Harness Cordis overlay that connects the existing read-only
  stdio MCP through the generic Harness MCP bridge without adding a runtime dependency.
- An opt-in `agiwiki-critical-review` Skill that stress-tests technical proposals, classifies
  overlap with prior ideas, and defines bounded falsification tests without gaining memory write
  authority.
- A periodic-memory review contract that separates deterministic daily checks, optional weekly
  Agent proposals, human approval, and explicit memory mutations.
- Adaptive schema v5 with explicit v1/v2/v3/v4 migration, hashed and revocable local capabilities,
  separate propose/approve/apply permissions, non-applying authenticated decisions, and atomic
  content-free application receipts for expiry deletion and exact-duplicate reduction.
- A closed, content-free `adaptive review-due` status for credential-free daily or weekly external
  reminders without scheduled proposal creation or memory mutation.

### Fixed

- Fallback search now requires one Entry to cover most informative query terms instead of
  returning a candidate that only shares one generic domain word.

### Security

- Closed canonical Pack bytes, relation-closure checks, credential-bearing URI rejection,
  private-path rejection, no-clobber installation, and fail-closed Pack reads.
- Exact Adaptive scope checks, per-read content-digest verification, explicit database-version
  recognition, expiring-memory filtering, append-only correction, and confirmed whole-lineage
  content removal. Write operation IDs are bound transactionally, and forget clears
  content-derived operation digests.
- Recorded Authoring Entries now bind normalized content digests, so same-locator edits fail
  closed; status labels legacy v1 results that cannot provide retrospective content sealing.

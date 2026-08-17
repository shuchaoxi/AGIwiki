# AGIWiki project retrospective

Date: 2026-08-17
Final Alpha: `v0.1.0-alpha.1`
Status: Experimental reference implementation in maintenance mode

## Original hypothesis

AGIWiki began with the hypothesis that personal AI agents would benefit from a provider-neutral
factual-memory layer. Source documents would be compiled into structured facts, concepts,
procedures, and troubleshooting entries, distributed as immutable Memory Packs, and reused by
multiple agents through a local CLI or MCP interface.

The design attempted to separate editable source material from portable releases and disposable
indexes. It also explored explicit adaptive memory, bounded authoring, source locators, content-
addressed identity, fail-closed verification, review receipts, and reproducible retrieval studies.

## What was implemented

The final Alpha preserves a complete local reference lifecycle:

- editable JSON Workspaces and closed contracts;
- source-grounded Entry shapes for facts, concepts, procedures, and troubleshooting;
- deterministic, immutable, content-addressed Memory Packs;
- private Home installation, exact activation, verification, and rebuildable FTS indexes;
- CLI and a fixed read-only stdio MCP surface;
- provider-neutral authoring and critical-review Skills;
- a resumable, budget-aware Authoring Controller;
- an opt-in local Adaptive Memory experiment with explicit lifecycle controls;
- Windows/WSL diagnostics and inert integration plans;
- frozen authoring and retrieval evaluation artifacts.

The code and tests demonstrate the engineering design. They do not establish that generated
Entries are true, that a Memory Pack is superior to strong RAG, or that a broad market exists.

## Evidence and limitations

Internal studies showed that deterministic artifacts, exact source locators, no-match behavior,
and source-faithful authoring checks can be implemented and tested. A seven-case verbatim-source
regression passed its narrow kind-selection and required-field gates. Other studies documented the
cost and limitations of authoring, lexical retrieval, transformed operational guidance, and the
absence of independent human validation or a strong provider-run semantic baseline.

The project therefore did not prove its central product hypothesis. In particular:

- compiling prose into Entries is a lossy transformation and may omit or strengthen qualifiers;
- most personal and general-document use cases are already served by RAG, long context, or mature
  memory runtimes;
- strict versioning, audit, and rollback needs are comparatively uncommon and often belong to
  organizations that can purchase or extend existing enterprise knowledge systems;
- authoring and semantic review add substantial cost before a Pack can be trusted;
- immutable Pack formats and schemas are straightforward for larger platforms to reproduce;
- the remaining differentiation depends on adoption, external evaluation, and a professional Pack
  ecosystem that this project has not established.

## Decision

AGIWiki will not continue as an actively developed personal-memory product. The repository remains
public under Apache-2.0 as a reference implementation and research record. Existing functionality
is retained so that its contracts, tests, security work, and negative findings remain inspectable.

Active development is stopped for:

- Adaptive Memory expansion;
- cloud sync, hosted services, or multi-tenant infrastructure;
- automatic or canonical knowledge graphs;
- new third-party memory-runtime adapters;
- a 1.0 product roadmap.

Maintenance is limited to critical security and data-integrity fixes, reproducibility repairs,
dependency compatibility, and documentation corrections. Existing experimental integrations are
historical artifacts, not compatibility commitments.

## Conditions for reconsideration

The decision should be reconsidered only if independent users provide concrete evidence that a
portable, immutable knowledge release solves a repeated problem that strong RAG does not solve
adequately. Useful evidence would include independently authored Packs, repeated cross-agent use,
lower unsupported-action or version-selection error rates, and a maintenance cost that can be
amortized over real tasks.

Until then, AGIWiki should be read as a completed experiment: a demonstration of one possible
artifact-oriented memory architecture and an honest record of why the product thesis was not
continued.

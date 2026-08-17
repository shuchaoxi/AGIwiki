# Authoring Controller

Status: local compilation control plane for Artifact Memory. It is not part of the model runtime
and never enters a Memory Pack.

The `agiwiki-author-memory` Skill tells a user-selected agent how to write Sources and Entries. The
Authoring Controller turns a large authorized source into bounded, resumable batches. It does not
call a model, store a copy of source text, or replace Workspace Schema and quality validation.

```text
authorized local source
        │
        ├─ stable digest + unit map
        ▼
immutable author plan
        │
        ├─ next batch ──> chosen agent + versioned authoring Skill
        │                         │
        │                         └─ writes Source and Entry JSON
        ├─ record validated Entry digests and measured usage
        ├─ amend one reviewed Entry through an append-only transition
        └─ status / resume / explicit budget extension
                                  │
                                  ▼
                         workspace validate → Pack
```

## Boundaries

- A plan accepts one explicitly selected local regular file and one initialized Workspace. It does
  not scan a parent directory.
- Private control state lives in `Workspace/.agiwiki-author/`; it can contain a local source path
  and never enters a Pack, MCP result, or portable Workspace digest.
- A v2 plan can bind one credential-free `--canonical-uri` to the plan and Source. Immutable v1
  plans remain readable and imply `canonical_uri=null`.
- Plans, batch claims, stored results, amendments, and budget extensions are closed JSON.
- `next` returns only a locator, budget, identities, and a result seed. It never returns source
  text. The selected agent must read the authorized source within its own permission boundary.
- The Controller validates structure, batch attribution, and digest consistency. It cannot decide
  whether a claim is true.
- The declared `prompt_set_id` records provenance. It is not cryptographic proof of the external
  agent's hidden prompt.

## Batch model

The first release supports:

- UTF-8 Markdown and text, divided by line range;
- PDF, divided by caller-supplied page count;
- another regular file as one file batch.

Only one claim can remain outstanding. Repeating `author next` after interruption returns that
same claim. A recorded result uses its batch file as an idempotency boundary: an identical result
replays; different content conflicts.

New result records use `agiwiki.author-batch-result.v2` and seal every attributed Entry's canonical
digest. Status reports distinguish sealed, legacy-bridged, and legacy-unsealed results. A v1 result
can still be read but cannot retrospectively prove the Entry bytes present when it was recorded.

## Build preflight

The CLI `pack build` checks every local Author Plan before producing a Pack. It blocks:

- incomplete or outstanding batches;
- changed source bytes;
- changed or missing recorded Entries.

A Workspace with no Author Plan remains buildable for manual workflows. An emergency export can
use `--allow-incomplete-authoring`, but the receipt retains blockers and an override marker.
Preflight proves control-ledger consistency only and always reports
`semantic_review=NOT_CHECKED`. A separate source comparison is still required before claiming
semantic accuracy.

## Controlled amendment

Do not overwrite an Entry after its batch is recorded. Inspect its public binding state first:

```bash
agiwiki author entry-status PLAN_ID \
  --workspace TARGET \
  --entry-id entry_<32-hex>
```

The result contains IDs, current and effective digests, binding strength, and amendment count; it
does not include Entry content or local paths.

For `sealed` and `legacy_bridged`, use `effective_entry_digest` as the expected old digest. For
`legacy_unsealed`, the first correction can use `current_entry_digest` only as an explicitly weaker
operator-asserted baseline:

```bash
agiwiki author amend PLAN_ID \
  --workspace TARGET \
  --entry-id entry_<32-hex> \
  --input revised-entry.json \
  --expect-old-digest sha256:<64-hex> \
  --operation-id review-fix-0001
```

The Controller derives the original batch, validates the same-ID replacement, appends a digest
transition receipt, and then atomically replaces the Workspace file. Replaying the same operation
after interruption converges; reusing the operation ID with different content fails closed.

## Budget behavior

Plans can limit estimated total Tokens and Entry count. Recorded usage must come from a provider or
agent receipt. When no trustworthy measurement exists, use `measurement_source=unavailable` with
both Token values set to zero; do not invent precision.

When the next estimated batch exceeds the remaining budget, the Controller stops without losing
progress. A user can approve a budget extension with a unique operation ID. For a long book, first
process a representative subset, review quality and cost, and only then continue.

## Why this is not one giant prompt

The shipped `agiwiki-author-memory.v4` workflow separates four decisions:

1. select knowledge worth reusing;
2. choose a kind from the question the exact source can answer;
3. preserve qualifiers, uncertainty, evidence strength, and exact locators;
4. validate closed JSON, references, and minimum information quality.

Ordinary prose stays in the source or RAG layer. A corpus may legitimately produce no Entries of a
particular kind. The author must never create operations or troubleshooting content merely to fill
a richer JSON shape.

Any behavioral change to selection, extraction, verification, or citation requires a new prompt
set ID. Existing plans retain their original prompt provenance and remain resumable.

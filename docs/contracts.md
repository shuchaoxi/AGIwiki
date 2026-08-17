# Data contracts

All portable documents are closed JSON objects. Unknown fields, duplicate
keys, non-finite numbers, unsafe URIs, common private machine paths, and
unresolved cross-references are rejected. Operational paths such as `/etc/hosts`
may still be factual content; authors must distinguish them from source-file
locations and secrets.

## Workspace

Create an empty authoring tree with:

```bash
agiwiki workspace init TARGET --slug SLUG --title TITLE --locale zh-CN
```

The command is no-clobber and creates private `sources/` and `entries/` directories. The
Workspace becomes complete only after it contains at least one valid Source and Entry.

Large-source compilation uses the local `agiwiki.author-plan.v2` control contract. It binds the
credential-free canonical Source URI when one is supplied. Readers continue to accept frozen v1
plans, whose Source URI is implicitly `null`. The plan,
batch claims, results, and budget extensions live under `.agiwiki-author/` and never enter the
portable Workspace snapshot or Pack. A recorded line/page Entry must cite a decimal position or
inclusive range wholly inside its claimed batch; later binding drift stops further authoring.
See [`authoring-controller.md`](authoring-controller.md).

`workspace validate` also applies `agiwiki.entry-quality.v1`. This is a deliberately small
information-completeness gate: summaries, fact statements, definitions, goals, diagnostic
signals, actions, and verification text must contain enough letters or numbers to be usable,
and every Entry needs at least two retrieval keywords. Only letters and numbers count
toward the minimum, so punctuation padding is rejected. It blocks one-word placeholders
but does not claim to prove truth. Accuracy still depends on inspecting the exact Source edition,
digest, and locator and preserving uncertainty during authoring.

`agiwiki.json` identifies one editable knowledge collection and its release
version. Filenames do not determine identity.

`find_memory` distinguishes retrieval from delivery: `found` means at least one candidate
matched, while `returned` means at least one candidate fit the current Token budget.
`truncated_by_budget` prevents a real match from being misreported as an empty knowledge base.

## Source

A Source records a stable `source_id`, media kind, title, edition, content
digest, optional portable canonical URI, and language. Original document bytes
are not copied into a Pack in version 0.1.

## Entry

An Entry has a stable `entry_id` and one of four kinds. The kind describes the reusable question
that the cited material can answer, not its topic or importance:

- `fact`: a bounded rule, value, compatibility condition, or constraint plus qualifiers;
- `concept`: a definition or model, its boundaries, details, examples, and misconceptions;
- `procedure`: source-supported actions or decision steps with a goal, prerequisites, expected
  results, warnings, verification, and failure guidance;
- `troubleshooting`: a source-supported observable symptom, diagnostics, matching fixes,
  escalation, warnings, and verification.

Authors do not balance these kinds by quota. If the source does not support every required
operational field, it remains a `fact` or `concept`, or stays in the original Source/RAG layer.
`indirect` support does not permit invented actions, diagnostics, or fixes.

Every Entry cites at least one Source through a portable locator. The derived
`entry_version_id` changes when the canonical Entry changes; the stable
`entry_id` does not.

## Memory Pack

The Pack manifest binds the exact Workspace, Source set, Entry revisions,
quality-policy version, output files, and their digests. `pack_id` derives from canonical portable
semantics. Timestamps, file locations, SQLite bytes, and local activation state
never enter its identity. Version 2 also requires every JSON file's bytes to be the canonical
UTF-8 encoding plus one trailing newline; reformatting creates an invalid artifact rather than
another byte representation of the same Pack.

The schemas live in [`src/agiwiki/schemas`](../src/agiwiki/schemas). The
[`examples/minimal-memory`](../examples/minimal-memory) Workspace contains one
Source and one Entry of each kind.

## Experimental Adaptive Memory contract

The four Entry kinds above remain the content shapes for source-derived Artifact Memory. Future
personal and conversational memory must not expand this single `kind` enum until it mixes
unrelated provenance and retention rules. The experimental local ledger instead uses separate,
orthogonal dimensions:

- class: `profile` or `episode`; `affective` is not accepted;
- scope: user, agent, run, or workspace;
- lifecycle: active, superseded, or deleted, with validity and expiry as separate time fields;
- provenance: explicit human input, authorized interaction, task result, or import;
- sensitivity: private or sensitive;
- retention: durable or expiring.

The closed schemas are `adaptive-write`, `adaptive-correction`, `adaptive-record`,
`adaptive-review-proposal`, `adaptive-review-decision`, and
`adaptive-review-decision-receipt` v1 and v2, `adaptive-review-application`, and
`adaptive-review-application-receipt`, and `adaptive-review-due`. The owner
must first run `agiwiki adaptive init`; other Adaptive commands refuse a missing or unsupported
ledger rather than creating one. Running that explicit command upgrades a recognized v1, v2, v3,
or v4 ledger to schema v5 by adding the operation journal when needed, immutable review tables,
the local capability table, and the append-only application table;
ordinary reads never migrate it. `correct` creates a new revision and supersedes the exact active
record. Omitting `valid_to` inherits the prior value; an explicit JSON `null` removes it. Normal
reads hide expired records, while
`list --include-expired` exposes active expired records for owner management.

`remember`, `correct`, and `forget` record a caller-supplied or generated `operation_id`. The same
ID and exact request replay the prior result; a different operation type, scope, target, or request
digest is rejected. Confirmed forgetting clears content-derived request digests for the forgotten
lineage, so those old operation IDs cannot recreate or reveal the removed request. A generated ID
is returned to the caller, but safe retry after a lost response requires an explicit retained ID.

`forget` requires explicit confirmation and clears the content, content digest, and provenance of
every revision in the selected lineage, retaining content-free tombstone metadata. This is
application-level removal, not a promise of forensic erasure from filesystem snapshots, backups,
storage media, or copies already exported elsewhere. Records are checked against their content
digest when read; the schemas do not recognize arbitrary credentials or PII inside valid prose,
so the caller remains responsible for what it submits.

`adaptive review-plan` is a content-free, exact-scope deterministic report. `review-create`
persists that proposal with stable candidate, snapshot, and proposal digests. `review-decide`
requires explicit confirmation, exact scope, complete candidate coverage, a principal with
`review_approve`, and one append-only v2 decision receipt. `review-create` separately requires
`review_propose`. `review-apply` requires a third `review_apply` capability, complete coverage of
all accepted candidates, exact decision/proposal binding, a still-current candidate snapshot, and
explicit confirmation. It can only apply expiration `forget` or exact-duplicate reduction; a
correction still needs explicit new content through `adaptive correct`. Every application is one
atomic transaction with a content-free append-only receipt and request-bound internal operations.
Capability files contain high-entropy tokens, SQLite stores only their hashes, and revocation
blocks future use. Enrollment trusts the local OS owner and does not establish a remote identity.
There is no writer MCP, automatic conversation capture, Unified Recall, import/export, or
affective memory yet.

`adaptive review-due` is a closed, content-free read projection. It never creates a proposal or
loads a capability. `never_reviewed` is immediately due; an undecided latest proposal reports
`pending_decision` and suppresses duplicate scheduling; a decided review derives the next daily
or weekly due time from `last_decision_at`. Recommendations may include an unapplied supported
action, a declared manual correction, and a new due proposal at the same time. They are reminders,
not authorization, and a valid low-scoring or due status still exits successfully.

See [`memory-strategy.md`](memory-strategy.md) before proposing contract changes.

## Operator diagnostics and integration plans

`agiwiki.doctor.v2` is a read-only operator report rather than portable memory. It reports Core,
Home, Pack, MCP, Windows/WSL, and external-provider layers separately. Its `READY` state means that
the local Artifact Memory and requested MCP bridge are ready; provider authentication and model
network access always remain `NOT_CHECKED`.

`agiwiki.integration-plan.v1` is an inert argv plan for Windows clients launching a WSL stdio MCP.
It supports Hermes, Claude Code, and Codex without changing their configuration. The plan contains
local paths and is not part of a Memory Pack. Executing its `install.argv` is an explicit operator
action; `remove.argv` is the corresponding rollback.

# Security model

AGIWiki assumes the local OS account controls its own files. It protects the
user from accidental path escape, malformed or ambiguous JSON, Pack tampering,
and an Agent silently broadening the active memory scope. It does not protect
against a malicious administrator or a compromised user account.

The first release enforces these boundaries:

- bounded UTF-8 JSON with duplicate-key and unknown-field rejection;
- no symlink traversal in Workspace, Pack, Home, or project markers;
- reject common private machine paths and credential-bearing URIs from portable fields;
- canonical content digests and a closed Pack file set;
- atomic, no-clobber Pack installation and Linux owner-only Home permissions;
- exact Pack activation and scope that can only narrow active memory;
- Pack verification before activation, indexing, search, and exact reads;
- local stdio MCP with two read tools and no management operation;
- normal `found: false` results instead of synthesized knowledge.

The optional Authoring Controller stores an explicitly selected Source's local path in the
private Workspace `.agiwiki-author/` control directory so an authorized Agent can resume an exact
batch. That path is operator-only state: it is excluded from Pack identity, Pack files, indexes,
and MCP responses. Batch delivery through the CLI deliberately reveals the authorized path to the
local caller that requested the plan.

New batch records seal each normalized Entry digest in one
`agiwiki.author-batch-result.v2` file. Later same-locator content drift therefore stops
`author next` and `author record`. A reviewed change uses the single-Entry `author amend`
transition: an append-only receipt binds the owning batch result, predecessor amendment,
and old/new Entry digests before the Workspace file is atomically replaced. A crash between
the receipt and replacement fails closed and the same operation can be replayed. The command
cannot change the Entry ID, choose another batch, add an Entry, or delete one.

Legacy v1 batch results contain IDs and locators but no Entry content digest. Status reports
these as `legacy_unsealed`; a first amendment can only establish an explicitly operator-asserted
baseline at amendment time. It cannot retroactively prove the bytes present when v1 was recorded.
These digests detect local content drift; they do not prove factual correctness, reviewer identity,
or resistance to a malicious owner who can rewrite both data and receipts.

Original documents remain the factual source. A Memory Entry is a derived,
versioned aid. For dangerous actions, current state and the original material
must still be checked and human confirmation may still be necessary.

AGIWiki does not attempt to recognize every possible secret embedded in prose.
Workspace authors and the Agent that prepares JSON must not copy passwords,
tokens, private keys, raw confidential logs, or unnecessary original text into
an Entry.

The complete permission and lifecycle path is currently validated on Linux. Windows path
routing is experimental, and POSIX `chmod` calls are not a substitute for Windows ACL review.

## Experimental Adaptive Memory boundary

The opt-in Phase 1 ledger implements a deliberately narrower local boundary:

- it is created only by explicit `agiwiki adaptive init`; ordinary reads do not create a ledger;
- every read and mutation requires an exact user, agent, run, or workspace scope;
- persisted non-deleted rows are checked against their content digest before delivery or mutation;
- an existing database is recognized by an exact schema version before initialization reuses it;
- expiry is filtered from normal reads and can be shown explicitly for owner management;
- correction appends a superseding revision instead of editing the prior content in place;
- forget requires an explicit confirmation flag and clears every revision in the lineage;
- `profile` and `episode` are accepted; affective state is not;
- the current MCP remains read-only and does not expose these write operations.

Closed fields, length limits, provenance shape, private file permissions, and control-character
checks are structural defenses. They do **not** reliably identify arbitrary passwords, tokens,
private keys, personal data, prompt injection, or false statements embedded in otherwise valid
prose. The person or local tool submitting a memory remains responsible for excluding content that
should not be persisted. AGIWiki does not claim automated PII or secret redaction.

Application-level forget cannot promise forensic erasure from filesystem snapshots, backups,
storage media, SQLite remnants outside its control, or copies already exported elsewhere. The
write operations bind a client operation ID to the exact request, scope, target, and result in the
same transaction. A caller can safely retry only when it retained that explicit ID. Confirmed
forget clears content-derived operation digests for the lineage; an erased operation binding is not
replayable. Automatic capture, an Agent writer, quarantine workflow, affective memory,
import/export, and Unified Recall remain behind future gates. A retrieval score is not a truth or
trust score.
Full requirements and staged gates are defined in [`memory-strategy.md`](memory-strategy.md).

A scheduled memory review has no implicit write authority. It may freeze a scoped snapshot and
persist a content-free proposal and append-only decision receipt, but correcting or forgetting
memory remains an explicit operation. Schema v5 authenticates propose, approve, and manual apply
operations with separate high-entropy local capabilities, stores only token hashes, and supports
revocation. Apply is limited to sealed, still-current expiry deletion and exact-duplicate
reduction; it is not a scheduler or general Agent writer. The system still trusts the local OS
owner for enrollment and has no remote identity or secret broker. Remaining unattended isolation,
crash-recovery, and anti-resurrection gates are defined
in [`memory-review-loop.md`](memory-review-loop.md) and are not yet implemented. Request-bound idempotency alone
does not authorize a scheduler. Technical skepticism is an opt-in Skill policy, not a durable user
fact and not a reason to expose unrelated private memory.

`review-due` is the only command intended for unattended periodic invocation. It is exact-scope,
credential-free, content-free, and read-only. Scheduler configuration must not contain proposal,
approval, or application capability paths. A scheduled process may notify on `due` or
`recommended_actions`; it must not translate those fields into a write automatically.

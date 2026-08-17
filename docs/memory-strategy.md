# Memory architecture and Mem0 strategy

Status: target architecture plus an experimental Phase 1 implementation, reviewed 2026-08-16.

The current product provides Workspace → Memory Pack → Home → CLI/read-only MCP, plus an
explicitly initialized Adaptive Memory ledger operated only through the local CLI. Unified Recall
and an Agent writer are not implemented.

## Product decision

AGIWiki can serve the same broad personal-agent memory market as Mem0, but it should not become a
thin clone that extracts facts from conversations, writes vectors, and performs semantic search.

The target is:

> A local-first, portable, correctable personal memory layer that manages both source-derived
> factual artifacts and explicitly authorized adaptive memory.

That requires two planes with different provenance and lifecycle rules:

1. **Artifact Memory** comes from PDFs, manuals, code, saved web pages, and notes. It has source
   editions, stable identities, exact locators, and immutable Memory Pack releases.
2. **Adaptive Memory** comes from explicitly authorized preferences, interactions, and task
   outcomes. It can be corrected, expire, and be forgotten in a private local ledger.

Future retrieval may combine both, but they must not become one canonical store. Original source
material remains the evidence for Artifact Memory; authorized interaction state remains the basis
for Adaptive Memory.

## What to learn from Mem0

Mem0's current documentation describes an open-source, self-hostable memory engine with add,
search, update, and delete operations, metadata filters, async support, reranking, multimodal input,
and REST service options. Its newer OSS algorithm uses single-pass ADD-only extraction and
multi-signal retrieval with semantic, BM25, and entity signals. External graph-store support has
been removed from that path in favor of entity linking.

Primary references:

- [Open Source overview](https://docs.mem0.ai/open-source/overview)
- [Python quickstart](https://docs.mem0.ai/open-source/python-quickstart)
- [Search memory](https://docs.mem0.ai/core-concepts/memory-operations/search)
- [New OSS memory algorithm](https://docs.mem0.ai/migration/oss-v2-to-v3)

AGIWiki should learn from:

- a complete add → search → correct/update → delete lifecycle;
- explicit user, agent, run, and workspace scopes;
- metadata filters, expiration, history, and replaceable retrieval backends;
- hybrid retrieval, optional reranking, and entity signals;
- a clear progression from local library to service and managed offering.

AGIWiki should not copy:

- a vector index as the only memory truth;
- mandatory cloud models, embedding APIs, or network access;
- silently promoting one model extraction into established fact;
- putting factual sources, preferences, events, and temporary context under one lifecycle;
- a second graph database that users must keep manually consistent.

## Competitive position

Both products can address the user task:

> Let different agents recover information that this person or project will need in future sessions.

The differentiation is narrower and testable:

| Capability | General adaptive-memory systems | AGIWiki |
| --- | --- | --- |
| Extract preferences/events from conversation | Common core capability | Not automatic; explicit CLI only |
| Add/search/update/delete | Core capability | Experimental local CLI lifecycle |
| Vector and hybrid retrieval | Common core path | Replaceable projection, never canonical truth |
| PDF/manual source editions and versions | Usually input metadata | Artifact Memory core |
| Portable immutable knowledge package | Usually not the main deliverable | Core differentiator |
| Model-free, network-free reads | Configuration dependent | Default guarantee |
| Reproduction and correction | Dynamic record history | Separate Pack revisions and adaptive ledger |
| Interoperability | Platform ecosystem | Future optional adapters |

AGIWiki can eventually compete with Mem0 for local adaptive memory while also treating Mem0 as an
optional backend. Choosing one backend must never destroy or lock away a user's Memory Packs.

## Target architecture

```text
PDF / manual / saved web page / code / note
                         │
                         ▼
                editable Workspace
                         │
                         ▼
              immutable Memory Pack --------+
                                              |
explicit authorized preference / episode     +--> future Unified Recall --> Agent
                         │                    |
                         ▼                    |
              private adaptive ledger -------+
```

### Artifact plane

- Workspace is editable source.
- Entry is a source-grounded fact, concept, procedure, or troubleshooting record.
- Pack is immutable, portable, and verifiable.
- Home installs, activates, and indexes exact Packs.
- A change creates a new Pack; an installed Pack is never overwritten in place.

### Adaptive plane

The current experimental ledger stores:

```text
memory_id / lineage_id      random identity and revision lineage
memory_class                profile | episode
scope                       user | agent | run | workspace + exact key
content                     explicit content, never hidden reasoning
content_digest              recomputed on every read
provenance                  type plus optional SHA-256 evidence digest
valid_from / valid_to       applicability window
confidence                  bounded declared confidence
sensitivity                 private | sensitive
retention / expires_at      durable or expiring
status                      active | superseded | deleted
```

Rules:

- never capture complete conversations silently;
- keep a stable user preference as `profile` only after explicit submission;
- keep a bounded past task outcome as `episode`;
- do not infer or persist affective state in Phase 1;
- correction appends a revision and supersedes the old head;
- confirmed forgetting removes content and searchable projection from the complete lineage and
  keeps only content-free tombstone metadata;
- forgetting is application-level best-effort deletion, not guaranteed forensic erasure from
  filesystem snapshots, backups, SSD retention, or external copies;
- never persist raw prompts, hidden reasoning, credentials, unnecessary personal data, or full
  logs; a closed Schema cannot recognize every secret embedded in free text.

### Working memory

Current tool output, unfinished plans, and model state belong to the agent runtime. AGIWiki does not
persist hidden reasoning. Content enters long-term memory only through an explicit Artifact Entry
or Adaptive Memory operation.

## Classification dimensions

Do not grow one unbounded `kind` enum. Classify memory along independent dimensions:

| Dimension | Examples |
| --- | --- |
| Origin | artifact, interaction, explicit user input, import |
| Semantic shape | fact, concept, procedure, troubleshooting, profile, episode |
| Scope | user, agent, run, workspace |
| Lifecycle | immutable revision, active, superseded, expiring, deleted |
| Sensitivity | private, sensitive |

The four Artifact kinds describe source-derived Entry structure. Preferences and episodes belong
to a mutable lifecycle and must not distort the Pack Schema.

## Storage and distribution

```text
Original documents       existing folders, Zotero, Git, NAS, or user cloud storage
Workspace                editable JSON, optionally beside a project
Memory Pack              portable immutable directory
Adaptive ledger          local SQLite below Personal Home, separate from Pack registry
FTS / embedding / entity disposable rebuildable projection
Cloud registry           optional future service, never local canonical truth
```

Contracts and identities are unified; Entries, Packs, dynamic records, and scopes remain discrete.
Do not create one giant JSON file or one resident database per source document.

## Relationship and graph policy

Pack relations such as `parent_of`, `child_of`, `related_to`, `prerequisite_for`, `supersedes`, and
`contradicts` remain explicit canonical edges. Any automatically extracted entity graph is only a
rebuildable retrieval projection:

- Pack relations are canonical.
- Adaptive provenance and supersession are canonical.
- entities and co-occurrence edges extracted from prose are cache data.
- prefer an embedded SQLite projection; do not require Neo4j merely to claim a knowledge graph.
- add an external graph adapter only after a real multi-hop benchmark proves value.

## Agent surface evolution

The existing MCP remains:

```text
agiwiki://catalog
find_memory
get_memory
```

Local Adaptive Memory uses explicit operator CLI commands only. It never reads conversation
history. Schema v2 records a request-bound operation ID for every write; the caller must retain and
reuse the same explicit ID to retry safely after a lost response.

Any future writer must be separate and disabled by default:

```text
agiwiki-mcp             read-only, enabled by the user
agiwiki-memory-writer   separate, scoped, explicit opt-in
```

Candidate write operations are remember, correct, forget, and record-episode. Ordinary users must
remain able to use CLI or a Skill without granting an agent write access.

Periodic review follows the same separation. Daily checks should be deterministic and read-only;
a weekly Agent pass may propose corrections, merges, retention changes, or technical-claim
challenges, but it cannot apply them. Human approval and request-bound idempotency are required
before any scheduled writer exists. Behavioral instructions such as technical skepticism belong in
a versioned opt-in Skill, not in every factual or profile record. See the
[periodic memory review loop](memory-review-loop.md).

## Interoperability route

Mem0 must not become a required dependency. Future work can add:

1. canonical import of user-selected Mem0 exports into quarantined profile/episode candidates;
2. explicit export of selected Adaptive records, never Pack source text;
3. an optional backend that lets future Unified Recall query both Pack and Mem0;
4. a complete native SQLite backend for users without Mem0;
5. the same benchmark across native, adapter, and no-long-term-memory conditions.

## Cloud boundary

Cloud is not the next core feature. Git, Syncthing, Nextcloud, or an existing user drive can test
cross-device demand first. A future independent sync service may provide encrypted Pack transfer,
versioned adaptive events, device permissions, signatures, release channels, and revocation. It
must not retain original PDFs or full conversations by default or make a hosted vector index the
only truth.

## Delivery phases

### Phase 0 — contracts and baseline: complete

- freeze the two-plane terminology and safety boundary;
- preserve Pack v2 and read-only MCP;
- establish comparison tasks and performance baselines.

### Phase 1 — local adaptive ledger: experimental implementation

- separate versioned SQLite ledger and closed Schemas;
- profile and episode only;
- explicit init/remember/get/list/search/correct/forget CLI;
- content-free, read-only `review-plan` for expired and exact-duplicate candidates;
- content-free persisted review proposals and append-only human decision receipts;
- hashed local capabilities separating `review_propose`, `review_approve`, and manual
  `review_apply`;
- atomic, content-free application receipts for still-current expiry deletion and exact-duplicate
  reduction; content-generating correction remains an explicit operator command;
- a credential-free, exact-scope `review-due` projection for daily or weekly external reminders,
  without a resident daemon or scheduled write authority;
- exact scope, TTL, supersession, and content-free tombstone;
- request-bound operation idempotency with content-derived bindings cleared by forget;
- no Agent writer, bulk import/export, conversation capture, LLM, or embedding requirement;

### Phase 2 — optional Agent writes

- separate writer process or Skill;
- explicit principal, scope, and policy on every write;
- human confirmation for sensitive or durable profile records;
- prompt-injection and memory-poisoning tests.

Affective memory remains deferred until a separate evaluation proves sustained value and short TTL,
confirmation, correction, and forgetting all pass.

### Phase 3 — Unified Recall

- retrieve Artifact and Adaptive memory separately, then merge;
- optional local embedding, BM25, entity boost, and reranking;
- expose signal provenance rather than one opaque score;
- enforce Token budgets and surface conflicts.

### Phase 4 — interoperability and evaluation

- add local canonical import/export only after redaction, scope, and tombstone anti-resurrection tests;
- evaluate across agents, models, and devices;
- compare adaptive-memory and source-grounding benchmarks;
- stop rebuilding general memory if the native layer does not beat an adapter on locality,
  correction, privacy, portability, or total cost.

### Phase 5 — optional cloud

Build a registry only after real users repeatedly request sync and demonstrate willingness to pay.

## Acceptance and stop criteria

Remembering one preference does not validate the product. Measure:

- recall and precision on questions that require memory;
- persistence rate for content that should not have been remembered;
- correction success;
- leakage after delete or TTL;
- cross-scope leakage among user, agent, run, and workspace;
- rate of incorrectly fossilized inference or affect;
- Artifact locator and version accuracy;
- Token, latency, and model cost per successful recall.

If native Adaptive Memory cannot improve locality, correction, privacy, portability, or total cost
over a Mem0 adapter on real tasks, stop rebuilding a general memory engine. Keep Artifact Memory
and support Mem0 as a backend.

## Commercial boundary

The local Apache-2.0 core remains free. Potential paid services are separate conveniences:

- encrypted multi-device sync and backup;
- signed Pack registry and update channels;
- optional hosted extraction, embedding, or reranking;
- high-quality vertical Packs;
- team sharing and device permissions.

Prove that users repeatedly save, correct, and retrieve memory before building cloud operations.

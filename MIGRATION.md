# AGIWiki migration boundary

## Target product

```text
Editable JSON Workspace
→ immutable portable Memory Pack
→ local Home and disposable FTS index
→ CLI / two-tool stdio MCP
```

## Rewrite rather than directory copy

The old `agentpedia_home`, `vertical_library`, and `knowledge_compiler`
directories are not copied. Their dependency closure includes public Capsule,
Wiki, rights, visibility, publication, outcome, source-replay, and governance
objects that are outside the personal product.

The new code freezes four small contracts (the first public Pack manifest is
`agiwiki.memory-pack.v2`):

1. Workspace project;
2. Source descriptor;
3. factual memory Entry;
4. immutable Memory Pack.

SQLite search indexes live below Home cache and are rebuilt from Pack JSON.
They are not canonical Pack files and do not affect Pack identity.

## First-release exclusions

- HTML/Wiki/HTTP;
- dynamic conversation memory;
- source importers and PDF parsing;
- outcome feedback;
- Mem0/Letta/Graphiti adapters;
- automatic upgrades;
- backup orchestration;
- remote transports;
- public content hosting.

The first release expects a user-selected Agent to read authorized source
material and edit Workspace JSON. AGIWiki remains deterministic and model-free.

These exclusions describe the original Pack-focused 0.1 migration boundary, not permanent
non-goals for every future version. The current checkout also contains an experimental, opt-in
local Adaptive Memory ledger without replacing Memory Packs or expanding the read-only MCP.
It must be created with `agiwiki adaptive init` and supports explicit CLI operations only;
automatic conversation capture, an Agent writer, Unified Recall, import/export, and adapters
remain unimplemented. See [`docs/memory-strategy.md`](docs/memory-strategy.md).

Adaptive schema v5 retains the v2 request-bound operation journal, v3 immutable review records, and
v4 hashed local principals, then adds append-only application receipts and the separate
`review_apply` permission. Running `agiwiki adaptive init` against a recognized private v1, v2,
v3, or v4 ledger performs an
explicit transactional migration; ordinary reads reject old versions and never migrate them
implicitly. Existing memories, events, operations, proposals, and legacy v1 decision receipts are
preserved. Migrated v3 rows have no retrospective principal binding and cannot be applied. New
review decisions use v2 receipts. A separately authorized and confirmed `review-apply` can execute
only still-current expiry deletion or exact-duplicate reduction; corrections remain explicit
content-bearing operator commands.

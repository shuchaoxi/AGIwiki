# Architecture

AGIWiki 0.1 deliberately has a provider-neutral authoring control plane, four portable/runtime
layers, and one optional adapter surface:

```text
Authorized source -> Authoring Controller -> user-selected Agent/Skill
                                             |
                                             v
Workspace JSON                    editable source
       |
       | validate + deterministic build
       v
Memory Pack                       immutable portable JSON
       |
       | verify + atomic install
       v
Personal Home                     exact activation + disposable FTS cache
       |
       +-------------------+
       v                   v
CLI                       stdio MCP
                          find_memory / get_memory

Explicit adaptive init -> private adaptive.sqlite3 -> operator CLI only
```

## Workspace

The Workspace is the only user-editable layer. It contains `agiwiki.json`,
`sources/*.json`, and `entries/*.json`. A user-selected Agent may read
authorized PDFs, manuals, web exports, code, and notes and write these JSON
files. AGIWiki itself does not call a model.

Document compilation follows the separate `agiwiki-author-memory` Skill; the core runtime never
calls a model. The Skill writes only the editable Workspace. Pack building, installation, and
activation remain explicit local operations.

For large sources, the local Authoring Controller creates an immutable batch plan, cumulative
budget, and resumable progress. It coordinates the user's selected agent without bundling a
provider SDK or storing a copy of source text. Private control state lives in
`Workspace/.agiwiki-author/` and never enters a Pack. See
[`authoring-controller.md`](authoring-controller.md).

## Memory Pack

A version-2 Pack contains only canonical, portable JSON bytes and records the exact
`agiwiki.entry-quality.v1` build policy. It excludes timestamps,
AGIWiki-generated absolute paths, databases, embeddings, caches, prompts, and
machine-specific state. Credential-bearing URIs and common local paths are
mechanically rejected, but authors remain responsible for not writing secrets
into free text. Pack identity changes whenever semantic Source or Entry content
changes.

The JSON Schema permits a large bounded file set as a denial-of-service safety ceiling, not a
latency promise. Version 0.1 fully authenticates Pack and index content on reads and is aimed at
small to medium personal collections; large-corpus performance work remains future work.

## Personal Home

Home stores verified Pack releases, exact activation state, and rebuildable
search indexes. Installed Pack files are immutable inputs. Direct editing is
detected before activation or reading.

The experimental Adaptive ledger is a separate SQLite database. `home init` does not create it;
the owner must run `adaptive init`. Its records are mutable lifecycle state rather than Pack
content, and its CLI does not alter Pack activation or identity. Adaptive schema v5 contains the
request-bound operation journal plus content-free review proposals and append-only human decision
receipts. Separate hashed local capabilities authorize proposing and approving; enrollment remains
under the local OS-owner trust boundary. A decision never applies its suggested action, and this
does not add an Agent writer or scheduler.

## Agent surface

The MCP server is local stdio and content-read-only. `find_memory` returns ranked
candidate memories. `get_memory` returns one exact Entry. Building, installing,
activating, and repairing are operator actions available only through the CLI.
Reads may create disposable indexes or quarantine an installed Pack that fails integrity;
they never edit Workspace or Pack JSON.

## Non-goals

The shipped core is not a website, hosted RAG service, public knowledge network, automatic
conversation-capture system, general document parser, or LLM runtime. Artifact search is a replaceable
local projection; the portable JSON Pack remains the source-derived deliverable.

## Target memory architecture

The product roadmap does not replace the Pack pipeline. Phase 1 now implements a separate,
opt-in Adaptive Memory CLI for explicitly submitted preferences and episodes. Unified Recall is
still only a target. Affective memory is deferred and would require a separate safety gate:

```text
Source material -> Workspace -> immutable Pack ----+
                                                    +-> Unified Recall -> Agent
Authorized interactions -> mutable local ledger ---+
```

Artifact and Adaptive memory have different provenance and lifecycle rules. Working memory and
hidden model reasoning remain the responsibility of the Agent runtime and are not persisted by
default. Dynamic writes must not be silently added to the existing read-only MCP surface.

The full design, Mem0 comparison, graph boundary, staged implementation plan, and stop criteria
are in [`memory-strategy.md`](memory-strategy.md). Agent writer, automatic capture, affective
memory, adapters, and Unified Recall are not part of the current contract.

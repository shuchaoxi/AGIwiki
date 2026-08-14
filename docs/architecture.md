# Architecture

AGIWiki deliberately has four layers and one optional adapter surface:

```text
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
```

## Workspace

The Workspace is the only user-editable layer. It contains `agiwiki.json`,
`sources/*.json`, and `entries/*.json`. A user-selected Agent may read
authorized PDFs, manuals, web exports, code, and notes and write these JSON
files. AGIWiki itself does not call a model.

## Memory Pack

A Pack contains only canonical, portable JSON. It excludes timestamps,
AGIWiki-generated absolute paths, databases, embeddings, caches, prompts, and
machine-specific state. Credential-bearing URIs and common local paths are
mechanically rejected, but authors remain responsible for not writing secrets
into free text. Pack identity changes whenever semantic Source or Entry content
changes.

## Personal Home

Home stores verified Pack releases, exact activation state, and rebuildable
search indexes. Installed Pack files are immutable inputs. Direct editing is
detected before activation or reading.

## Agent surface

The MCP server is local stdio and read-only. `find_memory` returns ranked
candidate memories. `get_memory` returns one exact Entry. Building, installing,
activating, and repairing are operator actions available only through the CLI.

## Non-goals

The core is not a website, hosted RAG service, public knowledge network,
conversation-memory system, document parser, or LLM runtime. Search is a
replaceable local projection; the portable JSON Pack is the delivered product.

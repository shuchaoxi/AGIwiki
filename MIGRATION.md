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

The new code freezes four small contracts:

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

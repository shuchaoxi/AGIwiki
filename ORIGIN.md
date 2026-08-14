# Source origin and relicensing record

AGIWiki is a new, reduced personal product. It does not copy the historical
repository as a directory tree and does not inherit its website, community,
governance, pilot, data, or multi-license documentation surfaces.

The repository owner has stated that the software work selected for migration
is their own work and is releasing the new implementation under Apache-2.0.

Reference source:

```text
repository: https://github.com/shuchaoxi/AGIPedia.git
reviewed source commit: 28afb86b860a49d5a6ea329908b2918388ebe028
```

Only design patterns and small reviewed primitives are migrated:

- canonical JSON, content digests, and stable content identities;
- symlink-safe and bounded JSON input;
- closed immutable directory Pack verification;
- atomic local Pack installation;
- exact Pack activation and scope narrowing;
- rebuildable SQLite full-text indexes;
- two-tool, local stdio MCP assembly.

Explicitly not migrated:

- TEG, Yajie, Spring, and audit workflow implementations;
- public AGIWiki/website code and deployment;
- Capsule publication, community, identity, federation, Pilot, and A/B code;
- historical datasets and content proposals;
- legacy documentation text or presentation assets.

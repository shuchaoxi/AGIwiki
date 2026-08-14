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

`workspace validate` also applies `agiwiki.entry-quality.v1`. This is a deliberately small
information-completeness gate: summaries, fact statements, definitions, goals, diagnostic
signals, actions, and verification text must contain enough letters or numbers to be usable,
and every Entry needs at least two retrieval keywords. Only letters and numbers count
toward the minimum, so punctuation padding is rejected. It blocks one-word placeholders
but does not claim to prove truth. Accuracy still depends on inspecting the exact Source edition,
digest, and locator and preserving uncertainty during authoring.

`agiwiki.json` identifies one editable knowledge collection and its release
version. Filenames do not determine identity.

## Source

A Source records a stable `source_id`, media kind, title, edition, content
digest, optional portable canonical URI, and language. Original document bytes
are not copied into a Pack in version 0.1.

## Entry

An Entry has a stable `entry_id` and one of four kinds:

- `fact`: a statement plus qualifiers;
- `concept`: definition, details, examples, and misconceptions;
- `procedure`: goal, prerequisites, ordered steps, warnings, verification, and
  failure guidance;
- `troubleshooting`: symptoms, diagnostics, fixes, escalation, warnings, and
  verification.

Every Entry cites at least one Source through a portable locator. The derived
`entry_version_id` changes when the canonical Entry changes; the stable
`entry_id` does not.

## Memory Pack

The Pack manifest binds the exact Workspace, Source set, Entry revisions,
output files, and their digests. `pack_id` derives from canonical portable
semantics. Timestamps, file locations, SQLite bytes, and local activation state
never enter its identity.

The schemas live in [`src/agiwiki/schemas`](../src/agiwiki/schemas). The
[`examples/minimal-memory`](../examples/minimal-memory) Workspace contains one
Source and one Entry of each kind.

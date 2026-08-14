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
- atomic, no-clobber Pack installation and private Home permissions;
- exact Pack activation and scope that can only narrow active memory;
- Pack verification before activation, indexing, search, and exact reads;
- local stdio MCP with two read tools and no management operation;
- normal `found: false` results instead of synthesized knowledge.

Original documents remain the factual source. A Memory Entry is a derived,
versioned aid. For dangerous actions, current state and the original material
must still be checked and human confirmation may still be necessary.

AGIWiki does not attempt to recognize every possible secret embedded in prose.
Workspace authors and the Agent that prepares JSON must not copy passwords,
tokens, private keys, raw confidential logs, or unnecessary original text into
an Entry.

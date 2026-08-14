# Contributing to AGIWiki

Thank you for helping improve AGIWiki. The project is intentionally narrow: editable JSON
Workspace → immutable Memory Pack → private local Home → CLI or two-tool stdio MCP.

Before proposing code, read `AGENTS.md`, `docs/architecture.md`, and
`docs/security-model.md`. Website, HTTP, hosted-service, community, enterprise-tenancy,
dynamic-memory, and model-runtime features are outside this repository.

## Development setup

Use Python 3.12 or newer in an isolated environment:

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -e '.[dev]'
./.venv/bin/python -m pytest -q
./.venv/bin/python -m ruff check .
./.venv/bin/python -m build
./.venv/bin/python -m twine check dist/*
```

Changes to JSON Schemas or portable identifiers require contract tests, an explicit
contract-version decision, and a migration note. Keep MCP read-only and limited to
`find_memory` and `get_memory`.

Do not include private source documents, credentials, generated Packs, Home databases, or
machine-specific paths in a contribution. Report security issues through `SECURITY.md`, not
a public issue.

Unless explicitly stated otherwise, contributions submitted to this repository are
licensed under Apache-2.0, the repository license.

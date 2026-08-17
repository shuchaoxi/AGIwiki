# Contributing to AGIWiki

Thank you for helping improve AGIWiki. The project reached its final Alpha at
`v0.1.0-alpha.1` and is now an experimental reference implementation in maintenance mode.
The implemented scope remains intentionally narrow: editable JSON Workspace → immutable
Memory Pack → private local Home → CLI or two-tool stdio MCP.

Maintainers may accept critical security or data-integrity fixes, reproducibility repairs,
dependency compatibility updates, tests, and documentation corrections. New product features are
out of scope. In particular, do not propose Adaptive Memory expansion, cloud or hosted services,
knowledge-graph engines, automatic conversation capture, or new provider-specific adapters.

Before proposing code, read `AGENTS.md`, `docs/architecture.md`, and
`docs/security-model.md`. Website, HTTP, hosted-service, community, enterprise-tenancy,
automatic conversation capture, and model-runtime features are outside this repository.
The experimental Adaptive Memory ledger is frozen at its final Alpha behavior and remains limited
to explicit local CLI operations; it must not widen the two-tool read-only MCP surface.

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

The optional repository-side LangGraph development loop is not part of the AGIWiki runtime.
To work on it, install the explicit extra and run its isolated tests:

```bash
./.venv/bin/python -m pip install -e '.[dev,devloop]'
env PYTHONPATH=tools ./.venv/bin/python -m pytest -q tools/agiwiki_devloop/tests
```

Changes to JSON Schemas or portable identifiers require contract tests, an explicit
contract-version decision, and a migration note. Keep MCP read-only and limited to
`find_memory` and `get_memory`.

Do not include private source documents, credentials, generated Packs, Home databases, or
machine-specific paths in a contribution. Report security issues through `SECURITY.md`, not
a public issue.

Files ending in `.credential.json` contain local capability secrets and are ignored by default.
Never override that ignore rule or paste their contents into logs, examples, issues, or tests.

Unless explicitly stated otherwise, contributions submitted to this repository are
licensed under Apache-2.0, the repository license.

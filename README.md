# AGIWiki

[![CI](https://github.com/shuchaoxi/AGIwiki/actions/workflows/ci.yml/badge.svg)](https://github.com/shuchaoxi/AGIwiki/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

> Experimental reference implementation for compiling source material into portable factual
> memory packs for AI agents.

> [!IMPORTANT]
> AGIWiki entered maintenance mode on 2026-08-17 with its final Alpha release,
> `v0.1.0-alpha.1`. The repository remains public as an Apache-2.0 reference implementation.
> Existing code, contracts, tests, and evaluation artifacts are retained, but no new Adaptive
> Memory, cloud, knowledge-graph, or third-party adapter development is planned. See
> [PROJECT_RETROSPECTIVE.md](PROJECT_RETROSPECTIVE.md) for the decision and evidence.

AGIWiki is an open-source, local-first factual-memory toolkit for individuals. A user-selected
agent can use the bundled `agiwiki-author-memory` Skill to read authorized PDFs, manuals, saved web
pages, code, and notes, then write ordinary JSON Workspace files. AGIWiki validates those files,
builds immutable Memory Packs, installs them into a private local Home, creates disposable search
indexes, and exposes the active Packs through a CLI or read-only stdio MCP.

```text
authorized documents + your chosen agent
                    ↓
          editable JSON Workspace
                    ↓ validate / build
          immutable Memory Pack
                    ↓ install / activate
              private AGIWiki Home
                    ↓
             CLI + read-only MCP
```

AGIWiki is not another hosted chatbot or vector-database service. It is a deterministic artifact
layer that sits beside RAG: keep ordinary long-tail prose in the source/RAG layer, and compile only
reusable, version-sensitive, operational, or failure-preventing knowledge into stable Entries.

## What 0.1 includes

- editable JSON Workspaces;
- Source, Fact, Concept, Procedure, and Troubleshooting contracts;
- reproducible Pack identity and fail-closed integrity verification;
- disposable local SQLite FTS indexes;
- multiple Pack installation with explicit activation;
- `find_memory` and `get_memory` through CLI and stdio MCP;
- provider-neutral read and authoring Skills;
- an opt-in Critical Review Skill for evidence-based technical challenge and idea deduplication;
- a resumable, budget-aware Authoring Controller for large sources;
- an experimental, explicitly initialized Adaptive Memory CLI for local profile and episode memory.

Version 0.1 deliberately excludes a website, HTML wiki, HTTP service, accounts, community hosting,
federation, enterprise tenancy, cloud sync, and automatic conversation capture. Source documents
and editable Workspaces stay on the user's device unless the user deliberately moves them.

The Adaptive Memory ledger is separate from immutable Memory Packs. It does not start with
`home init`, capture conversations, or widen the two-tool MCP surface. See
[the memory strategy](docs/memory-strategy.md) for its current limits. A future daily/weekly review
loop is specified as read-only proposal generation followed by explicit approval; it is not an
automatic writer. See [Periodic memory review](docs/memory-review-loop.md).

## Editing model

People and authoring agents edit Workspace JSON, never an installed Pack:

```text
edit Workspace JSON → validate → build a new Pack → explicitly activate it
```

Editing an installed Pack invalidates its digest and makes reads fail closed.

## Project status: maintenance

The final Alpha is an experimental reference implementation, not an actively developed product or
a supported public knowledge service. Maintenance is limited to critical security and data-
integrity fixes, reproducibility repairs, dependency compatibility, and documentation corrections.
Feature development has stopped, including expansion of Adaptive Memory, hosted or cloud services,
automatic knowledge graphs, and additional provider-specific adapters. The existing DeepSeek
Harness overlay remains as historical experimental evidence without an ongoing compatibility
commitment.

The complete personal-memory lifecycle runs locally, but contracts may remain incompatible with
future tools and no 1.0 release is planned. Do not describe AGIWiki as a public knowledge platform,
and do not treat example Entries, Pack integrity, or Schema validation as factual certification.

## Five-minute local trial

For a shorter consumer-oriented walkthrough, see [Getting started](docs/getting-started.md).

Install the checkout in an isolated environment:

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -e .
```

Run the lifecycle without activating the virtual environment:

```bash
# 1. Validate the included example Workspace.
./.venv/bin/agiwiki workspace validate examples/minimal-memory

# 2. Build a pure-JSON immutable Pack.
./.venv/bin/agiwiki pack build examples/minimal-memory ./demo.memory-pack

# 3. Initialize a private Home and install the Pack.
./.venv/bin/agiwiki home init
PACK_ID="$(./.venv/bin/agiwiki home install ./demo.memory-pack | \
  ./.venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["pack_id"])')"
./.venv/bin/agiwiki home list

# 4. Activate that exact Pack.
./.venv/bin/agiwiki home activate "$PACK_ID"

# 5. Search and retrieve an exact Entry.
./.venv/bin/agiwiki memory find "canonical JSON"
./.venv/bin/agiwiki memory get entry_44444444444444444444444444444444 \
  --pack-id "$PACK_ID"
```

The core makes no network calls and never invokes a model. The default Home uses the platform's
user-data directory; tests and isolated instances can use the global `--home /private/path` option.

Create an empty editable Workspace when you are ready to author your own memory:

```bash
./.venv/bin/agiwiki workspace init ./my-memory \
  --slug my-memory --title "My factual memory" --locale en-US
```

## Compile a large document in bounded batches

AGIWiki does not send an entire book in one model request. Create a local plan for the exact source
the user selected. For a 480-page PDF:

```bash
PLAN_ID="$(./.venv/bin/agiwiki author plan ./manual.pdf \
  --workspace ./my-memory --unit-type page --unit-count 480 \
  --canonical-uri https://example.org/manual.pdf \
  --batch-size 20 --budget-tokens 100000 | \
  ./.venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["plan_id"])')"

./.venv/bin/agiwiki author next "$PLAN_ID" --workspace ./my-memory
./.venv/bin/agiwiki author status "$PLAN_ID" --workspace ./my-memory
```

After installing the complete authoring Skill in the agent of your choice, give it a narrow task:

```text
Use $agiwiki-author-memory to process only /absolute/path/manual.pdf into
/absolute/path/my-memory. The PDF has 480 pages. Validate and record every bounded batch, then
stop. Do not build, install, or activate a Pack for me.
```

The Agent writes and validates Entries, then records each batch using the Controller's
`result_seed`. Calling `author next` again after interruption resumes the outstanding batch.
Markdown and text sources are planned by line; PDFs require an exact caller-supplied page count.
Page or line evidence must remain inside the claimed batch.

`pack build` rejects incomplete plans, changed source bytes, or changed recorded Entries. An
emergency export requires `--allow-incomplete-authoring` and records the override. This preflight
proves control-state consistency, not truth; it always reports `semantic_review=NOT_CHECKED`.
Before publication, compare new Entries with their exact source locators. See the
[Authoring Controller](docs/authoring-controller.md) and the
[authoring Skill](skills/agiwiki-author-memory/SKILL.md).

Published research artifacts include:

- a [46-page RFC 9112 authoring and retrieval pilot](docs/evaluations/rfc9112-46-page-pilot.md);
- a [35-page RFC 9111 pre-frozen consumer pilot](docs/evaluations/rfc9111-35-page-pilot.md).
- a [seven-case RFC 9111 verbatim kind-fit regression](docs/evaluations/rfc9111-kind-fit-v4-verbatim.md).

These are internal Agent studies, not independent human validation or proof that AGIWiki beats a
strong semantic RAG system. External semantic or hybrid retrieval runs can be frozen and replayed
with [`tools/evaluate_frozen_retrieval.py`](tools/evaluate_frozen_retrieval.py). That research tool
ships only in the repository and source distribution, not in the runtime wheel. See the
[frozen-retrieval contract](docs/evaluations/frozen-external-retrieval.md).

## Experimental Adaptive Memory

Adaptive Memory currently supports explicit local `profile` and `episode` records in a separate
`adaptive.sqlite3`. Save a `profile.json` such as:

```json
{
  "contract_version": "agiwiki.adaptive-write.v1",
  "memory_class": "profile",
  "scope": {"type": "user", "key": "me"},
  "content": "Prefer concise answers and preserve necessary technical terminology.",
  "provenance": {"type": "explicit_user"}
}
```

Initialize, write, and search it explicitly:

```bash
./.venv/bin/agiwiki home init
./.venv/bin/agiwiki adaptive init
REMEMBER_OPERATION="$(./.venv/bin/python -c \
  'import secrets; print("op_" + secrets.token_hex(16))')"
./.venv/bin/agiwiki adaptive remember --input ./profile.json \
  --operation-id "$REMEMBER_OPERATION"
./.venv/bin/agiwiki adaptive search "concise answers" \
  --scope-type user --scope-key me
```

Corrections append a revision. Confirmed forgetting removes the complete lineage's content:

```bash
CORRECT_OPERATION="$(./.venv/bin/python -c \
  'import secrets; print("op_" + secrets.token_hex(16))')"
./.venv/bin/agiwiki adaptive correct <MEMORY_ID> \
  --scope-type user --scope-key me --input ./correction.json \
  --operation-id "$CORRECT_OPERATION"
./.venv/bin/agiwiki adaptive list \
  --scope-type user --scope-key me --include-expired
./.venv/bin/agiwiki adaptive review-plan \
  --scope-type user --scope-key me
FORGET_OPERATION="$(./.venv/bin/python -c \
  'import secrets; print("op_" + secrets.token_hex(16))')"
./.venv/bin/agiwiki adaptive forget <CURRENT_MEMORY_ID> \
  --scope-type user --scope-key me --confirm \
  --operation-id "$FORGET_OPERATION"
```

To keep an auditable review without changing memory, persist a proposal and record one complete
human decision set:

```bash
./.venv/bin/agiwiki adaptive principal-create \
  --permissions review_propose \
  --credential-output ./review-proposer.credential.json --confirm
./.venv/bin/agiwiki adaptive principal-create \
  --permissions review_approve \
  --credential-output ./review-approver.credential.json --confirm
./.venv/bin/agiwiki adaptive principal-create \
  --permissions review_apply \
  --credential-output ./review-applier.credential.json --confirm
./.venv/bin/agiwiki adaptive review-create \
  --scope-type user --scope-key me \
  --credential-file ./review-proposer.credential.json
# Copy proposal_id, proposal_digest, and every candidate_id into decision.json.
./.venv/bin/agiwiki adaptive review-decide <PROPOSAL_ID> \
  --scope-type user --scope-key me --input ./decision.json \
  --credential-file ./review-approver.credential.json \
  --decision-id decision_0123456789abcdef0123456789abcdef \
  --confirm
# For accepted forget/keep-one actions, bind every accepted candidate in application.json.
# Accepted corrections still require the explicit adaptive correct command.
./.venv/bin/agiwiki adaptive review-apply \
  decision_0123456789abcdef0123456789abcdef \
  --scope-type user --scope-key me --input ./application.json \
  --credential-file ./review-applier.credential.json \
  --application-id application_0123456789abcdef0123456789abcdef \
  --confirm
./.venv/bin/agiwiki adaptive review-show <PROPOSAL_ID> \
  --scope-type user --scope-key me
./.venv/bin/agiwiki adaptive review-due \
  --scope-type user --scope-key me --interval weekly
```

`review-decide` only records intention. A separate `review_apply` capability may then atomically
apply accepted expiry deletion or exact-duplicate reduction. The application must name the one
duplicate memory to keep, binds the sealed decision and proposal digests, rechecks that every
target is still current, and emits a content-free append-only receipt. It never invents corrected
content: accepted `correct` actions still require the explicit `adaptive correct` command. Keep
credential files private, do not commit them, and give proposal, approval, and application
permissions to different processes when separation matters.

Every write records a request-bound operation ID. Reusing the same explicit ID with the same
request safely returns the prior result; reusing it for different content, scope, target, or action
fails closed. If `--operation-id` is omitted, AGIWiki creates one and returns it, but a caller that
loses that response cannot safely guess it. Confirmed forgetting removes content-derived operation
digests as well as memory content. The read-only `review-plan` reports expired records and exact
duplicate candidates without returning their content or applying changes. Schema v5 persists the
proposal, decision, and optional application receipt behind separate hashed local capabilities.
Only supported, still-current, explicitly confirmed actions mutate memory; raw capability tokens
are not stored in SQLite or printed in receipts. This ledger is not queried by MCP and never reads
conversation history automatically.

For periodic reminders, schedule only `review-due`. It is credential-free and read-only: it
reports `never_reviewed`, `pending_decision`, or `reviewed`, computes the next daily or weekly due
time from the latest sealed decision, and lists pending manual actions without returning memory
content. A scheduler must parse `due` and `recommended_actions`; exit code zero means the status
contract was valid, not that a review is unnecessary. Do not place capability-file paths in cron,
systemd, Windows Task Scheduler, or Hermes scheduled commands. Proposal creation, approval, and
application remain separate interactive steps.

## Connect an agent

Install the optional MCP dependency and start the local stdio server:

```bash
./.venv/bin/python -m pip install -e '.[mcp]'
./.venv/bin/agiwiki-mcp
```

The MCP surface is intentionally fixed to one resource and two content-read tools:

- `agiwiki://catalog`
- `find_memory`
- `get_memory`

Queries cannot edit a Workspace or Pack. Runtime integrity failures can mark an installed Pack
`BROKEN` and remove it from the active set.

All three complete Skills ship in the source tree and wheel. Locate the exact installed copies without
changing any agent configuration:

```bash
./.venv/bin/agiwiki integration skill-path --capability read
./.venv/bin/agiwiki integration skill-path --capability author
./.venv/bin/agiwiki integration skill-path --capability review
```

Copy the complete selected directory into the Skill location recognized by your agent. The author
and review Skills include required `references/` files. AGIWiki never writes agent configuration on
its own.
See [Agent integration](docs/agent-integration.md). For Codex MCP configuration, also consult the
[official OpenAI MCP documentation](https://developers.openai.com/codex/mcp/).

An experimental, default-off
[DeepSeek Harness overlay](integrations/deepseek-harness/README.md) reuses the same two read-only
tools through Harness's generic MCP bridge. It is source-only, adds no Python runtime dependency,
and does not expose authoring or Adaptive Memory writes.

For a Windows client that launches AGIWiki inside WSL, generate a side-effect-free plan first:

```bash
./.venv/bin/agiwiki doctor --platform windows-wsl --distro Ubuntu-24.04
./.venv/bin/agiwiki integration render \
  --client codex --platform windows-wsl --distro Ubuntu-24.04
```

Use `--client hermes` or `--client claude` for the other supported templates. The renderer returns
separate install, verify, and remove argument arrays but never executes them. It does not inspect
provider credentials or network access; those layers remain `NOT_CHECKED`.

## Data model

- `agiwiki.json`: Workspace identity and version.
- `sources/*.json`: source edition, content digest, and portable locator.
- `entries/*.json`: fact, concept, procedure, or troubleshooting memory.
- `pack.json + sources.json + entries/*.json`: portable immutable Pack.
- `Home/indexes/*.sqlite3`: disposable search caches, not Pack content.
- `Home/adaptive.sqlite3`: explicitly initialized experimental adaptive ledger.

See [Architecture](docs/architecture.md), [Contracts](docs/contracts.md), and
[Security model](docs/security-model.md).

The example Workspace cites a secondary evidence note maintained in this repository. It
demonstrates source binding and safe handling; it is not a mirror of Python's official
documentation. Verify runtime claims against documentation for the matching Python version.

## Support scope

The complete 0.1 lifecycle and private-file permission checks are validated on Linux. CI covers
Python 3.12–3.14. macOS and Windows path handling remains experimental, and Windows `chmod` is not
an independent ACL guarantee. Reads currently favor integrity over throughput by verifying Packs
and disposable indexes. Safety limits in the Schema are not performance promises.

## Development

```bash
./.venv/bin/python -m pip install -e '.[dev]'
./.venv/bin/python -m pytest -q
./.venv/bin/python -m ruff check .
./.venv/bin/python -m build
```

Read [CONTRIBUTING.md](CONTRIBUTING.md), [ORIGIN.md](ORIGIN.md), and [MIGRATION.md](MIGRATION.md)
before proposing changes.

## License

AGIWiki is licensed under the [Apache License 2.0](LICENSE). Copyright and attribution notices are
in [NOTICE](NOTICE); the independent rewrite and relicensing record is in [ORIGIN.md](ORIGIN.md).
Unless explicitly stated otherwise, contributions submitted to this repository are licensed under
Apache-2.0.

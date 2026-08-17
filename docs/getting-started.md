# Getting started

This guide takes a new user from installation to an active local Memory Pack. AGIWiki runs locally,
does not call a model, and does not upload source documents.

## Requirements

- Python 3.12 or newer
- Linux for the fully tested 0.1 permission model
- optional: an agent that supports local Skills and stdio MCP

## Install

From a cloned repository:

```bash
python3.12 -m venv .venv
./.venv/bin/python -m pip install -e .
./.venv/bin/agiwiki --version
```

Install `.[mcp]` only if an agent will call the local stdio server:

```bash
./.venv/bin/python -m pip install -e '.[mcp]'
```

## Try the included memory

```bash
./.venv/bin/agiwiki workspace validate examples/minimal-memory
./.venv/bin/agiwiki pack build examples/minimal-memory ./demo.memory-pack
./.venv/bin/agiwiki home init
./.venv/bin/agiwiki home install ./demo.memory-pack
```

The install command returns a `pack_id`. Activate that exact value:

```bash
./.venv/bin/agiwiki home activate pack_<returned-id>
./.venv/bin/agiwiki memory find "canonical JSON"
```

Installed Packs are immutable. To change knowledge, edit the Workspace, validate it, build a new
Pack, and explicitly activate the new Pack.

## Author your own memory

Create a Workspace:

```bash
./.venv/bin/agiwiki workspace init ./my-memory \
  --slug my-memory --title "My factual memory" --locale en-US
```

For a large PDF, create a resumable plan with its exact page count:

```bash
./.venv/bin/agiwiki author plan ./manual.pdf \
  --workspace ./my-memory \
  --unit-type page --unit-count 120 \
  --batch-size 10 --budget-tokens 30000
```

Install the complete authoring Skill in your chosen agent. Locate the bundled directory with:

```bash
./.venv/bin/agiwiki integration skill-path --capability author
./.venv/bin/agiwiki integration skill-path --capability review
```

Then give the agent an exact source and target:

```text
Use $agiwiki-author-memory to process only /absolute/path/manual.pdf into
/absolute/path/my-memory. The PDF has 120 pages. Validate and record every bounded batch, then
stop before building, installing, or activating a Pack.
```

After the agent stops:

1. inspect `agiwiki author status`;
2. compare every new Entry with its exact source locator;
3. apply corrections with `agiwiki author amend`;
4. run `workspace validate` and `pack build`;
5. install and activate the resulting Pack.

Successful Schema validation is not factual certification. Pack build receipts deliberately report
`semantic_review=NOT_CHECKED` unless a person or separate reviewer performs that comparison.

## Connect through MCP

Run the stdio server from the same environment and Home:

```bash
./.venv/bin/agiwiki-mcp
```

The server exposes only:

- resource `agiwiki://catalog`;
- tool `find_memory`;
- tool `get_memory`.

It has no write, build, install, activation, or source-reading tool. See
[Agent integration](agent-integration.md) for Codex, Claude Code, Hermes, and Windows/WSL guidance.

## Diagnose before connecting

```bash
./.venv/bin/agiwiki doctor
```

For a Windows client launching AGIWiki in WSL:

```bash
./.venv/bin/agiwiki doctor --platform windows-wsl --distro Ubuntu-24.04
./.venv/bin/agiwiki integration render \
  --client codex --platform windows-wsl --distro Ubuntu-24.04
```

The renderer is inert: review its install, verify, and remove argv arrays before executing anything.
Provider credentials and network access remain `NOT_CHECKED`.

## Safety reminders

- authorize exact files rather than broad directories;
- do not put passwords, tokens, private keys, raw prompts, or hidden reasoning in a Workspace;
- do not publish machine-specific paths or diagnostic output;
- back up editable Workspaces and original sources; indexes can be rebuilt;
- treat direct edits to an installed Pack as corruption, not as an update workflow.

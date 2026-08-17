# Agent integration

AGIWiki separates three trust surfaces:

- the optional authoring Skill may read only the material the user explicitly selected and
  writes editable Workspace JSON;
- the optional Critical Review Skill challenges technical proposals but has no memory write
  authority;
- the stdio MCP is read-only and exposes only active, verified Memory Packs.

## Locate a bundled Skill

The wheel, source distribution, and editable checkout carry all three complete Skills. Locate the copy
belonging to the exact AGIWiki interpreter before installing it into an Agent:

```bash
agiwiki integration skill-path --capability read
agiwiki integration skill-path --capability author
agiwiki integration skill-path --capability review
```

The command is read-only. Its closed JSON result reports `available`, `path`, `entrypoint`, required
files, and any missing files; it never copies a Skill or modifies Agent configuration. Local
installation paths are machine-specific and should not be published as diagnostic output.

Copy the **entire** reported directory into the Skill directory recognized by your Agent. In a
source checkout the canonical directories are:

```text
skills/agiwiki-memory/
skills/agiwiki-author-memory/
skills/agiwiki-critical-review/
```

Do not copy only `SKILL.md`: the authoring Skill requires
`references/authoring-contract.md`, and the review Skill requires `references/review-contract.md`.
Skill locations and enablement are Agent-specific; consult that Agent's current documentation. An
Agent without native Skill support can still be given the relevant `SKILL.md` as an explicit
workflow, but AGIWiki does not modify its configuration.

After installing the complete author Skill, give the Agent an exact source, Workspace, and PDF page
count instead of a broad directory, for example:

```text
Use $agiwiki-author-memory to process only /absolute/path/manual.pdf into
/absolute/path/my-memory. The PDF has 120 pages. Validate and record every bounded batch, then stop
before building, installing, or activating a Pack.
```

This leaves publication as an explicit operator decision. Confirm `author status` is complete;
`pack build` will also fail closed if a local plan is incomplete or drifted.

Use `$agiwiki-critical-review` only when the user wants a technical proposal, architecture,
research direction, implementation plan, novelty claim, or prior idea challenged. It separates
facts from assumptions, compares the simplest baseline, classifies overlap with prior approaches,
and proposes the cheapest falsification test. It may consult active factual memory through the
read-only MCP, but it cannot persist an idea, preference, or verdict. Routine and emotional
conversation should not be forced through this critical mode.

## Configure local stdio MCP

Install the optional dependency and use an absolute executable path in the Agent config:

```bash
/absolute/path/to/AGIwiki/.venv/bin/python -m pip install -e '/absolute/path/to/AGIwiki[mcp]'
```

Generic configuration shape:

```json
{
  "command": "/absolute/path/to/AGIwiki/.venv/bin/agiwiki-mcp",
  "args": ["--home", "/absolute/private/path/to/agiwiki-home"],
  "transport": "stdio"
}
```

For Codex, follow the current
[Codex MCP documentation](https://developers.openai.com/codex/mcp/) and either add the stdio
server with the CLI:

```bash
codex mcp add agiwiki -- \
  /absolute/path/to/AGIwiki/.venv/bin/agiwiki-mcp \
  --home /absolute/private/path/to/agiwiki-home
codex mcp list
```

or copy the table from
[`examples/mcp/codex.config.toml`](../examples/mcp/codex.config.toml) into the Codex
`config.toml`. Codex uses `[mcp_servers.<server-name>]` TOML tables rather than the
`mcpServers` JSON shape used by some other clients.

The client should launch this process locally. Do not expose it as an unauthenticated network
service. The surface remains exactly one catalog resource and two tools:

- `agiwiki://catalog`
- `find_memory`
- `get_memory`

Authoring, building, installation, and activation remain explicit operator operations.

## DeepSeek Harness

DeepSeek Harness can consume the same read-only stdio MCP through its generic
`@deepseek-ai/dsh-mcp-client` bridge. AGIWiki ships a default-off community overlay and an exact
compatibility receipt in
[`integrations/deepseek-harness/`](../integrations/deepseek-harness/README.md).

The current Harness bridge exposes tools under qualified names, so the model sees
`mcp__agiwiki__find_memory` and `mcp__agiwiki__get_memory`. It does not bridge MCP resources;
`agiwiki://catalog` is therefore unavailable through this path. No writer tool is added.

DeepSeek Harness is in developer preview and warns that breaking changes will occur. Treat this
as an experimental integration, check the pinned upstream commit, and re-run a real lookup after
every Harness upgrade. AGIWiki does not install or configure DeepSeek Harness automatically.

## Windows clients with a WSL installation

When AGIWiki is installed inside WSL but Hermes, Claude Code, or Codex runs on Windows, the
Windows client must launch the Linux MCP process through `wsl.exe`. Do not hand-edit three
different configurations or paste an unreviewed shell string. First run the layered, read-only
diagnostic from the exact Python environment that will serve MCP:

```bash
agiwiki --home /absolute/private/agiwiki-home doctor \
  --platform windows-wsl --distro Ubuntu-24.04
```

The result separates these layers:

- packaged Core and JSON Schemas;
- private Home and registry;
- installed, verified, globally active Packs;
- the optional MCP dependency and fixed one-resource/two-tool surface;
- the Windows/WSL bridge;
- model-provider authentication and network, which are deliberately `NOT_CHECKED`.

`doctor` never creates a Home, repairs a Pack, starts MCP, reads provider credentials, or sends a
model request. A successful MCP layer therefore does not claim that the client's model account or
network is healthy.

After `doctor` reports `READY`, render an inert client-specific plan:

```bash
agiwiki --home /absolute/private/agiwiki-home integration render \
  --client hermes --platform windows-wsl --distro Ubuntu-24.04

agiwiki --home /absolute/private/agiwiki-home integration render \
  --client claude --platform windows-wsl --distro Ubuntu-24.04

agiwiki --home /absolute/private/agiwiki-home integration render \
  --client codex --platform windows-wsl --distro Ubuntu-24.04
```

The JSON result contains separate `install.argv`, `verify.argv`, and `remove.argv` arrays plus the
exact `wsl.exe` server command. Rendering is side-effect free; only executing `install.argv`
changes client configuration. Review the array before running it, and retain `remove.argv` as the
rollback. The output includes private local paths and must not be posted publicly.

For Claude, `--claude-scope local` is the safe default; `project` and `user` are explicit choices.
The renderer supports local stdio clients only. It does not claim that the ChatGPT Windows app can
consume stdio MCP, and it does not create a tunnel or public HTTP endpoint.

## Experimental adaptive-memory CLI

The local CLI now has an experimental, explicitly initialized Adaptive Memory ledger for
`profile` and `episode` records. Its `remember`, `correct`, and confirmed `forget` operations are
operator commands; they are not Agent tools. See the copyable JSON and complete CLI flow in the
[README](../README.md#experimental-adaptive-memory).

The current MCP does not query this ledger, capture conversations, or implement Mem0-compatible
writes. A future writer process or Skill must remain separate, opt-in, scoped, and disabled by
default. The local CLI now records request-bound operation IDs, but a writer still needs an
isolated capability delivery, apply policy, bounded budgets, injection defenses, and crash-safe
application. The local CLI can persist content-free review proposals and capability-authenticated
decision receipts, but those receipts deliberately do not apply actions. Unified Recall,
automatic capture, affective memory, and adapters also remain unimplemented. See
[`memory-strategy.md`](memory-strategy.md).

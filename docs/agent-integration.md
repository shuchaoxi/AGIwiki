# Agent integration

AGIWiki separates two trust surfaces:

- the optional authoring Skill may read only the material the user explicitly selected and
  writes editable Workspace JSON;
- the stdio MCP is read-only and exposes only active, verified Memory Packs.

## Install a Skill from the source repository

Clone or download this repository, then copy the **entire** selected directory into the Skill
directory recognized by your Agent:

```text
skills/agiwiki-memory/
skills/agiwiki-author-memory/
```

Do not copy only `SKILL.md`: the authoring Skill requires
`references/authoring-contract.md`. Skill locations and enablement are Agent-specific; consult
that Agent's current documentation. An Agent without native Skill support can still be given
the relevant `SKILL.md` as an explicit workflow, but AGIWiki does not modify its configuration.

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

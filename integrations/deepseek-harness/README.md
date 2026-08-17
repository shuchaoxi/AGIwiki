# DeepSeek Harness integration

This default-off integration lets DeepSeek Harness use an already initialized AGIWiki Home
through the existing read-only stdio MCP server. It does not add Node.js, Cordis, DeepSeek, or
network dependencies to the AGIWiki Python package.

DeepSeek Harness is currently a developer preview and explicitly warns that compatibility-breaking
changes will occur. This integration is therefore experimental. Its current compatibility record
is pinned in [`COMPATIBILITY.json`](COMPATIBILITY.json).

## What is connected

DeepSeek Harness starts `agiwiki-mcp` through its generic
`@deepseek-ai/dsh-mcp-client` bridge. The model receives these qualified tool names:

- `mcp__agiwiki__find_memory`
- `mcp__agiwiki__get_memory`

The AGIWiki MCP resource `agiwiki://catalog` is not available because the current Harness bridge
consumes MCP tools only. This does not block search or exact retrieval. No AGIWiki write,
authoring, build, install, activation, or Adaptive Memory command is exposed.

## Prerequisites

1. Install AGIWiki and its optional MCP dependency in an isolated Python environment.
2. Initialize an AGIWiki Home, install a verified Memory Pack, and activate it explicitly.
3. Make `agiwiki-mcp` available on the `PATH` inherited by DeepSeek Harness.
4. If you use a non-default Home, export `AGIWIKI_HOME` before starting Harness.
5. Install and configure DeepSeek Harness separately by following its current upstream guide.

Example shell setup from an AGIWiki source checkout:

```bash
./.venv/bin/python -m pip install -e '.[mcp]'
export PATH="/absolute/path/to/AGIwiki/.venv/bin:$PATH"
export AGIWIKI_HOME="/absolute/private/path/to/agiwiki-home"
npx @deepseek-ai/dsh web --patch \
  "/absolute/path/to/AGIwiki/integrations/deepseek-harness/agiwiki.cordis.yml"
```

Do not put credentials or private local paths into the committed overlay. `AGIWIKI_HOME` is read
from the process environment, and the Home remains on the user's machine.

## Verify the connection

Wait until Harness has discovered the two `mcp__agiwiki__...` tools. Then ask a question that the
active Pack can answer and explicitly request an AGIWiki lookup. Confirm in the tool trace that:

1. `mcp__agiwiki__find_memory` was called;
2. a returned exact `entry_id` was passed to `mcp__agiwiki__get_memory`;
3. the answer cites the Entry title and portable source locator;
4. a no-match result is reported as no match rather than being filled with invented memory.

The committed compatibility record is a static source-contract check, not a claim that a live
DeepSeek model, user account, network, or every future Harness release was tested.

## Upstream references

- [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
- [Harness architecture](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)
- [Generic MCP client bridge](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md)

AGIWiki is an independent Apache-2.0 project. DeepSeek Harness is an independent MIT-licensed
project. This configuration is a community interoperability example and does not imply
endorsement, partnership, or support by DeepSeek.

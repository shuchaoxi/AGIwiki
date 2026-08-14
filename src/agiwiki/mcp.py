"""Provider-neutral, read-only stdio MCP surface for AGIWiki."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Sequence

from .home import HomeService
from .paths import resolve_home_paths
from .runtime import MemoryRuntime


def build_mcp_server(
    runtime: MemoryRuntime | None = None,
    *,
    project_root: str | None = None,
) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "MCP support is optional; install the agiwiki[mcp] extra"
        ) from exc

    selected = MemoryRuntime() if runtime is None else runtime
    server = FastMCP("AGIWiki")

    @server.resource("agiwiki://catalog")
    def catalog() -> dict[str, Any]:
        """List exact locally active Memory Packs without local paths."""

        return selected.catalog(project_root=project_root)

    @server.tool()
    def find_memory(
        query: str,
        workspace_ids: list[str] | None = None,
        limit: int = 8,
        token_budget: int = 3000,
    ) -> dict[str, Any]:
        """Find candidate factual memories in the already active local scope."""

        return selected.find_memory(
            query,
            workspace_ids=workspace_ids,
            project_root=project_root,
            limit=limit,
            token_budget=token_budget,
        )

    @server.tool()
    def get_memory(
        entry_id: str,
        entry_version_id: str | None = None,
        pack_id: str | None = None,
        workspace_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Read one exact active factual-memory Entry and its source metadata."""

        return selected.get_memory(
            entry_id,
            entry_version_id=entry_version_id,
            pack_id=pack_id,
            workspace_ids=workspace_ids,
            project_root=project_root,
        )

    return server


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agiwiki-mcp")
    parser.add_argument("--home")
    parser.add_argument("--project")
    args = parser.parse_args(argv)
    home = HomeService(_paths(args.home))
    try:
        server = build_mcp_server(
            MemoryRuntime(home),
            project_root=(
                None if args.project is None else str(Path(args.project).absolute())
            ),
        )
    except RuntimeError as exc:
        print(f"agiwiki-mcp: {exc}", file=sys.stderr)
        return 2
    server.run(transport="stdio")
    return 0


def _paths(explicit: str | None):
    if explicit is None:
        return resolve_home_paths()
    return resolve_home_paths({"AGIWIKI_HOME": explicit})


__all__ = ["build_mcp_server", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

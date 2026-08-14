from __future__ import annotations

import json
from pathlib import Path
import sys
from types import ModuleType

from agiwiki.cli import main
from agiwiki.mcp import build_mcp_server


EXAMPLE = Path(__file__).parents[1] / "examples" / "minimal-memory"


def test_cli_validates_builds_and_initializes_without_network(
    tmp_path: Path, capsys
) -> None:
    assert main(["workspace", "validate", str(EXAMPLE)]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["entry_count"] == 4

    pack = tmp_path / "pack"
    assert main(["pack", "build", str(EXAMPLE), str(pack)]) == 0
    assert json.loads(capsys.readouterr().out)["pack_id"].startswith("pack_")

    home = tmp_path / "home"
    assert main(["--home", str(home), "home", "init"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1


def test_mcp_surface_has_exactly_two_read_tools_and_catalog(
    monkeypatch,
) -> None:
    class FakeFastMCP:
        def __init__(self, name: str):
            self.name = name
            self.tools = {}
            self.resources = {}

        def tool(self):
            def decorate(function):
                self.tools[function.__name__] = function
                return function

            return decorate

        def resource(self, uri: str):
            def decorate(function):
                self.resources[uri] = function
                return function

            return decorate

    mcp_module = ModuleType("mcp")
    server_module = ModuleType("mcp.server")
    fastmcp_module = ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)

    server = build_mcp_server(runtime=object())
    assert set(server.tools) == {"find_memory", "get_memory"}
    assert set(server.resources) == {"agiwiki://catalog"}

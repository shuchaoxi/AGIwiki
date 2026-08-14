from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from agiwiki.cli import main
from agiwiki.home import HomeService
from agiwiki.mcp import build_mcp_server
from agiwiki.pack import build_workspace_pack
from agiwiki.paths import resolve_home_paths
from agiwiki.workspace import validate_workspace

EXAMPLE = Path(__file__).parents[1] / "examples" / "minimal-memory"


def test_cli_validates_builds_and_initializes_without_network(
    tmp_path: Path, capsys
) -> None:
    authored = tmp_path / "authored"
    assert (
        main(
            [
                "workspace",
                "init",
                str(authored),
                "--slug",
                "authored",
                "--title",
                "Authored Memory",
            ]
        )
        == 0
    )
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["workspace_id"].startswith("ws_")
    assert (authored / "sources").is_dir()

    assert main(["workspace", "validate", str(EXAMPLE)]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["entry_count"] == 4

    pack = tmp_path / "pack"
    assert main(["pack", "build", str(EXAMPLE), str(pack)]) == 0
    assert json.loads(capsys.readouterr().out)["pack_id"].startswith("pack_")

    home = tmp_path / "home"
    assert main(["--home", str(home), "home", "init"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1


def test_cli_exposes_package_version(capsys) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["--version"])
    assert stopped.value.code == 0
    assert capsys.readouterr().out.strip() == "agiwiki 0.1.0"


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


def test_real_mcp_stdio_round_trip(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    pack = tmp_path / "pack"
    build_workspace_pack(validate_workspace(EXAMPLE), pack)
    home_path = tmp_path / "home"
    home = HomeService(resolve_home_paths({"AGIWIKI_HOME": str(home_path)}))
    home.init()
    installed = home.install_pack(pack)
    home.activate(installed.pack_id)

    async def exercise_server() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agiwiki.mcp", "--home", str(home_path)],
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            initialized = await asyncio.wait_for(session.initialize(), timeout=10)
            assert initialized.serverInfo.name == "AGIWiki"

            tools = await asyncio.wait_for(session.list_tools(), timeout=10)
            assert {tool.name for tool in tools.tools} == {
                "find_memory",
                "get_memory",
            }

            resources = await asyncio.wait_for(session.list_resources(), timeout=10)
            assert {str(resource.uri) for resource in resources.resources} == {
                "agiwiki://catalog"
            }

            result = await asyncio.wait_for(
                session.call_tool("find_memory", {"query": "规范化 JSON"}),
                timeout=10,
            )
            assert result.isError is False
            assert result.structuredContent is not None
            assert result.structuredContent["found"] is True
            assert result.structuredContent["results"][0]["entry_id"] == (
                "entry_44444444444444444444444444444444"
            )

    asyncio.run(exercise_server())

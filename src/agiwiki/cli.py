"""Small operator CLI for the personal factual-memory lifecycle."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import sys
from typing import Any, Sequence

from .doctor import inspect_home
from .home import HomeService
from .mcp import main as mcp_main
from .pack import build_workspace_pack, verify_pack
from .paths import resolve_home_paths
from .runtime import MemoryRuntime
from .workspace import initialize_workspace, validate_workspace


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.area == "mcp":
        forwarded = ["--home", str(_paths(args.home).root)]
        if args.project is not None:
            forwarded.extend(("--project", args.project))
        return mcp_main(forwarded)
    try:
        result = _dispatch(args)
    except (OSError, ValueError, KeyError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "message": str(exc)}
            ),
            file=sys.stderr,
        )
        return 2
    if result is not None:
        print(json.dumps(_jsonable(result), ensure_ascii=False, sort_keys=True))
    return 0


def _dispatch(args: argparse.Namespace) -> Any:
    if args.area == "workspace" and args.action == "init":
        manifest = initialize_workspace(
            args.path,
            slug=args.slug,
            title=args.title,
            default_locale=args.locale,
            version=args.version,
        )
        return {
            "ok": True,
            "workspace_id": manifest["workspace_id"],
            "slug": manifest["slug"],
            "version": manifest["version"],
        }
    if args.area == "workspace" and args.action == "validate":
        workspace = validate_workspace(args.path)
        return {
            "ok": True,
            "workspace_id": workspace.workspace_id,
            "workspace_digest": workspace.workspace_digest,
            "source_count": len(workspace.sources),
            "entry_count": len(workspace.entries),
        }
    if args.area == "pack" and args.action == "build":
        return build_workspace_pack(validate_workspace(args.workspace), args.destination)
    if args.area == "pack" and args.action == "verify":
        manifest = verify_pack(args.path)
        return {
            "ok": True,
            "workspace_id": manifest["workspace_id"],
            "pack_id": manifest["pack_id"],
            "manifest_digest": manifest["manifest_digest"],
            "entry_count": manifest["entry_count"],
        }
    paths = _paths(args.home)
    home = HomeService(paths)
    if args.area == "home":
        if args.action == "init":
            return {"ok": True, **home.init()}
        if args.action == "install":
            return home.install_pack(args.pack)
        if args.action == "activate":
            return home.activate(args.pack_id)
        if args.action == "deactivate":
            return home.deactivate(args.pack_id)
        if args.action == "list":
            return {"ok": True, "releases": home.list_releases()}
        if args.action == "link-project":
            return home.link_project(
                args.project,
                project_id=args.project_id,
                pack_ids=args.pack_ids,
            )
    runtime = MemoryRuntime(home)
    if args.area == "memory":
        if args.action == "catalog":
            return runtime.catalog(project_root=args.project)
        if args.action == "find":
            return runtime.find_memory(
                args.query,
                project_root=args.project,
                limit=args.limit,
                token_budget=args.token_budget,
            )
        if args.action == "get":
            return runtime.get_memory(
                args.entry_id,
                entry_version_id=args.entry_version_id,
                pack_id=args.pack_id,
                project_root=args.project,
            )
    if args.area == "doctor":
        return inspect_home(paths)
    raise ValueError("unsupported command")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agiwiki")
    parser.add_argument("--home", help="override the private AGIWiki Home")
    areas = parser.add_subparsers(dest="area", required=True)

    workspace = areas.add_parser("workspace", help="create or validate a Workspace")
    workspace_actions = workspace.add_subparsers(dest="action", required=True)
    initialize = workspace_actions.add_parser(
        "init", help="create an empty authoring Workspace"
    )
    initialize.add_argument("path")
    initialize.add_argument("--slug", required=True)
    initialize.add_argument("--title", required=True)
    initialize.add_argument("--locale", default="zh-CN")
    initialize.add_argument("--version", default="0.1.0")
    validate = workspace_actions.add_parser("validate", help="validate one Workspace")
    validate.add_argument("path")

    pack = areas.add_parser("pack", help="build or verify immutable Memory Packs")
    pack_actions = pack.add_subparsers(dest="action", required=True)
    build = pack_actions.add_parser("build", help="build a Pack from a Workspace")
    build.add_argument("workspace")
    build.add_argument("destination")
    verify = pack_actions.add_parser("verify", help="verify a closed Pack")
    verify.add_argument("path")

    home = areas.add_parser("home", help="manage the private local Home")
    home_actions = home.add_subparsers(dest="action", required=True)
    home_actions.add_parser("init", help="initialize private Home storage")
    install = home_actions.add_parser("install", help="install a verified Pack")
    install.add_argument("pack")
    activate = home_actions.add_parser("activate", help="activate one exact Pack")
    activate.add_argument("pack_id")
    deactivate = home_actions.add_parser("deactivate", help="deactivate one exact Pack")
    deactivate.add_argument("pack_id")
    home_actions.add_parser("list", help="list installed Pack releases")
    link = home_actions.add_parser(
        "link-project", help="pin project scope without broadening activation"
    )
    link.add_argument("project")
    link.add_argument("project_id")
    link.add_argument("pack_ids", nargs="+")

    memory = areas.add_parser("memory", help="query active factual memory")
    memory_actions = memory.add_subparsers(dest="action", required=True)
    catalog = memory_actions.add_parser("catalog", help="list active Packs")
    catalog.add_argument("--project")
    find = memory_actions.add_parser("find", help="find candidate memories")
    find.add_argument("query")
    find.add_argument("--project")
    find.add_argument("--limit", type=int, default=8)
    find.add_argument("--token-budget", type=int, default=3000)
    get = memory_actions.add_parser("get", help="read one exact memory Entry")
    get.add_argument("entry_id")
    get.add_argument("--entry-version-id")
    get.add_argument("--pack-id")
    get.add_argument("--project")

    areas.add_parser("doctor", help="run read-only Home diagnostics")
    mcp = areas.add_parser("mcp", help="run the two-tool stdio MCP server")
    mcp.add_argument("--project")
    return parser


def _paths(explicit: str | None):
    if explicit is None:
        return resolve_home_paths()
    return resolve_home_paths({"AGIWIKI_HOME": explicit})


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return value


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

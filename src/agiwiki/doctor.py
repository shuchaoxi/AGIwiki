"""Layered, read-only diagnostics for local Agent consumption readiness."""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import __version__
from .adaptive_contracts import adaptive_validators
from .authoring_contracts import authoring_validators
from .contracts import schema_validators
from .pack import verify_pack
from .paths import HomePaths, resolve_home_paths, safe_child
from .registry import HomeRegistry

DOCTOR_CONTRACT = "agiwiki.doctor.v2"

_OK = "OK"
_WARNING = "WARNING"
_ERROR = "ERROR"
_NOT_INITIALIZED = "NOT_INITIALIZED"
_NOT_APPLICABLE = "NOT_APPLICABLE"
_NOT_CHECKED = "NOT_CHECKED"


def inspect_home(
    paths: HomePaths | None = None,
    *,
    platform: str = "auto",
    distro: str | None = None,
    environ: Mapping[str, str] | None = None,
    wsl_executable: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Inspect Core, Home, Packs, MCP, bridge, and external-provider layers.

    The function never creates a directory, database, index, or client
    configuration. Provider authentication and network access remain outside
    the local diagnostic boundary.
    """

    selected = resolve_home_paths() if paths is None else paths
    values = os.environ if environ is None else environ
    selected_platform = _platform(platform, values)

    core = _core_layer()
    home, registry = _home_layer(selected)
    packs = _pack_layer(selected, registry)
    mcp = _mcp_layer()
    bridge = _bridge_layer(
        selected_platform,
        distro=distro,
        environ=values,
        executable=wsl_executable,
    )
    provider = {
        "name": "model_provider",
        "status": _NOT_CHECKED,
        "checks": [
            {
                "name": "authentication_and_network",
                "status": _NOT_CHECKED,
                "detail": (
                    "AGIWiki does not read provider credentials or make a model request"
                ),
            }
        ],
    }
    layers = [core, home, packs, mcp, bridge, provider]
    artifact_ready = (
        core["status"] == _OK and home["status"] == _OK and packs["status"] == _OK
    )
    mcp_ready = artifact_ready and mcp["status"] == _OK
    bridge_ready = (
        mcp_ready
        if selected_platform == "linux"
        else mcp_ready and bridge["status"] == _OK
    )
    return {
        "contract_version": DOCTOR_CONTRACT,
        "status": "READY" if bridge_ready else "NOT_READY",
        "platform": selected_platform,
        "readiness": {
            "core": core["status"] == _OK,
            "artifact_memory": artifact_ready,
            "mcp": mcp_ready,
            "windows_wsl": (
                bridge_ready if selected_platform == "windows-wsl" else None
            ),
            "model_provider": _NOT_CHECKED,
        },
        "layers": layers,
    }


def _core_layer() -> dict[str, Any]:
    checks: list[dict[str, Any]] = [
        {
            "name": "python_version",
            "status": _OK if sys.version_info >= (3, 12) else _ERROR,
            "required": ">=3.12",
            "detected": f"{sys.version_info.major}.{sys.version_info.minor}",
        },
        {
            "name": "agiwiki_version",
            "status": _OK,
            "detected": __version__,
        },
    ]
    try:
        count = (
            len(schema_validators())
            + len(authoring_validators())
            + len(adaptive_validators())
        )
        checks.append(
            {"name": "packaged_schemas", "status": _OK, "schema_count": count}
        )
    except Exception as exc:  # noqa: BLE001 - report packaged-schema corruption
        checks.append(
            {
                "name": "packaged_schemas",
                "status": _ERROR,
                "error": type(exc).__name__,
            }
        )
    return _layer("core", checks)


def _home_layer(
    paths: HomePaths,
) -> tuple[dict[str, Any], HomeRegistry | None]:
    if not paths.root.exists():
        return (
            {
                "name": "home",
                "status": _NOT_INITIALIZED,
                "checks": [
                    {
                        "name": "home_root",
                        "status": _NOT_INITIALIZED,
                        "detail": "run agiwiki home init explicitly",
                    }
                ],
            },
            None,
        )
    checks: list[dict[str, Any]] = []
    _check_private_directory(paths.root, "home_root", checks)
    for label, directory in (
        ("packs_root", paths.packs_root),
        ("staging_root", paths.staging_root),
        ("indexes_root", paths.indexes_root),
    ):
        _check_private_directory(directory, label, checks)
    registry = HomeRegistry(paths)
    try:
        metadata = registry.metadata()
        checks.append({"name": "registry", "status": _OK, **metadata})
    except Exception as exc:  # noqa: BLE001 - diagnostics report corruption
        checks.append(
            {"name": "registry", "status": _ERROR, "error": type(exc).__name__}
        )
        return _layer("home", checks), None
    return _layer("home", checks), registry


def _pack_layer(
    paths: HomePaths,
    registry: HomeRegistry | None,
) -> dict[str, Any]:
    if registry is None:
        return {
            "name": "packs",
            "status": _NOT_INITIALIZED,
            "checks": [
                {
                    "name": "registry_dependency",
                    "status": _NOT_INITIALIZED,
                }
            ],
        }
    checks: list[dict[str, Any]] = []
    try:
        releases = registry.list_releases()
        activations = registry.list_activations(scope_type="GLOBAL", scope_key="global")
    except Exception as exc:  # noqa: BLE001 - diagnostics report corruption
        return {
            "name": "packs",
            "status": _ERROR,
            "checks": [
                {
                    "name": "registry_projection",
                    "status": _ERROR,
                    "error": type(exc).__name__,
                }
            ],
        }
    if not releases:
        checks.append(
            {
                "name": "installed_releases",
                "status": _WARNING,
                "count": 0,
                "detail": "install and activate at least one verified Pack",
            }
        )
    else:
        checks.append(
            {"name": "installed_releases", "status": _OK, "count": len(releases)}
        )
    for release in releases:
        try:
            path = safe_child(paths.root, *Path(release["relative_path"]).parts)
            manifest = verify_pack(path)
            valid = (
                manifest["pack_id"] == release["pack_id"]
                and manifest["workspace_id"] == release["workspace_id"]
                and manifest["manifest_digest"] == release["manifest_digest"]
                and release["health"] == "OK"
            )
            if not valid:
                raise ValueError("identity mismatch")
            checks.append(
                {
                    "name": "release",
                    "pack_id": release["pack_id"],
                    "status": _OK,
                }
            )
        except Exception as exc:  # noqa: BLE001 - diagnose every broken release
            checks.append(
                {
                    "name": "release",
                    "pack_id": release["pack_id"],
                    "status": _ERROR,
                    "error": type(exc).__name__,
                }
            )
    checks.append(
        {
            "name": "global_activations",
            "status": _OK if activations else _WARNING,
            "count": len(activations),
            "detail": (
                "active Packs are available to the default MCP scope"
                if activations
                else "activate at least one Pack for the default MCP scope"
            ),
        }
    )
    return _layer("packs", checks)


def _mcp_layer() -> dict[str, Any]:
    available = _module_available("mcp.server.fastmcp")
    return _layer(
        "mcp",
        [
            {
                "name": "optional_dependency",
                "status": _OK if available else _ERROR,
                "detail": (
                    "mcp.server.fastmcp is importable"
                    if available
                    else "install AGIWiki with the mcp extra"
                ),
            },
            {
                "name": "surface",
                "status": _OK if available else _NOT_CHECKED,
                "resource_count": 1,
                "read_tool_count": 2,
                "write_tool_count": 0,
            },
        ],
    )


def _bridge_layer(
    platform: str,
    *,
    distro: str | None,
    environ: Mapping[str, str],
    executable: str | os.PathLike[str] | None,
) -> dict[str, Any]:
    if platform == "linux":
        return {
            "name": "windows_wsl_bridge",
            "status": _NOT_APPLICABLE,
            "checks": [],
        }
    selected = Path(
        executable if executable is not None else "/mnt/c/Windows/System32/wsl.exe"
    )
    detected_distro = environ.get("WSL_DISTRO_NAME")
    checks = [
        {
            "name": "wsl_executable",
            "status": _OK if selected.is_file() else _ERROR,
        },
        {
            "name": "wsl_distribution",
            "status": (
                _OK
                if detected_distro and (distro is None or detected_distro == distro)
                else _ERROR
            ),
            "detected": detected_distro,
            "requested": distro,
        },
    ]
    return _layer("windows_wsl_bridge", checks)


def _check_private_directory(
    path: Path, label: str, checks: list[dict[str, Any]]
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
        okay = stat.S_ISDIR(current.st_mode) and not path.is_symlink()
        private = current.st_mode & 0o077 == 0
        status = _OK if okay and private else _ERROR
    except OSError:
        status = _ERROR
    checks.append({"name": label, "status": status})


def _layer(name: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = {item["status"] for item in checks}
    if _ERROR in statuses:
        status = _ERROR
    elif _WARNING in statuses:
        status = _WARNING
    elif _NOT_INITIALIZED in statuses:
        status = _NOT_INITIALIZED
    elif _NOT_CHECKED in statuses and _OK not in statuses:
        status = _NOT_CHECKED
    else:
        status = _OK
    return {"name": name, "status": status, "checks": checks}


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _platform(value: str, environ: Mapping[str, str]) -> str:
    if value not in {"auto", "linux", "windows-wsl"}:
        raise ValueError("platform must be auto, linux, or windows-wsl")
    if value == "auto":
        return "windows-wsl" if environ.get("WSL_DISTRO_NAME") else "linux"
    return value


__all__ = ["DOCTOR_CONTRACT", "inspect_home"]

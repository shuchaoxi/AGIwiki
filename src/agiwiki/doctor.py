"""Read-only diagnostics for one personal AGIWiki Home."""

from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Any

from .pack import verify_pack
from .paths import HomePaths, resolve_home_paths, safe_child
from .registry import HomeRegistry


def inspect_home(paths: HomePaths | None = None) -> dict[str, Any]:
    selected = resolve_home_paths() if paths is None else paths
    if not selected.root.exists():
        return {
            "contract_version": "agiwiki.doctor.v1",
            "status": "NOT_INITIALIZED",
            "checks": [],
        }
    checks: list[dict[str, Any]] = []
    _check_private_directory(selected.root, "home_root", checks)
    for label, directory in (
        ("packs_root", selected.packs_root),
        ("staging_root", selected.staging_root),
        ("indexes_root", selected.indexes_root),
    ):
        _check_private_directory(directory, label, checks)
    registry = HomeRegistry(selected)
    try:
        metadata = registry.metadata()
        checks.append({"name": "registry", "status": "OK", **metadata})
    except Exception as exc:
        checks.append({"name": "registry", "status": "ERROR", "error": type(exc).__name__})
        return _report(checks)
    for release in registry.list_releases():
        try:
            path = safe_child(selected.root, *Path(release["relative_path"]).parts)
            manifest = verify_pack(path)
            valid = (
                manifest["pack_id"] == release["pack_id"]
                and manifest["workspace_id"] == release["workspace_id"]
                and manifest["manifest_digest"] == release["manifest_digest"]
            )
            if not valid:
                raise ValueError("identity mismatch")
            checks.append(
                {"name": "release", "pack_id": release["pack_id"], "status": "OK"}
            )
        except Exception as exc:
            checks.append(
                {
                    "name": "release",
                    "pack_id": release["pack_id"],
                    "status": "ERROR",
                    "error": type(exc).__name__,
                }
            )
    return _report(checks)


def _check_private_directory(
    path: Path, label: str, checks: list[dict[str, Any]]
) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
        okay = stat.S_ISDIR(current.st_mode) and not path.is_symlink()
        private = current.st_mode & 0o077 == 0
        status = "OK" if okay and private else "ERROR"
    except OSError:
        status = "ERROR"
    checks.append({"name": label, "status": status})


def _report(checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contract_version": "agiwiki.doctor.v1",
        "status": "OK" if all(item["status"] == "OK" for item in checks) else "ERROR",
        "checks": checks,
    }


__all__ = ["inspect_home"]

"""Private, platform-aware paths for one personal AGIWiki Home."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class PathError(ValueError):
    """A Home path is ambiguous, unsafe, or not private."""


@dataclass(frozen=True, slots=True)
class HomePaths:
    root: Path
    registry_db: Path
    adaptive_db: Path
    packs_root: Path
    staging_root: Path
    indexes_root: Path


def resolve_home_paths(
    environ: Mapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
) -> HomePaths:
    values = os.environ if environ is None else environ
    explicit = values.get("AGIWIKI_HOME")
    platform = os.name if platform_name is None else platform_name
    if explicit:
        root = Path(explicit).expanduser()
    elif platform == "nt":
        base = values.get("LOCALAPPDATA") or values.get("APPDATA")
        if not base:
            raise PathError("LOCALAPPDATA or APPDATA is required on Windows")
        root = Path(base) / "AGIWiki"
    else:
        base = values.get("XDG_DATA_HOME")
        if base:
            root = Path(base) / "agiwiki"
        else:
            home = values.get("HOME")
            if not home:
                raise PathError("HOME is required when AGIWIKI_HOME is not set")
            root = Path(home) / ".local" / "share" / "agiwiki"
    if ".." in root.parts:
        raise PathError("Home path must not contain parent traversal")
    root = root.absolute()
    _reject_symlink_components(root)
    return HomePaths(
        root=root,
        registry_db=root / "registry.sqlite3",
        adaptive_db=root / "adaptive.sqlite3",
        packs_root=root / "packs",
        staging_root=root / "staging",
        indexes_root=root / "indexes",
    )


def initialize_home_paths(paths: HomePaths) -> HomePaths:
    _mkdir_private(paths.root)
    for directory in (paths.packs_root, paths.staging_root, paths.indexes_root):
        _mkdir_private(directory)
    return paths


def safe_child(root: str | os.PathLike[str], *parts: str) -> Path:
    base = Path(root).absolute()
    _reject_symlink_components(base)
    if not parts:
        raise PathError("child path requires at least one component")
    for part in parts:
        candidate = Path(part)
        if not part or candidate.is_absolute() or ".." in candidate.parts:
            raise PathError("child path components must be relative and contained")
    result = base.joinpath(*parts)
    if result != base and base not in result.parents:
        raise PathError("child path escapes its root")
    _reject_symlink_components(result)
    return result


def require_private_regular_file(path: str | os.PathLike[str]) -> Path:
    target = Path(path).absolute()
    _reject_symlink_components(target)
    try:
        current = os.stat(target, follow_symlinks=False)
    except OSError as exc:
        raise PathError("required private file is missing") from exc
    if not stat.S_ISREG(current.st_mode):
        raise PathError("required private file is not regular")
    if current.st_mode & 0o077:
        raise PathError("required private file permissions are too broad")
    return target


def _mkdir_private(path: Path) -> None:
    _reject_symlink_components(path.parent.absolute())
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise PathError("Home directory cannot be created safely") from exc
    if path.is_symlink() or not path.is_dir():
        raise PathError("Home path must be a regular directory")
    os.chmod(path, 0o700)


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise PathError("Home path must not contain symlinks")


__all__ = [
    "HomePaths",
    "PathError",
    "initialize_home_paths",
    "require_private_regular_file",
    "resolve_home_paths",
    "safe_child",
]

"""Read and cross-validate one editable AGIWiki JSON Workspace."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .contracts import (
    ContractError,
    MAX_JSON_BYTES,
    load_json_document,
    normalize_entry,
    normalize_source,
    normalize_workspace,
    sha256_digest,
)
from .codec import JSONDocumentError, write_json_new
from .quality import EntryQualityError, validate_entries_quality


WORKSPACE_MANIFEST = "agiwiki.json"
MAX_SOURCES = 10_000
MAX_ENTRIES = 100_000


class WorkspaceError(ContractError):
    """A Workspace cannot be loaded as one complete portable memory source."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Workspace:
    """A normalized, referentially complete snapshot of editable JSON."""

    root: Path
    manifest_path: Path
    _manifest: Mapping[str, Any]
    _sources: tuple[Mapping[str, Any], ...]
    _entries: tuple[Mapping[str, Any], ...]
    source_paths: Mapping[str, Path]
    entry_paths: Mapping[str, Path]
    workspace_digest: str

    @property
    def manifest(self) -> dict[str, Any]:
        return deepcopy(dict(self._manifest))

    @property
    def sources(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(dict(item)) for item in self._sources)

    @property
    def entries(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(dict(item)) for item in self._entries)

    @property
    def workspace_id(self) -> str:
        return str(self._manifest["workspace_id"])

    def source(self, source_id: str) -> dict[str, Any]:
        for item in self._sources:
            if item["source_id"] == source_id:
                return deepcopy(dict(item))
        raise KeyError("Source is not present in this Workspace")

    def entry(self, entry_id: str) -> dict[str, Any]:
        for item in self._entries:
            if item["entry_id"] == entry_id:
                return deepcopy(dict(item))
        raise KeyError("Entry is not present in this Workspace")

    def locate_entry(self, entry_id: str) -> Path:
        try:
            return self.entry_paths[entry_id]
        except KeyError as exc:
            raise KeyError("Entry is not present in this Workspace") from exc

    def to_portable_dict(self) -> dict[str, Any]:
        """Return content suitable for Pack construction, with no local paths."""

        return {
            "contract_version": "agiwiki.workspace-snapshot.v1",
            "manifest": deepcopy(dict(self._manifest)),
            "sources": [deepcopy(dict(item)) for item in self._sources],
            "entries": [deepcopy(dict(item)) for item in self._entries],
            "workspace_digest": self.workspace_digest,
        }


def load_workspace(path: str | os.PathLike[str]) -> Workspace:
    """Load a flat ``sources/*.json`` plus ``entries/*.json`` Workspace."""

    root = _safe_workspace_root(path)
    manifest_path = root / WORKSPACE_MANIFEST
    sources_directory = root / "sources"
    entries_directory = root / "entries"
    _require_directory(sources_directory, "sources")
    _require_directory(entries_directory, "entries")

    source_files = _scan_json_directory(
        sources_directory,
        label="sources",
        maximum=MAX_SOURCES,
    )
    entry_files = _scan_json_directory(
        entries_directory,
        label="entries",
        maximum=MAX_ENTRIES,
    )
    if not source_files:
        raise WorkspaceError("Workspace must contain at least one Source JSON file")
    if not entry_files:
        raise WorkspaceError("Workspace must contain at least one Entry JSON file")

    try:
        manifest = normalize_workspace(
            load_json_document(manifest_path, max_bytes=MAX_JSON_BYTES),
            source_path=manifest_path,
        )
        source_rows = [
            (
                normalize_source(
                    load_json_document(item, max_bytes=MAX_JSON_BYTES),
                    source_path=item,
                ),
                item,
            )
            for item in source_files
        ]
        entry_rows = [
            (
                normalize_entry(
                    load_json_document(item, max_bytes=MAX_JSON_BYTES),
                    source_path=item,
                ),
                item,
            )
            for item in entry_files
        ]
    except (ContractError, JSONDocumentError) as exc:
        raise WorkspaceError(str(exc)) from exc

    _reject_duplicate_ids(source_rows, field="source_id", label="Source")
    _reject_duplicate_ids(entry_rows, field="entry_id", label="Entry")
    source_ids = {str(item[0]["source_id"]) for item in source_rows}
    entry_ids = {str(item[0]["entry_id"]) for item in entry_rows}
    _validate_entry_references(entry_rows, source_ids=source_ids, entry_ids=entry_ids)

    source_rows.sort(key=lambda item: str(item[0]["source_id"]))
    entry_rows.sort(key=lambda item: str(item[0]["entry_id"]))
    sources = tuple(item[0] for item in source_rows)
    entries = tuple(item[0] for item in entry_rows)
    digest = workspace_digest(manifest, sources, entries)
    return Workspace(
        root=root,
        manifest_path=manifest_path,
        _manifest=MappingProxyType(deepcopy(manifest)),
        _sources=sources,
        _entries=entries,
        source_paths=MappingProxyType(
            {str(item[0]["source_id"]): item[1] for item in source_rows}
        ),
        entry_paths=MappingProxyType(
            {str(item[0]["entry_id"]): item[1] for item in entry_rows}
        ),
        workspace_digest=digest,
    )


def validate_workspace(path: str | os.PathLike[str]) -> Workspace:
    """Validate structure and minimum information completeness without writing."""

    workspace = load_workspace(path)
    try:
        validate_entries_quality(
            workspace.entries,
            source_paths=workspace.entry_paths,
        )
    except EntryQualityError as exc:
        raise WorkspaceError(str(exc)) from exc
    return workspace


def initialize_workspace(
    path: str | os.PathLike[str],
    *,
    slug: str,
    title: str,
    default_locale: str = "zh-CN",
    version: str = "0.1.0",
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Create one private, empty authoring Workspace without overwriting files.

    An initialized Workspace intentionally does not validate as a complete memory
    source until at least one Source and one Entry have been authored.
    """

    try:
        manifest = normalize_workspace(
            {
                "contract_version": "agiwiki.workspace.v1",
                "workspace_id": workspace_id or f"ws_{secrets.token_hex(16)}",
                "slug": slug,
                "version": version,
                "title": title,
                "default_locale": default_locale,
            }
        )
    except ContractError as exc:
        raise WorkspaceError(str(exc)) from exc
    candidate = Path(path)
    if ".." in candidate.parts:
        raise WorkspaceError("Workspace path must not contain parent traversal")
    root = candidate.absolute()
    _reject_symlink_components(root.parent)
    if root.exists() or root.is_symlink():
        raise WorkspaceError("Workspace target already exists")
    if not root.parent.is_dir():
        raise WorkspaceError("Workspace parent must be an existing directory")
    try:
        root.mkdir(mode=0o700)
        os.chmod(root, 0o700)
        for directory in (root / "sources", root / "entries"):
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
        manifest_path = root / WORKSPACE_MANIFEST
        write_json_new(manifest_path, manifest)
        os.chmod(manifest_path, 0o600)
    except OSError as exc:
        raise WorkspaceError(
            "Workspace initialization failed; inspect the new target before retrying"
        ) from exc
    return deepcopy(manifest)


def workspace_digest(
    manifest: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
) -> str:
    """Digest normalized semantic content independently of filenames and layout."""

    normalized_manifest = normalize_workspace(manifest)
    normalized_sources = sorted(
        (normalize_source(item) for item in sources),
        key=lambda item: item["source_id"],
    )
    normalized_entries = sorted(
        (normalize_entry(item) for item in entries),
        key=lambda item: item["entry_id"],
    )
    return sha256_digest(
        {
            "contract_version": "agiwiki.workspace-snapshot.v1",
            "manifest": normalized_manifest,
            "sources": normalized_sources,
            "entries": normalized_entries,
        }
    )


def _safe_workspace_root(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path)
    if ".." in candidate.parts:
        raise WorkspaceError("Workspace path must not contain parent traversal")
    root = candidate.absolute()
    _reject_symlink_components(root)
    if root.is_symlink() or not root.is_dir():
        raise WorkspaceError("Workspace root must be a regular directory")
    return root


def _require_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise WorkspaceError(f"Workspace {label} must be a regular directory")


def _scan_json_directory(
    directory: Path,
    *,
    label: str,
    maximum: int,
) -> list[Path]:
    try:
        children = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise WorkspaceError(f"Workspace {label} cannot be scanned") from exc
    if len(children) > maximum:
        raise WorkspaceError(f"Workspace {label} exceeds the file count limit")
    result: list[Path] = []
    for child in children:
        if child.is_symlink():
            raise WorkspaceError(f"Workspace {label} must not contain symlinks")
        if not child.is_file() or child.suffix != ".json":
            raise WorkspaceError(
                f"Workspace {label} may contain only flat .json files"
            )
        result.append(child)
    return result


def _reject_duplicate_ids(
    rows: Sequence[tuple[Mapping[str, Any], Path]],
    *,
    field: str,
    label: str,
) -> None:
    seen: dict[str, Path] = {}
    for value, path in rows:
        identity = str(value[field])
        previous = seen.get(identity)
        if previous is not None:
            raise WorkspaceError(
                f"duplicate {label} identity {identity!r} in "
                f"{previous.name!r} and {path.name!r}"
            )
        seen[identity] = path


def _validate_entry_references(
    rows: Sequence[tuple[Mapping[str, Any], Path]],
    *,
    source_ids: set[str],
    entry_ids: set[str],
) -> None:
    for entry, path in rows:
        entry_id = str(entry["entry_id"])
        for source_ref in entry["source_refs"]:
            source_id = str(source_ref["source_id"])
            if source_id not in source_ids:
                raise WorkspaceError(
                    f"{path}: source_ref {source_id!r} is not in this Workspace"
                )
        for relation in entry["relations"]:
            target = str(relation["target_entry_id"])
            if target not in entry_ids:
                raise WorkspaceError(
                    f"{path}: relation target {target!r} is not in this Workspace"
                )
            if target == entry_id:
                raise WorkspaceError(f"{path}: Entry relation cannot target itself")


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise WorkspaceError("Workspace path must not contain symlinks")


__all__ = [
    "MAX_ENTRIES",
    "MAX_SOURCES",
    "WORKSPACE_MANIFEST",
    "Workspace",
    "WorkspaceError",
    "initialize_workspace",
    "load_workspace",
    "validate_workspace",
    "workspace_digest",
]

"""Read-only factual-memory runtime over exact active Pack releases."""

from __future__ import annotations

from copy import deepcopy
import os
import re
from typing import Any, Sequence

from .codec import canonical_json, load_json_document, sha256_digest
from .home import HomeService
from .pack import PackError, get_entry, find_memory as find_pack_memory
from .paths import safe_child


class RuntimeError(ValueError):
    """A memory request is unsafe, ambiguous, or outside active scope."""


_ENTRY_ID = re.compile(r"^entry_[a-f0-9]{32}$")
_ENTRY_VERSION_ID = re.compile(r"^entryv_[a-f0-9]{32}$")
_PACK_ID = re.compile(r"^pack_[a-f0-9]{32}$")


class MemoryRuntime:
    def __init__(self, home: HomeService | None = None):
        self.home = HomeService() if home is None else home

    def catalog(
        self,
        *,
        workspace_ids: Sequence[str] | None = None,
        project_root: str | os.PathLike[str] | None = None,
    ) -> dict[str, Any]:
        releases = self._active(workspace_ids=workspace_ids, project_root=project_root)
        return {
            "contract_version": "agiwiki.memory-catalog.v1",
            "count": len(releases),
            "packs": [
                {
                    "workspace_id": row["workspace_id"],
                    "pack_id": row["pack_id"],
                    "version": row["version"],
                    "manifest_digest": row["manifest_digest"],
                    "entry_count": row["manifest"]["entry_count"],
                    "source_count": row["manifest"]["source_count"],
                }
                for row in releases
            ],
        }

    def find_memory(
        self,
        query: str,
        *,
        workspace_ids: Sequence[str] | None = None,
        project_root: str | os.PathLike[str] | None = None,
        limit: int = 8,
        token_budget: int = 3000,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or "\x00" in query:
            raise RuntimeError("query must contain safe text")
        if len(query) > 1000:
            raise RuntimeError("query is too long")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise RuntimeError("limit must be between 1 and 20")
        if type(token_budget) is not int or not 256 <= token_budget <= 32768:
            raise RuntimeError("token_budget must be between 256 and 32768")
        releases = self._active(
            workspace_ids=workspace_ids,
            project_root=project_root,
            verify=False,
        )
        candidates: list[dict[str, Any]] = []
        for row in releases:
            index_path = safe_child(self.home.paths.indexes_root, f"{row['pack_id']}.sqlite3")
            try:
                result = find_pack_memory(
                    row["pack_path"],
                    index_path,
                    query,
                    limit=limit,
                )
            except PackError:
                self.home.quarantine_release(row["pack_id"])
                raise
            if (
                result["pack_id"] != row["pack_id"]
                or result["manifest_digest"] != row["manifest_digest"]
            ):
                raise RuntimeError("search index does not match the activated release")
            candidates.extend(
                {
                    **item,
                    "workspace_id": row["workspace_id"],
                    "pack_id": row["pack_id"],
                    "manifest_digest": row["manifest_digest"],
                }
                for item in result["results"]
            )
        candidates.sort(
            key=lambda item: (
                item["score"],
                item["workspace_id"],
                item["entry_id"],
                item["pack_id"],
            )
        )
        selected: list[dict[str, Any]] = []
        remaining = token_budget
        for item in candidates:
            estimate = max(1, (len(canonical_json(item)) + 3) // 4)
            if estimate > remaining:
                continue
            selected.append(item)
            remaining -= estimate
            if len(selected) >= limit:
                break
        return {
            "contract_version": "agiwiki.memory-find.v1",
            "found": bool(selected),
            "query_digest": sha256_digest(query.strip()),
            "count": len(selected),
            "results": selected,
            "token_budget": token_budget,
            "estimated_tokens": token_budget - remaining,
        }

    def get_memory(
        self,
        entry_id: str,
        *,
        entry_version_id: str | None = None,
        pack_id: str | None = None,
        workspace_ids: Sequence[str] | None = None,
        project_root: str | os.PathLike[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(entry_id, str) or _ENTRY_ID.fullmatch(entry_id) is None:
            raise RuntimeError("entry_id is invalid")
        if entry_version_id is not None and (
            not isinstance(entry_version_id, str)
            or _ENTRY_VERSION_ID.fullmatch(entry_version_id) is None
        ):
            raise RuntimeError("entry_version_id is invalid")
        if pack_id is not None and (
            not isinstance(pack_id, str) or _PACK_ID.fullmatch(pack_id) is None
        ):
            raise RuntimeError("pack_id is invalid")
        releases = self._active(
            workspace_ids=workspace_ids,
            project_root=project_root,
            verify=False,
        )
        if pack_id is not None:
            releases = [row for row in releases if row["pack_id"] == pack_id]
        matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in releases:
            self.home.verify_release(row["pack_id"])
            try:
                envelope = get_entry(
                    row["pack_path"],
                    entry_id,
                    entry_version_id=entry_version_id,
                    verify=False,
                )
            except KeyError:
                continue
            matches.append((row, envelope))
        if not matches:
            return {
                "contract_version": "agiwiki.memory-get.v1",
                "found": False,
                "entry_id": entry_id,
            }
        if len(matches) != 1:
            raise RuntimeError("memory lookup is ambiguous; provide exact pack_id")
        release, envelope = matches[0]
        entry = deepcopy(envelope["entry"])
        source_ids = {item["source_id"] for item in entry["source_refs"]}
        source_envelope = load_json_document(release["pack_path"] / "sources.json")
        sources = [
            deepcopy(item)
            for item in source_envelope["sources"]
            if item["source_id"] in source_ids
        ]
        return {
            "contract_version": "agiwiki.memory-get.v1",
            "found": True,
            "workspace_id": release["workspace_id"],
            "pack_id": release["pack_id"],
            "manifest_digest": release["manifest_digest"],
            "entry_version_id": envelope["entry_version_id"],
            "entry": entry,
            "sources": sources,
        }

    def _active(
        self,
        *,
        workspace_ids: Sequence[str] | None,
        project_root: str | os.PathLike[str] | None,
        verify: bool = True,
    ) -> list[dict[str, Any]]:
        requested = _scope(workspace_ids)
        releases = self.home.active_releases(
            scope_type="GLOBAL",
            scope_key="global",
            verify=verify,
        )
        if requested is not None:
            releases = [row for row in releases if row["workspace_id"] in requested]
        if project_root is not None:
            marker = self.home.load_project_marker(project_root)
            allowed = set(marker["pack_ids"])
            releases = [row for row in releases if row["pack_id"] in allowed]
        return sorted(releases, key=lambda row: (row["workspace_id"], row["pack_id"]))


def _scope(values: Sequence[str] | None) -> set[str] | None:
    if values is None:
        return None
    if isinstance(values, (str, bytes)):
        raise RuntimeError("workspace_ids must be an array")
    result = set(values)
    if len(result) != len(values) or any(
        not isinstance(item, str) or not item for item in values
    ):
        raise RuntimeError("workspace_ids must contain unique non-empty strings")
    return result


__all__ = ["MemoryRuntime", "RuntimeError"]

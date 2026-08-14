"""Build and verify portable immutable AGIWiki Memory Packs.

A canonical Pack is intentionally a JSON-only closed directory:

``pack.json``, ``sources.json`` and ``entries/*.json``.

SQLite never crosses this boundary.  Search indexes are disposable local
cache built by :mod:`agiwiki.index`.
"""

from __future__ import annotations

from copy import deepcopy
import ctypes
import errno
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from .codec import (
    JSONDocumentError,
    file_sha256,
    load_json_document,
    sha256_digest,
    stable_id,
    write_json_new,
)
from .contracts import (
    ContractError,
    normalize_entry,
    normalize_source,
    normalize_workspace,
    validate_document,
)
from .workspace import workspace_digest as calculate_workspace_digest


PACK_CONTRACT = "agiwiki.memory-pack.v1"
PACK_FORMAT = "portable-json-directory-v1"
PACK_ENTRY_CONTRACT = "agiwiki.pack-entry.v1"
PACK_SOURCES_CONTRACT = "agiwiki.pack-sources.v1"
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_PACK_ID = re.compile(r"^pack_[a-f0-9]{32}$")
_ENTRY_VERSION_ID = re.compile(r"^entryv_[a-f0-9]{32}$")
_ENTRY_ID = re.compile(r"^entry_[a-f0-9]{32}$")
_SOURCE_ID = re.compile(r"^src_[a-f0-9]{32}$")


class PackError(ValueError):
    """A Memory Pack cannot be built or consumed without ambiguity."""


def build_workspace_pack(
    workspace: Any,
    destination: str | os.PathLike[str],
) -> dict[str, Any]:
    """Build from the frozen ``Workspace`` value returned by ``load_workspace``."""

    try:
        return build_pack(
            workspace.manifest,
            workspace.sources,
            workspace.entries,
            destination,
        )
    except AttributeError as exc:
        raise PackError("workspace does not expose manifest, sources and entries") from exc


def build_pack(
    manifest: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
    destination: str | os.PathLike[str],
) -> dict[str, Any]:
    """Atomically publish one deterministic Pack, or replay an exact build."""

    workspace, normalized_sources, normalized_entries = _normalize_inputs(
        manifest, sources, entries
    )
    source_envelope, source_refs, sources_digest = _source_artifact(normalized_sources)
    entry_envelopes, entry_refs, entries_digest = _entry_artifacts(normalized_entries)
    identity = _pack_identity(
        workspace,
        sources=normalized_sources,
        entries=normalized_entries,
        sources_digest=sources_digest,
        entries_digest=entries_digest,
    )

    target = _safe_build_target(destination)
    if target.exists() or target.is_symlink():
        existing = verify_pack(target)
        if (
            existing["pack_id"] != identity["pack_id"]
            or existing["workspace_digest"] != identity["workspace_digest"]
            or existing["sources_digest"] != sources_digest
            or existing["entries_digest"] != entries_digest
        ):
            raise FileExistsError("existing Pack conflicts with this build")
        return {**_receipt(existing), "replayed": True}

    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlink_components(target.parent.absolute())
    lock = target.parent / f".{target.name}.lock"
    lock_fd = _acquire_lock(lock)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    os.chmod(temporary, 0o700)
    published = False
    try:
        entries_root = temporary / "entries"
        entries_root.mkdir(mode=0o700)
        write_json_new(temporary / "sources.json", source_envelope)
        for envelope, reference in zip(entry_envelopes, entry_refs, strict=True):
            write_json_new(temporary / reference["path"], envelope)

        output_paths = ["sources.json", *(item["path"] for item in entry_refs)]
        outputs = {
            relative: file_sha256(temporary / relative)
            for relative in sorted(output_paths)
        }
        pack_manifest: dict[str, Any] = {
            "contract_version": PACK_CONTRACT,
            "format": PACK_FORMAT,
            "workspace": workspace,
            "workspace_id": workspace["workspace_id"],
            "workspace_digest": identity["workspace_digest"],
            "pack_id": identity["pack_id"],
            "source_count": len(source_refs),
            "sources_digest": sources_digest,
            "source_refs": source_refs,
            "entry_count": len(entry_refs),
            "entries_digest": entries_digest,
            "entry_refs": entry_refs,
            "outputs": outputs,
        }
        pack_manifest["manifest_digest"] = sha256_digest(pack_manifest)
        write_json_new(temporary / "pack.json", pack_manifest)
        verify_pack(temporary)
        _rename_no_replace(temporary, target)
        published = True
        return {**_receipt(pack_manifest), "replayed": False}
    except (ContractError, JSONDocumentError, OSError, ValueError) as exc:
        if isinstance(exc, (PackError, FileExistsError)):
            raise
        raise PackError("Memory Pack build failed") from exc
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary)
        os.close(lock_fd)
        lock.unlink(missing_ok=True)


def verify_pack(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Fully authenticate a Pack's identity, JSON contracts and closed file set."""

    root = _safe_pack_root(path)
    manifest = _load_pack_manifest_unverified(root)
    _validate_manifest_shape(manifest)
    body = {key: value for key, value in manifest.items() if key != "manifest_digest"}
    if manifest["manifest_digest"] != sha256_digest(body):
        raise PackError("Pack manifest digest mismatch")

    expected_files = {"pack.json", *manifest["outputs"]}
    descendants = tuple(root.rglob("*"))
    if any(item.is_symlink() for item in descendants):
        raise PackError("Pack must not contain symlinks")
    actual_directories = {
        item.relative_to(root).as_posix() for item in descendants if item.is_dir()
    }
    actual_files = {
        item.relative_to(root).as_posix() for item in descendants if item.is_file()
    }
    if actual_directories != {"entries"}:
        raise PackError("Pack directory set is not closed")
    if actual_files != expected_files:
        raise PackError("Pack file set is not closed")
    for relative, expected in manifest["outputs"].items():
        if file_sha256(root / relative) != expected:
            raise PackError("Pack output digest mismatch")

    source_envelope = load_json_document(root / "sources.json")
    validate_document("pack-sources", source_envelope)
    _require_exact_keys(
        source_envelope,
        {"contract_version", "sources", "sources_digest"},
        "sources envelope",
    )
    if source_envelope["contract_version"] != PACK_SOURCES_CONTRACT:
        raise PackError("sources envelope contract is unsupported")
    raw_sources = source_envelope["sources"]
    if not isinstance(raw_sources, list):
        raise PackError("sources envelope must contain an array")
    normalized_sources = tuple(normalize_source(item) for item in raw_sources)
    expected_source_envelope, source_refs, sources_digest = _source_artifact(
        normalized_sources
    )
    if source_envelope != expected_source_envelope:
        raise PackError("sources envelope is not canonical")

    entry_envelopes: list[dict[str, Any]] = []
    for reference in manifest["entry_refs"]:
        envelope = load_json_document(root / reference["path"])
        normalized = _normalize_entry_envelope(envelope)
        if normalized != envelope:
            raise PackError("Pack entry envelope is not canonical")
        entry_envelopes.append(normalized)
    _, entry_refs, entries_digest = _entry_artifacts(
        tuple(item["entry"] for item in entry_envelopes)
    )

    workspace = normalize_workspace(manifest["workspace"])
    identity = _pack_identity(
        workspace,
        sources=normalized_sources,
        entries=tuple(item["entry"] for item in entry_envelopes),
        sources_digest=sources_digest,
        entries_digest=entries_digest,
    )
    expected = {
        "workspace_id": workspace["workspace_id"],
        "workspace_digest": identity["workspace_digest"],
        "pack_id": identity["pack_id"],
        "source_count": len(source_refs),
        "sources_digest": sources_digest,
        "source_refs": source_refs,
        "entry_count": len(entry_refs),
        "entries_digest": entries_digest,
        "entry_refs": entry_refs,
    }
    if any(manifest[key] != value for key, value in expected.items()):
        raise PackError("Pack identity does not match its canonical content")
    expected_outputs = {
        "sources.json": file_sha256(root / "sources.json"),
        **{
            item["path"]: file_sha256(root / item["path"])
            for item in entry_refs
        },
    }
    if manifest["outputs"] != dict(sorted(expected_outputs.items())):
        raise PackError("Pack output manifest does not match its content")
    _validate_source_references(normalized_sources, tuple(item["entry"] for item in entry_envelopes))
    return deepcopy(manifest)


def load_pack_manifest(
    path: str | os.PathLike[str],
    *,
    verify: bool = True,
) -> dict[str, Any]:
    return verify_pack(path) if verify else _load_pack_manifest_unverified(_safe_pack_root(path))


def pack_identity(manifest: Mapping[str, Any]) -> dict[str, str]:
    """Project a Pack identity without exposing paths or mutable cache state."""

    _validate_manifest_shape(manifest)
    return {
        "workspace_id": str(manifest["workspace_id"]),
        "pack_id": str(manifest["pack_id"]),
        "manifest_digest": str(manifest["manifest_digest"]),
    }


def iter_entries(
    path: str | os.PathLike[str],
    *,
    verify: bool = True,
) -> tuple[dict[str, Any], ...]:
    root = _safe_pack_root(path)
    manifest = verify_pack(root) if verify else _load_pack_manifest_unverified(root)
    result: list[dict[str, Any]] = []
    for reference in manifest["entry_refs"]:
        envelope = load_json_document(root / reference["path"])
        if file_sha256(root / reference["path"]) != manifest["outputs"][reference["path"]]:
            raise PackError("Pack entry changed after manifest verification")
        if _normalize_entry_envelope(envelope) != envelope:
            raise PackError("Pack entry envelope is not canonical")
        result.append(envelope)
    return tuple(result)


def get_entry(
    path: str | os.PathLike[str],
    entry_id: str,
    *,
    entry_version_id: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Return one exact envelope from an authenticated Pack."""

    if not isinstance(entry_id, str) or _ENTRY_ID.fullmatch(entry_id) is None:
        raise PackError("entry_id is invalid")
    if entry_version_id is not None and (
        not isinstance(entry_version_id, str)
        or _ENTRY_VERSION_ID.fullmatch(entry_version_id) is None
    ):
        raise PackError("entry_version_id is invalid")
    root = _safe_pack_root(path)
    manifest = verify_pack(root) if verify else _load_pack_manifest_unverified(root)
    references = [
        item
        for item in manifest["entry_refs"]
        if item["entry_id"] == entry_id
        and (entry_version_id is None or item["entry_version_id"] == entry_version_id)
    ]
    if not references:
        raise KeyError("entry is not present in this Pack")
    if len(references) != 1:
        raise PackError("entry lookup is ambiguous")
    reference = references[0]
    target = root / reference["path"]
    if file_sha256(target) != manifest["outputs"][reference["path"]]:
        raise PackError("Pack entry changed after manifest verification")
    envelope = load_json_document(target)
    if _normalize_entry_envelope(envelope) != envelope:
        raise PackError("Pack entry envelope is not canonical")
    if any(
        envelope[key] != reference[key]
        for key in ("entry_id", "entry_version_id", "entry_digest")
    ):
        raise PackError("Pack entry does not match its manifest reference")
    return deepcopy(envelope)


def find_memory(
    path: str | os.PathLike[str],
    index_path: str | os.PathLike[str],
    query: str,
    *,
    limit: int = 8,
) -> dict[str, Any]:
    """Query a disposable index, rebuilding it when absent or stale."""

    from .index import ensure_index, find_memory as find_indexed_memory

    target = Path(index_path)
    if target.exists() and not target.is_symlink():
        try:
            return find_indexed_memory(path, target, query, limit=limit)
        except (ValueError, OSError):
            pass
    ensure_index(path, target)
    return find_indexed_memory(path, target, query, limit=limit)


def _normalize_inputs(
    manifest: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
        raise PackError("sources must be a sequence")
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise PackError("entries must be a sequence")
    workspace = normalize_workspace(manifest)
    normalized_sources = tuple(
        sorted((normalize_source(item) for item in sources), key=lambda item: item["source_id"])
    )
    normalized_entries = tuple(
        sorted((normalize_entry(item) for item in entries), key=lambda item: item["entry_id"])
    )
    if not normalized_entries:
        raise PackError("a Pack must contain at least one entry")
    _require_unique_ids(normalized_sources, "source_id")
    _require_unique_ids(normalized_entries, "entry_id")
    _validate_source_references(normalized_sources, normalized_entries)
    return workspace, normalized_sources, normalized_entries


def _source_artifact(
    sources: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]], str]:
    refs = [
        {"source_id": item["source_id"], "source_digest": sha256_digest(item)}
        for item in sources
    ]
    digest = sha256_digest(refs)
    return (
        {
            "contract_version": PACK_SOURCES_CONTRACT,
            "sources": [deepcopy(dict(item)) for item in sources],
            "sources_digest": digest,
        },
        refs,
        digest,
    )


def _entry_artifacts(
    entries: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], str]:
    envelopes: list[dict[str, Any]] = []
    refs: list[dict[str, str]] = []
    for raw in entries:
        entry = normalize_entry(raw)
        entry_digest = sha256_digest(entry)
        version_id = stable_id(
            "entryv",
            {"entry_id": entry["entry_id"], "entry_digest": entry_digest},
        )
        envelope = {
            "contract_version": PACK_ENTRY_CONTRACT,
            "entry_id": entry["entry_id"],
            "entry_version_id": version_id,
            "entry_digest": entry_digest,
            "entry": entry,
        }
        relative = f"entries/{version_id}.json"
        refs.append(
            {
                "entry_id": entry["entry_id"],
                "entry_version_id": version_id,
                "entry_digest": entry_digest,
                "path": relative,
            }
        )
        envelopes.append(envelope)
    pairs = sorted(zip(envelopes, refs, strict=True), key=lambda pair: pair[1]["entry_id"])
    envelopes = [pair[0] for pair in pairs]
    refs = [pair[1] for pair in pairs]
    return envelopes, refs, sha256_digest(refs)


def _normalize_entry_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    validate_document("pack-entry", value)
    _require_exact_keys(
        value,
        {"contract_version", "entry_id", "entry_version_id", "entry_digest", "entry"},
        "entry envelope",
    )
    if value["contract_version"] != PACK_ENTRY_CONTRACT:
        raise PackError("entry envelope contract is unsupported")
    entry = normalize_entry(value["entry"])
    digest = sha256_digest(entry)
    version_id = stable_id(
        "entryv", {"entry_id": entry["entry_id"], "entry_digest": digest}
    )
    expected = {
        "contract_version": PACK_ENTRY_CONTRACT,
        "entry_id": entry["entry_id"],
        "entry_version_id": version_id,
        "entry_digest": digest,
        "entry": entry,
    }
    if dict(value) != expected:
        raise PackError("entry envelope identity mismatch")
    return expected


def _pack_identity(
    workspace: Mapping[str, Any],
    *,
    sources: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
    sources_digest: str,
    entries_digest: str,
) -> dict[str, str]:
    workspace_digest = calculate_workspace_digest(workspace, sources, entries)
    pack_id = stable_id(
        "pack",
        {
            "contract_version": PACK_CONTRACT,
            "format": PACK_FORMAT,
            "workspace_id": workspace["workspace_id"],
            "workspace_digest": workspace_digest,
            "sources_digest": sources_digest,
            "entries_digest": entries_digest,
        },
    )
    return {"workspace_digest": workspace_digest, "pack_id": pack_id}


def _validate_manifest_shape(value: Mapping[str, Any]) -> None:
    validate_document("memory-pack", value)
    keys = {
        "contract_version",
        "format",
        "workspace",
        "workspace_id",
        "workspace_digest",
        "pack_id",
        "source_count",
        "sources_digest",
        "source_refs",
        "entry_count",
        "entries_digest",
        "entry_refs",
        "outputs",
        "manifest_digest",
    }
    _require_exact_keys(value, keys, "Pack manifest")
    if value["contract_version"] != PACK_CONTRACT or value["format"] != PACK_FORMAT:
        raise PackError("Pack contract or format is unsupported")
    if not isinstance(value["pack_id"], str) or _PACK_ID.fullmatch(value["pack_id"]) is None:
        raise PackError("Pack ID is invalid")
    for field in ("workspace_digest", "sources_digest", "entries_digest", "manifest_digest"):
        if not isinstance(value[field], str) or _DIGEST.fullmatch(value[field]) is None:
            raise PackError(f"{field} is invalid")
    if type(value["source_count"]) is not int or value["source_count"] < 0:
        raise PackError("source_count is invalid")
    if type(value["entry_count"]) is not int or value["entry_count"] < 1:
        raise PackError("entry_count is invalid")
    _validate_source_refs(value["source_refs"])
    _validate_entry_refs(value["entry_refs"])
    outputs = value["outputs"]
    if not isinstance(outputs, dict) or not outputs:
        raise PackError("Pack outputs must be a non-empty object")
    if list(outputs) != sorted(outputs):
        raise PackError("Pack output paths must be sorted")
    for relative, digest in outputs.items():
        _validate_relative_output(relative)
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise PackError("Pack output digest is invalid")


def _validate_source_refs(value: Any) -> None:
    if not isinstance(value, list):
        raise PackError("source_refs must be an array")
    previous = ""
    for item in value:
        _require_exact_keys(item, {"source_id", "source_digest"}, "source reference")
        source_id = item["source_id"]
        if not isinstance(source_id, str) or _SOURCE_ID.fullmatch(source_id) is None:
            raise PackError("source reference ID is invalid")
        if source_id <= previous:
            raise PackError("source references must be sorted and unique")
        previous = source_id
        if not isinstance(item["source_digest"], str) or _DIGEST.fullmatch(item["source_digest"]) is None:
            raise PackError("source reference digest is invalid")


def _validate_entry_refs(value: Any) -> None:
    if not isinstance(value, list) or not value:
        raise PackError("entry_refs must be a non-empty array")
    previous = ""
    for item in value:
        _require_exact_keys(
            item,
            {"entry_id", "entry_version_id", "entry_digest", "path"},
            "entry reference",
        )
        entry_id = item["entry_id"]
        version_id = item["entry_version_id"]
        if not isinstance(entry_id, str) or _ENTRY_ID.fullmatch(entry_id) is None:
            raise PackError("entry reference ID is invalid")
        if entry_id <= previous:
            raise PackError("entry references must be sorted and unique")
        previous = entry_id
        if not isinstance(version_id, str) or _ENTRY_VERSION_ID.fullmatch(version_id) is None:
            raise PackError("entry version ID is invalid")
        if item["path"] != f"entries/{version_id}.json":
            raise PackError("entry reference path is not derived from its version")
        if not isinstance(item["entry_digest"], str) or _DIGEST.fullmatch(item["entry_digest"]) is None:
            raise PackError("entry reference digest is invalid")


def _validate_relative_output(value: Any) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or (value != "sources.json" and re.fullmatch(r"entries/entryv_[a-f0-9]{32}\.json", value) is None)
    ):
        raise PackError("Pack output path is invalid")


def _validate_source_references(
    sources: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
) -> None:
    source_ids = {item["source_id"] for item in sources}
    for entry in entries:
        for reference in entry["source_refs"]:
            if reference["source_id"] not in source_ids:
                raise PackError("entry references a Source absent from the Pack")


def _load_pack_manifest_unverified(root: Path) -> dict[str, Any]:
    try:
        return load_json_document(root / "pack.json")
    except JSONDocumentError as exc:
        raise PackError("Pack manifest is invalid") from exc


def _safe_pack_root(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path)
    if ".." in candidate.parts:
        raise PackError("Pack path must not contain parent traversal")
    root = candidate.absolute()
    _reject_symlink_components(root)
    if not root.is_dir():
        raise PackError("Pack must be a regular directory")
    return root


def _safe_build_target(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path)
    if ".." in candidate.parts:
        raise PackError("Pack target must not contain parent traversal")
    target = candidate.absolute()
    _reject_symlink_components(target.parent)
    if target.is_symlink():
        raise PackError("Pack target must not be a symlink")
    return target


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise PackError("Pack path must not contain symlinks")
        if current.exists() and current != path and not current.is_dir():
            raise PackError("Pack path contains a non-directory component")


def _require_exact_keys(value: Any, keys: set[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise PackError(f"{label} fields are not closed")


def _require_unique_ids(values: Iterable[Mapping[str, Any]], field: str) -> None:
    identifiers = [item[field] for item in values]
    if len(identifiers) != len(set(identifiers)):
        raise PackError(f"duplicate {field}")


def _acquire_lock(path: Path) -> int:
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PackError("another build owns the Pack target") from exc


def _rename_no_replace(source: Path, target: Path) -> None:
    """Atomically rename a directory without replacing any existing target."""

    if target.exists() or target.is_symlink():
        raise FileExistsError("Pack target already exists")
    if os.name == "posix":
        libc = ctypes.CDLL(None, use_errno=True)
        if hasattr(libc, "renameat2"):
            renameat2 = libc.renameat2
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            if renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1) == 0:
                return
            code = ctypes.get_errno()
            if code not in {errno.ENOSYS, errno.EINVAL}:
                if code == errno.EEXIST:
                    raise FileExistsError("Pack target already exists")
                raise OSError(code, os.strerror(code), target)
        if hasattr(libc, "renamex_np"):
            renamex_np = libc.renamex_np
            renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            renamex_np.restype = ctypes.c_int
            if renamex_np(os.fsencode(source), os.fsencode(target), 4) == 0:
                return
            code = ctypes.get_errno()
            if code not in {errno.ENOSYS, errno.EINVAL}:
                if code == errno.EEXIST:
                    raise FileExistsError("Pack target already exists")
                raise OSError(code, os.strerror(code), target)
        raise PackError("platform lacks atomic no-replace directory rename")
    os.rename(source, target)


def _receipt(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "contract_version": manifest["contract_version"],
        "workspace_id": manifest["workspace_id"],
        "pack_id": manifest["pack_id"],
        "manifest_digest": manifest["manifest_digest"],
        "source_count": manifest["source_count"],
        "entry_count": manifest["entry_count"],
    }


__all__ = [
    "PACK_CONTRACT",
    "PACK_ENTRY_CONTRACT",
    "PACK_FORMAT",
    "PACK_SOURCES_CONTRACT",
    "PackError",
    "build_pack",
    "build_workspace_pack",
    "find_memory",
    "get_entry",
    "iter_entries",
    "load_pack_manifest",
    "pack_identity",
    "verify_pack",
]

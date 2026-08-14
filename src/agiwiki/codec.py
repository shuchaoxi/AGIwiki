"""Deterministic JSON and bounded local-file primitives.

Canonical JSON is the identity boundary for Workspace entries and Memory
Packs.  Readers deliberately reject duplicate keys, non-finite numbers,
symlinks and files that change while they are being read.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping


DEFAULT_MAX_JSON_BYTES = 8 * 1024 * 1024


class JSONDocumentError(ValueError):
    """A JSON document cannot cross the AGIWiki trust boundary."""


def canonical_json(value: Any) -> str:
    """Return the stable UTF-8 JSON representation used for identities."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise JSONDocumentError("value is not canonical JSON") from exc


def canonical_json_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def sha256_digest(value: Any) -> str:
    """Digest bytes directly and all other values as canonical JSON."""

    payload = value if isinstance(value, bytes) else canonical_json_bytes(value)
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def stable_id(prefix: str, value: Any, *, length: int = 32) -> str:
    if (
        not isinstance(prefix, str)
        or not prefix
        or not prefix.replace("_", "").isalnum()
        or not 16 <= length <= 64
    ):
        raise JSONDocumentError("stable ID parameters are invalid")
    return f"{prefix}_{sha256_digest(value).removeprefix('sha256:')[:length]}"


def load_json_document(
    path: str | os.PathLike[str],
    *,
    max_bytes: int = DEFAULT_MAX_JSON_BYTES,
) -> dict[str, Any]:
    """Read one bounded, stable, regular UTF-8 JSON object.

    The descriptor is opened without following the final symlink where the
    platform supports it.  File identity is checked before and after the read,
    and the pathname must still designate that same object.
    """

    if type(max_bytes) is not int or max_bytes < 2:
        raise JSONDocumentError("max_bytes must be an integer greater than one")
    candidate = Path(path)
    if ".." in candidate.parts:
        raise JSONDocumentError("JSON path must not contain parent traversal")
    target = candidate.absolute()
    _reject_symlink_components(target)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise JSONDocumentError("JSON document could not be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise JSONDocumentError("JSON document is not a bounded regular file")
        payload = bytearray()
        while len(payload) <= max_bytes:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        try:
            current = os.stat(target, follow_symlinks=False)
        except OSError as exc:
            raise JSONDocumentError("JSON document path changed while reading") from exc
        if not _same_file(before, after) or not _same_file(after, current):
            raise JSONDocumentError("JSON document changed while reading")
    finally:
        os.close(descriptor)
    if len(payload) > max_bytes:
        raise JSONDocumentError("JSON document exceeds the size limit")
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_closed_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JSONDocumentError("JSON document is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise JSONDocumentError("JSON document root must be an object")
    canonical_json(value)
    return value


def write_json_new(path: str | os.PathLike[str], value: Mapping[str, Any]) -> None:
    """Create one owner-only canonical JSON file without replacing a target."""

    target = Path(path)
    if target.is_symlink():
        raise FileExistsError("JSON output already exists as a symlink")
    payload = canonical_json_bytes(dict(value)) + b"\n"
    try:
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except OSError:
        raise
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("canonical JSON write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def file_sha256(path: str | os.PathLike[str]) -> str:
    """Hash one stable regular file without following its final symlink."""

    candidate = Path(path)
    if ".." in candidate.parts:
        raise JSONDocumentError("file path must not contain parent traversal")
    target = candidate.absolute()
    _reject_symlink_components(target)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise JSONDocumentError("file could not be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise JSONDocumentError("file to hash must be regular")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        try:
            current = os.stat(target, follow_symlinks=False)
        except OSError as exc:
            raise JSONDocumentError("file path changed while hashing") from exc
        if not _same_file(before, after) or not _same_file(after, current):
            raise JSONDocumentError("file changed while hashing")
        return f"sha256:{digest.hexdigest()}"
    finally:
        os.close(descriptor)


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JSONDocumentError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise JSONDocumentError(f"non-finite JSON number is forbidden: {value}")


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise JSONDocumentError("file path must not contain symlinks")


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        stat.S_ISREG(first.st_mode)
        and stat.S_ISREG(second.st_mode)
        and first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_size == second.st_size
        and first.st_mtime_ns == second.st_mtime_ns
    )


__all__ = [
    "DEFAULT_MAX_JSON_BYTES",
    "JSONDocumentError",
    "canonical_json",
    "canonical_json_bytes",
    "file_sha256",
    "load_json_document",
    "sha256_digest",
    "stable_id",
    "write_json_new",
]

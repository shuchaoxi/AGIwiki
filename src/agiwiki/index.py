"""Disposable SQLite FTS cache for verified AGIWiki Memory Packs."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .codec import canonical_json, sha256_digest
from .pack import PackError, iter_entries, verify_pack

INDEX_CONTRACT = "agiwiki.memory-index.v1"
TOKENIZER_TRIGRAM = "trigram"
TOKENIZER_FALLBACK = "unicode61"
_QUERY_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:+-]*|[\u3400-\u9fff]+")
_ENGLISH_STOPWORDS = {
    "a",
    "an",
    "and",
    "can",
    "do",
    "for",
    "how",
    "is",
    "of",
    "please",
    "safe",
    "safely",
    "the",
    "to",
    "verify",
    "what",
    "when",
    "where",
    "which",
    "with",
}
_CJK_STOPWORDS = {
    "并",
    "了",
    "为",
    "于",
    "及",
    "在",
    "把",
    "或",
    "的",
    "与",
    "一下",
    "什么",
    "可以",
    "如何",
    "怎么",
    "怎样",
    "是否",
    "有关",
    "相关",
    "能否",
    "请问",
    "这个",
    "那个",
    "进行",
    "操作",
    "方法",
    "步骤",
    "问题",
}


class IndexError(ValueError):
    """A disposable search index is invalid or cannot be used safely."""


def build_index(
    pack_path: str | os.PathLike[str],
    index_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Create a Pack-bound cache without replacing an existing file."""

    manifest = verify_pack(pack_path)
    target = _safe_index_target(index_path)
    if target.exists() or target.is_symlink():
        metadata = verify_index(pack_path, target)
        return {**metadata, "status": "ok", "replayed": True}
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlink_components(target.parent.absolute())
    temporary = _temporary_index(target.parent, target.name)
    try:
        metadata = _create_index(pack_path, manifest, temporary)
        _link_no_replace(temporary, target)
        os.chmod(target, 0o600)
        return {**metadata, "status": "ok", "replayed": False}
    except (OSError, sqlite3.Error, PackError, ValueError) as exc:
        if isinstance(exc, (FileExistsError, IndexError)):
            raise
        raise IndexError("search index build failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def rebuild_index(
    pack_path: str | os.PathLike[str],
    index_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Atomically replace a disposable cache with one for the current Pack."""

    manifest = verify_pack(pack_path)
    target = _safe_index_target(index_path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlink_components(target.parent.absolute())
    if target.is_symlink():
        raise IndexError("index target must not be a symlink")
    temporary = _temporary_index(target.parent, target.name)
    try:
        metadata = _create_index(pack_path, manifest, temporary)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        return {**metadata, "status": "ok", "replayed": False}
    except (OSError, sqlite3.Error, PackError, ValueError) as exc:
        if isinstance(exc, IndexError):
            raise
        raise IndexError("search index rebuild failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def ensure_index(
    pack_path: str | os.PathLike[str],
    index_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Return valid metadata, creating or replacing only disposable cache."""

    target = _safe_index_target(index_path)
    if not target.exists() and not target.is_symlink():
        try:
            result = build_index(pack_path, target)
            return _metadata_projection(result)
        except FileExistsError:
            # A concurrent reader won the no-clobber publication race.  The
            # winner is still verified before it can be reused.
            return verify_index(pack_path, target)
    try:
        return verify_index(pack_path, target)
    except (IndexError, PackError, sqlite3.Error):
        return _metadata_projection(rebuild_index(pack_path, target))


def verify_index(
    pack_path: str | os.PathLike[str],
    index_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Require index metadata and every FTS row to match current Pack JSON."""

    manifest = verify_pack(pack_path)
    target = _safe_existing_index(index_path)
    # Re-authenticate the JSON snapshot used to derive rows.  The index is a
    # cache, so correctness is more important than avoiding a second bounded
    # Pack walk at this build-time boundary.
    expected_entries = iter_entries(pack_path, verify=False)
    with (
        _database_snapshot(target) as snapshot,
        _readonly_database(snapshot) as connection,
    ):
        return _verify_connection(connection, manifest, expected_entries)


def find_memory(
    pack_path: str | os.PathLike[str],
    index_path: str | os.PathLike[str],
    query: str,
    *,
    limit: int = 8,
) -> dict[str, Any]:
    """Search one verified Pack through its verified disposable index."""

    normalized = _query(query)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise IndexError("limit must be between 1 and 50")
    manifest = verify_pack(pack_path)
    entries = iter_entries(pack_path, verify=False)
    target = _safe_existing_index(index_path)
    with (
        _database_snapshot(target) as snapshot,
        _readonly_database(snapshot) as connection,
    ):
        metadata = _verify_connection(connection, manifest, entries)
        fallback_ranked = False
        try:
            if metadata["tokenizer"] == TOKENIZER_TRIGRAM and len(normalized) < 3:
                escaped = (
                    normalized.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                rows = connection.execute(
                    """
                        SELECT entry_version_id,entry_id,title,summary,kind,0.0
                        FROM entry_fts
                        WHERE search_text LIKE ? ESCAPE '\\'
                        ORDER BY entry_id LIMIT ?
                        """,
                    (f"%{escaped}%", limit),
                ).fetchall()
            else:
                phrase = '"' + normalized.replace('"', '""') + '"'
                rows = connection.execute(
                    """
                        SELECT entry_version_id,entry_id,title,summary,kind,
                               bm25(entry_fts)
                        FROM entry_fts WHERE entry_fts MATCH ?
                        ORDER BY bm25(entry_fts),entry_id LIMIT ?
                        """,
                    (phrase, limit),
                ).fetchall()
            if not rows:
                rows = _fallback_search(connection, normalized, limit=limit)
                fallback_ranked = True
        except sqlite3.Error as exc:
            raise IndexError("search query failed") from exc
    return {
        "contract_version": "agiwiki.memory-search.v1",
        "pack_id": metadata["pack_id"],
        "manifest_digest": metadata["manifest_digest"],
        "query_digest": sha256_digest(normalized),
        "tokenizer": metadata["tokenizer"],
        "count": len(rows),
        "results": [
            {
                "entry_version_id": row[0],
                "entry_id": row[1],
                "title": row[2],
                "summary": row[3],
                "kind": row[4],
                "score": float(-row[5] if fallback_ranked else row[5]),
            }
            for row in rows
        ],
    }


def _create_index(
    pack_path: str | os.PathLike[str],
    manifest: Mapping[str, Any],
    target: Path,
) -> dict[str, Any]:
    entries = iter_entries(pack_path, verify=False)
    tokenizer = TOKENIZER_TRIGRAM
    try:
        _populate_index(target, manifest, entries, tokenizer=tokenizer)
    except sqlite3.OperationalError as exc:
        if "tokenizer" not in str(exc).lower() and "trigram" not in str(exc).lower():
            raise
        target.unlink(missing_ok=True)
        tokenizer = TOKENIZER_FALLBACK
        _populate_index(target, manifest, entries, tokenizer=tokenizer)
    metadata = verify_index(pack_path, target)
    return metadata


def _populate_index(
    target: Path,
    manifest: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    *,
    tokenizer: str,
) -> None:
    connection = sqlite3.connect(target)
    try:
        connection.executescript(
            f"""
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=FULL;
            CREATE TABLE index_meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE VIRTUAL TABLE entry_fts USING fts5(
                entry_version_id UNINDEXED,
                entry_id UNINDEXED,
                title,
                summary,
                kind UNINDEXED,
                search_text,
                tokenize='{tokenizer}'
            );
            """
        )
        metadata = _index_metadata(manifest, tokenizer=tokenizer)
        connection.execute(
            "INSERT INTO index_meta(key,value) VALUES('manifest',?)",
            (canonical_json(metadata),),
        )
        connection.executemany(
            """
            INSERT INTO entry_fts(
                entry_version_id,entry_id,title,summary,kind,search_text
            ) VALUES(?,?,?,?,?,?)
            """,
            (_search_row(item) for item in entries),
        )
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise IndexError("new search index failed integrity check")
    finally:
        connection.close()
    os.chmod(target, 0o600)


def _index_metadata(manifest: Mapping[str, Any], *, tokenizer: str) -> dict[str, Any]:
    return {
        "contract_version": INDEX_CONTRACT,
        "workspace_id": manifest["workspace_id"],
        "pack_id": manifest["pack_id"],
        "manifest_digest": manifest["manifest_digest"],
        "entry_count": manifest["entry_count"],
        "tokenizer": tokenizer,
    }


def _verify_connection(
    connection: sqlite3.Connection,
    manifest: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        metadata_row = connection.execute(
            "SELECT value FROM index_meta WHERE key='manifest'"
        ).fetchone()
        rows = connection.execute(
            """
            SELECT entry_version_id,entry_id,title,summary,kind,search_text
            FROM entry_fts ORDER BY entry_id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise IndexError("search index schema is invalid") from exc
    if integrity != ("ok",) or metadata_row is None:
        raise IndexError("search index integrity check failed")
    try:
        metadata = json.loads(metadata_row[0])
    except (TypeError, json.JSONDecodeError) as exc:
        raise IndexError("search index metadata is invalid") from exc
    if canonical_json(metadata) != metadata_row[0]:
        raise IndexError("search index metadata is not canonical JSON")
    _validate_metadata(metadata)
    expected_metadata = _index_metadata(
        manifest,
        tokenizer=metadata["tokenizer"],
    )
    expected_rows = sorted(
        (_search_row(item) for item in entries), key=lambda row: row[1]
    )
    if metadata != expected_metadata or rows != expected_rows:
        raise IndexError("search index does not match Pack content")
    return metadata


def _validate_metadata(value: Any) -> None:
    keys = {
        "contract_version",
        "workspace_id",
        "pack_id",
        "manifest_digest",
        "entry_count",
        "tokenizer",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise IndexError("search index metadata fields are not closed")
    if value["contract_version"] != INDEX_CONTRACT:
        raise IndexError("search index contract is unsupported")
    if value["tokenizer"] not in {TOKENIZER_TRIGRAM, TOKENIZER_FALLBACK}:
        raise IndexError("search index tokenizer is unsupported")
    for key in ("workspace_id", "pack_id", "manifest_digest"):
        if not isinstance(value[key], str) or not value[key]:
            raise IndexError(f"search index {key} is invalid")
    if type(value["entry_count"]) is not int or value["entry_count"] < 1:
        raise IndexError("search index entry_count is invalid")


def _search_row(envelope: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    entry = envelope["entry"]
    search_text = "\n".join(
        (
            entry["title"],
            entry["summary"],
            " ".join(entry["keywords"]),
            " ".join(entry["applies_to"]),
            canonical_json(entry["content"]),
        )
    )
    return (
        envelope["entry_version_id"],
        entry["entry_id"],
        entry["title"],
        entry["summary"],
        entry["kind"],
        search_text,
    )


def _query(value: Any) -> str:
    if not isinstance(value, str):
        raise IndexError("query must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > 1000 or "\x00" in normalized:
        raise IndexError("query must contain 1 to 1000 safe characters")
    return normalized


def _fallback_search(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
) -> list[tuple[Any, ...]]:
    """Recall partial natural-language matches after exact FTS phrase failure."""

    terms = _fallback_terms(query)
    if not terms:
        return []
    score_parts: list[str] = []
    score_parameters: list[str] = []
    where_parts: list[str] = []
    where_parameters: list[str] = []
    match_parts: list[str] = []
    match_parameters: list[str] = []
    for term in terms:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        score_parts.append(
            "(CASE WHEN title LIKE ? ESCAPE '\\' THEN 8 ELSE 0 END + "
            "CASE WHEN summary LIKE ? ESCAPE '\\' THEN 4 ELSE 0 END + "
            "CASE WHEN search_text LIKE ? ESCAPE '\\' THEN 1 ELSE 0 END)"
        )
        score_parameters.extend((pattern, pattern, pattern))
        where_parts.append("search_text LIKE ? ESCAPE '\\'")
        where_parameters.append(pattern)
        match_parts.append("CASE WHEN search_text LIKE ? ESCAPE '\\' THEN 1 ELSE 0 END")
        match_parameters.append(pattern)
    minimum_matches = 1 if len(terms) == 1 else math.ceil(len(terms) * 0.6)
    statement = f"""
        SELECT entry_version_id,entry_id,title,summary,kind,
               ({" + ".join(score_parts)}) AS relevance
        FROM entry_fts
        WHERE ({" OR ".join(where_parts)})
          AND ({" + ".join(match_parts)}) >= ?
        ORDER BY relevance DESC,entry_id
        LIMIT ?
    """
    return connection.execute(
        statement,
        (
            *score_parameters,
            *where_parameters,
            *match_parameters,
            minimum_matches,
            limit,
        ),
    ).fetchall()


def _fallback_terms(query: str) -> tuple[str, ...]:
    terms: list[str] = []
    for match in _QUERY_PART.finditer(query):
        value = match.group(0)
        if "\u3400" <= value[0] <= "\u9fff":
            terms.extend(_cjk_fallback_terms(value))
        elif len(value) >= 2:
            folded = value.casefold()
            if folded not in _ENGLISH_STOPWORDS:
                terms.append(folded)
    unique = tuple(dict.fromkeys(terms))[:32]
    return unique


def _cjk_fallback_terms(value: str) -> tuple[str, ...]:
    cleaned = value
    for stopword in sorted(_CJK_STOPWORDS, key=len, reverse=True):
        cleaned = cleaned.replace(stopword, " ")
    terms: list[str] = []
    for segment in cleaned.split():
        if len(segment) < 2:
            continue
        terms.append(segment)
        if len(segment) > 3:
            terms.extend(
                segment[index : index + 2] for index in range(len(segment) - 1)
            )
    return tuple(terms)


def _safe_index_target(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path)
    if ".." in candidate.parts:
        raise IndexError("index path must not contain parent traversal")
    target = candidate.absolute()
    _reject_symlink_components(target.parent)
    if target.is_symlink():
        raise IndexError("index path must not be a symlink")
    return target


def _safe_existing_index(path: str | os.PathLike[str]) -> Path:
    target = _safe_index_target(path)
    try:
        current = os.stat(target, follow_symlinks=False)
    except OSError as exc:
        raise IndexError("search index is missing") from exc
    if not stat.S_ISREG(current.st_mode):
        raise IndexError("search index must be a regular file")
    return target


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise IndexError("index path must not contain symlinks")
        if current.exists() and current != path and not current.is_dir():
            raise IndexError("index path contains a non-directory component")


def _temporary_index(parent: Path, name: str) -> Path:
    descriptor, raw = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=parent)
    os.close(descriptor)
    target = Path(raw)
    target.unlink()
    return target


def _link_no_replace(source: Path, target: Path) -> None:
    try:
        os.link(source, target, follow_symlinks=False)
    except FileExistsError as exc:
        raise FileExistsError("search index target already exists") from exc


@contextmanager
def _readonly_database(path: Path) -> Iterator[sqlite3.Connection]:
    uri = f"file:{quote(str(path))}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise IndexError("search index cannot be opened read-only") from exc
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def _database_snapshot(path: Path) -> Iterator[Path]:
    """Pin and copy one stable cache file before SQLite parses its bytes."""

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        source = os.open(path, flags)
    except OSError as exc:
        raise IndexError("search index could not be opened safely") from exc
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        before = os.fstat(source)
        if not stat.S_ISREG(before.st_mode):
            raise IndexError("search index must be a regular file")
        temporary = tempfile.TemporaryDirectory(prefix="agiwiki-index-")
        temporary_root = Path(temporary.name)
        os.chmod(temporary_root, 0o700)
        snapshot = temporary_root / "search.sqlite"
        output = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            while chunk := os.read(source, 1024 * 1024):
                remaining = memoryview(chunk)
                while remaining:
                    written = os.write(output, remaining)
                    if written <= 0:
                        raise IndexError("search index snapshot write made no progress")
                    remaining = remaining[written:]
            os.fsync(output)
        finally:
            os.close(output)
        after = os.fstat(source)
        try:
            current = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise IndexError("search index path changed while reading") from exc
        if not _same_file(before, after) or not _same_file(after, current):
            raise IndexError("search index changed while reading")
        yield snapshot
    finally:
        os.close(source)
        if temporary is not None:
            temporary.cleanup()


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        stat.S_ISREG(first.st_mode)
        and stat.S_ISREG(second.st_mode)
        and first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_size == second.st_size
        and first.st_mtime_ns == second.st_mtime_ns
    )


def _metadata_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in (
            "contract_version",
            "workspace_id",
            "pack_id",
            "manifest_digest",
            "entry_count",
            "tokenizer",
        )
    }


__all__ = [
    "INDEX_CONTRACT",
    "TOKENIZER_FALLBACK",
    "TOKENIZER_TRIGRAM",
    "IndexError",
    "build_index",
    "ensure_index",
    "find_memory",
    "rebuild_index",
    "verify_index",
]

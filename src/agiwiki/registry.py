"""Small transactional registry for installed and active Memory Packs."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Iterator, Mapping, Sequence

from .codec import canonical_json
from .paths import HomePaths, initialize_home_paths, require_private_regular_file


SCHEMA_VERSION = 1


class RegistryError(ValueError):
    """Registry state violates an exact installation or activation invariant."""


class HomeRegistry:
    def __init__(self, paths: HomePaths):
        self.paths = paths

    def initialize(self, *, home_id: str | None = None) -> dict[str, Any]:
        initialize_home_paths(self.paths)
        database = self.paths.registry_db
        if not database.exists():
            descriptor = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        if database.is_symlink():
            raise RegistryError("registry database must not be a symlink")
        os.chmod(database, 0o600)
        with self._connect(initializing=True) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS home_meta(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    schema_version INTEGER NOT NULL,
                    home_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS workspace_releases(
                    pack_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    health TEXT NOT NULL CHECK(health IN ('OK','BROKEN')),
                    UNIQUE(pack_id,workspace_id)
                );
                CREATE TABLE IF NOT EXISTS activations(
                    scope_type TEXT NOT NULL CHECK(scope_type IN ('GLOBAL','PROJECT')),
                    scope_key TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    pack_id TEXT NOT NULL,
                    PRIMARY KEY(scope_type,scope_key,workspace_id),
                    FOREIGN KEY(pack_id,workspace_id)
                        REFERENCES workspace_releases(pack_id,workspace_id)
                );
                CREATE TABLE IF NOT EXISTS projects(
                    project_id TEXT PRIMARY KEY,
                    marker_digest TEXT NOT NULL,
                    pack_ids_json TEXT NOT NULL
                );
                """
            )
            row = connection.execute(
                "SELECT schema_version,home_id FROM home_meta WHERE singleton=1"
            ).fetchone()
            if row is None:
                identifier = home_id or f"home_{secrets.token_hex(16)}"
                connection.execute(
                    "INSERT INTO home_meta(singleton,schema_version,home_id) VALUES(1,?,?)",
                    (SCHEMA_VERSION, identifier),
                )
                row = (SCHEMA_VERSION, identifier)
            elif home_id is not None and row[1] != home_id:
                raise RegistryError("existing Home identity conflicts with requested identity")
            if row[0] != SCHEMA_VERSION:
                raise RegistryError("registry schema version is unsupported")
            return {"schema_version": row[0], "home_id": row[1]}

    def metadata(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT schema_version,home_id FROM home_meta WHERE singleton=1"
            ).fetchone()
        if row is None or row[0] != SCHEMA_VERSION:
            raise RegistryError("registry metadata is missing or unsupported")
        return {"schema_version": row[0], "home_id": row[1]}

    def insert_release(self, release: Mapping[str, Any]) -> dict[str, Any]:
        normalized = _release(release)
        with self._connect(write=True) as connection:
            existing = connection.execute(
                "SELECT * FROM workspace_releases WHERE pack_id=?",
                (normalized["pack_id"],),
            ).fetchone()
            if existing is not None:
                value = dict(existing)
                if value != normalized:
                    raise RegistryError("installed Pack identity conflicts with registry")
                return value
            connection.execute(
                """
                INSERT INTO workspace_releases(
                    pack_id,workspace_id,version,manifest_digest,relative_path,health
                ) VALUES(:pack_id,:workspace_id,:version,:manifest_digest,:relative_path,:health)
                """,
                normalized,
            )
        return normalized

    def get_release(self, pack_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspace_releases WHERE pack_id=?", (pack_id,)
            ).fetchone()
        return None if row is None else dict(row)

    def list_releases(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM workspace_releases"
        params: tuple[Any, ...] = ()
        if workspace_id is not None:
            query += " WHERE workspace_id=?"
            params = (workspace_id,)
        query += " ORDER BY workspace_id,version,pack_id"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, params)]

    def set_health(self, pack_id: str, health: str) -> None:
        if health not in {"OK", "BROKEN"}:
            raise RegistryError("release health is invalid")
        with self._connect(write=True) as connection:
            changed = connection.execute(
                "UPDATE workspace_releases SET health=? WHERE pack_id=?",
                (health, pack_id),
            ).rowcount
            if changed != 1:
                raise RegistryError("release is not registered")
            if health == "BROKEN":
                connection.execute("DELETE FROM activations WHERE pack_id=?", (pack_id,))

    def activate_exact(
        self,
        *,
        pack_id: str,
        scope_type: str = "GLOBAL",
        scope_key: str = "global",
    ) -> dict[str, Any]:
        scope_type, scope_key = _scope(scope_type, scope_key)
        with self._connect(write=True) as connection:
            release = connection.execute(
                "SELECT * FROM workspace_releases WHERE pack_id=?", (pack_id,)
            ).fetchone()
            if release is None or release["health"] != "OK":
                raise RegistryError("only a healthy installed Pack can be activated")
            connection.execute(
                """
                INSERT INTO activations(scope_type,scope_key,workspace_id,pack_id)
                VALUES(?,?,?,?)
                ON CONFLICT(scope_type,scope_key,workspace_id)
                DO UPDATE SET pack_id=excluded.pack_id
                """,
                (scope_type, scope_key, release["workspace_id"], pack_id),
            )
            return {
                "scope_type": scope_type,
                "scope_key": scope_key,
                "workspace_id": release["workspace_id"],
                "pack_id": pack_id,
            }

    def deactivate_exact(
        self,
        *,
        pack_id: str,
        scope_type: str = "GLOBAL",
        scope_key: str = "global",
    ) -> bool:
        scope_type, scope_key = _scope(scope_type, scope_key)
        with self._connect(write=True) as connection:
            changed = connection.execute(
                "DELETE FROM activations WHERE scope_type=? AND scope_key=? AND pack_id=?",
                (scope_type, scope_key, pack_id),
            ).rowcount
        return changed == 1

    def list_activations(
        self,
        *,
        scope_type: str | None = None,
        scope_key: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[str] = []
        if scope_type is not None:
            normalized, _ = _scope(scope_type, scope_key or "global")
            clauses.append("a.scope_type=?")
            params.append(normalized)
        if scope_key is not None:
            if not scope_key or len(scope_key) > 256:
                raise RegistryError("scope_key is invalid")
            clauses.append("a.scope_key=?")
            params.append(scope_key)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.scope_type,a.scope_key,a.workspace_id,a.pack_id,
                       r.version,r.manifest_digest,r.relative_path,r.health
                FROM activations a JOIN workspace_releases r USING(pack_id,workspace_id)
                """
                + where
                + " ORDER BY a.scope_type,a.scope_key,a.workspace_id",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_project_pins(
        self,
        *,
        project_id: str,
        marker_digest: str,
        pack_ids: Sequence[str],
    ) -> dict[str, Any]:
        if not project_id or len(project_id) > 256:
            raise RegistryError("project_id is invalid")
        unique = sorted(set(pack_ids))
        if not unique or len(unique) != len(pack_ids):
            raise RegistryError("project Pack pins must be non-empty and unique")
        pins_json = canonical_json(unique)
        with self._connect(write=True) as connection:
            placeholders = ",".join("?" for _ in unique)
            rows = connection.execute(
                f"SELECT pack_id,health FROM workspace_releases WHERE pack_id IN ({placeholders})",
                tuple(unique),
            ).fetchall()
            if {row["pack_id"] for row in rows} != set(unique) or any(
                row["health"] != "OK" for row in rows
            ):
                raise RegistryError("project pins require healthy installed Packs")
            connection.execute(
                """
                INSERT INTO projects(project_id,marker_digest,pack_ids_json)
                VALUES(?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET
                  marker_digest=excluded.marker_digest,
                  pack_ids_json=excluded.pack_ids_json
                """,
                (project_id, marker_digest, pins_json),
            )
        return {
            "project_id": project_id,
            "marker_digest": marker_digest,
            "pack_ids": unique,
        }

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT project_id,marker_digest,pack_ids_json FROM projects "
                "WHERE project_id=?",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            pack_ids = json.loads(row["pack_ids_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RegistryError("project Pack pins are invalid") from exc
        if canonical_json(pack_ids) != row["pack_ids_json"]:
            raise RegistryError("project Pack pins are not canonical")
        return {
            "project_id": row["project_id"],
            "marker_digest": row["marker_digest"],
            "pack_ids": pack_ids,
        }

    @contextmanager
    def _connect(
        self,
        *,
        write: bool = False,
        initializing: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        if not initializing:
            require_private_regular_file(self.paths.registry_db)
        connection = sqlite3.connect(self.paths.registry_db)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise RegistryError("SQLite foreign keys are unavailable")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write or initializing:
                connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _release(value: Mapping[str, Any]) -> dict[str, str]:
    keys = {"pack_id", "workspace_id", "version", "manifest_digest", "relative_path", "health"}
    if set(value) != keys:
        raise RegistryError("release fields are not closed")
    result = {key: str(value[key]) for key in keys}
    if result["health"] not in {"OK", "BROKEN"}:
        raise RegistryError("release health is invalid")
    relative = Path(result["relative_path"])
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise RegistryError("release path must be relative and contained")
    if any(not result[key] for key in keys):
        raise RegistryError("release fields must be non-empty")
    return result


def _scope(scope_type: str, scope_key: str) -> tuple[str, str]:
    normalized = scope_type.upper()
    if normalized not in {"GLOBAL", "PROJECT"}:
        raise RegistryError("scope_type must be GLOBAL or PROJECT")
    if not isinstance(scope_key, str) or not scope_key or len(scope_key) > 256:
        raise RegistryError("scope_key is invalid")
    return normalized, scope_key


__all__ = ["HomeRegistry", "RegistryError", "SCHEMA_VERSION"]

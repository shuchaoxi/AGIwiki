"""Local transactional ledger for explicitly authored Adaptive Memory."""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import hmac
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .adaptive_contracts import (
    AdaptiveContractError,
    microseconds_to_timestamp,
    normalize_adaptive_correction,
    normalize_adaptive_write,
    normalize_review_application,
    normalize_review_decision,
    review_candidate_id,
    timestamp_to_microseconds,
    validate_adaptive_record,
    validate_review_application_receipt,
    validate_review_due,
    validate_review_proposal,
    validate_review_receipt,
)
from .codec import (
    canonical_json,
    load_json_document,
    sha256_digest,
    write_json_new,
)
from .paths import (
    HomePaths,
    initialize_home_paths,
    require_private_regular_file,
)

ADAPTIVE_SCHEMA_VERSION = 5
_LEGACY_ADAPTIVE_SCHEMA_VERSION = 1
_OPERATIONS_SCHEMA_VERSION = 2
_REVIEW_SCHEMA_VERSION = 3
_CAPABILITY_SCHEMA_VERSION = 4
_MEMORY_ID = re.compile(r"^mem_[0-9a-f]{32}$")
_OPERATION_ID = re.compile(r"^op_[0-9a-f]{32}$")
_PROPOSAL_ID = re.compile(r"^review_[0-9a-f]{32}$")
_DECISION_ID = re.compile(r"^decision_[0-9a-f]{32}$")
_APPLICATION_ID = re.compile(r"^application_[0-9a-f]{32}$")
_PRINCIPAL_ID = re.compile(r"^principal_[0-9a-f]{32}$")
_CAPABILITY_TOKEN = re.compile(r"^agwcap_[0-9a-f]{64}$")
_SCOPE_TYPES = {"user", "agent", "run", "workspace"}
_STATUSES = {"active", "superseded", "deleted"}
_PRINCIPAL_PERMISSIONS = {"review_propose", "review_approve", "review_apply"}


class AdaptiveMemoryError(ValueError):
    """Adaptive Memory state or an operation is invalid."""


class AdaptiveMemoryStore:
    def __init__(
        self,
        paths: HomePaths,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.paths = paths
        self._clock = clock or (lambda: datetime.now(UTC))

    def initialize(self, *, ledger_id: str | None = None) -> dict[str, Any]:
        database = self.paths.adaptive_db
        if database.exists() or database.is_symlink():
            metadata = _inspect_existing_database(
                database,
                accepted_versions={
                    _LEGACY_ADAPTIVE_SCHEMA_VERSION,
                    _OPERATIONS_SCHEMA_VERSION,
                    _REVIEW_SCHEMA_VERSION,
                    _CAPABILITY_SCHEMA_VERSION,
                    ADAPTIVE_SCHEMA_VERSION,
                },
            )
            if ledger_id is not None and metadata["ledger_id"] != ledger_id:
                raise AdaptiveMemoryError("existing adaptive ledger identity conflicts")
            if metadata["schema_version"] != ADAPTIVE_SCHEMA_VERSION:
                metadata = _migrate_to_current(database)
            return metadata

        initialize_home_paths(self.paths)
        created = False
        try:
            descriptor = os.open(
                database,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.close(descriptor)
            created = True
        except FileExistsError:
            metadata = _inspect_existing_database(
                database,
                accepted_versions={
                    _LEGACY_ADAPTIVE_SCHEMA_VERSION,
                    _OPERATIONS_SCHEMA_VERSION,
                    _REVIEW_SCHEMA_VERSION,
                    _CAPABILITY_SCHEMA_VERSION,
                    ADAPTIVE_SCHEMA_VERSION,
                },
            )
            if ledger_id is not None and metadata["ledger_id"] != ledger_id:
                raise AdaptiveMemoryError("existing adaptive ledger identity conflicts")
            if metadata["schema_version"] != ADAPTIVE_SCHEMA_VERSION:
                metadata = _migrate_to_current(database)
            return metadata
        try:
            with self._connect(initializing=True) as connection:
                connection.executescript(_SCHEMA)
                identifier = ledger_id or f"ledger_{secrets.token_hex(16)}"
                connection.execute(
                    "INSERT INTO ledger_meta(singleton,schema_version,ledger_id) "
                    "VALUES(1,?,?)",
                    (ADAPTIVE_SCHEMA_VERSION, identifier),
                )
                return {
                    "schema_version": ADAPTIVE_SCHEMA_VERSION,
                    "ledger_id": identifier,
                }
        except (sqlite3.Error, AdaptiveMemoryError) as exc:
            if created:
                database.unlink(missing_ok=True)
            raise AdaptiveMemoryError(
                "adaptive database initialization failed"
            ) from exc

    def metadata(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT schema_version,ledger_id FROM ledger_meta WHERE singleton=1"
            ).fetchone()
        if row is None or row["schema_version"] != ADAPTIVE_SCHEMA_VERSION:
            raise AdaptiveMemoryError("adaptive database metadata is unsupported")
        return {
            "schema_version": row["schema_version"],
            "ledger_id": row["ledger_id"],
        }

    def create_principal(
        self,
        *,
        token: str,
        permissions: list[str],
        confirm: bool = False,
        principal_id: str | None = None,
    ) -> dict[str, Any]:
        """Enroll one local capability without returning or storing its raw token."""

        if confirm is not True:
            raise AdaptiveMemoryError(
                "principal creation requires explicit confirmation"
            )
        identifier = _principal_id(principal_id)
        capability = _capability_token(token)
        normalized_permissions = _permissions(permissions)
        now = self._now_timestamp()
        token_hash = sha256_digest(capability.encode("utf-8"))
        with self._connect(write=True) as connection:
            existing = connection.execute(
                "SELECT token_hash,permissions_json,status,created_at_us "
                "FROM adaptive_principals WHERE principal_id=?",
                (identifier,),
            ).fetchone()
            replayed = existing is not None
            if existing is not None:
                if not hmac.compare_digest(
                    existing["token_hash"], token_hash
                ) or existing["permissions_json"] != canonical_json(
                    normalized_permissions
                ):
                    raise AdaptiveMemoryError(
                        "principal_id is already bound to another capability"
                    )
                created_at = microseconds_to_timestamp(existing["created_at_us"])
                status = existing["status"]
            else:
                connection.execute(
                    "INSERT INTO adaptive_principals("
                    "principal_id,token_hash,permissions_json,status,created_at_us,"
                    "revoked_at_us) VALUES(?,?,?,'active',?,NULL)",
                    (
                        identifier,
                        token_hash,
                        canonical_json(normalized_permissions),
                        timestamp_to_microseconds(now),
                    ),
                )
                created_at = now
                status = "active"
        return {
            "contract_version": "agiwiki.local-principal.v1",
            "ok": True,
            "principal_id": identifier,
            "permissions": normalized_permissions,
            "status": status,
            "created_at": created_at,
            "replayed": replayed,
            "raw_token_stored_in_ledger": False,
        }

    def revoke_principal(
        self,
        principal_id: str,
        *,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Revoke future use of one capability under the local OS-owner boundary."""

        identifier = _principal_id(principal_id, generate=False)
        if confirm is not True:
            raise AdaptiveMemoryError(
                "principal revocation requires explicit confirmation"
            )
        now = self._now_timestamp()
        with self._connect(write=True) as connection:
            row = connection.execute(
                "SELECT status,revoked_at_us FROM adaptive_principals "
                "WHERE principal_id=?",
                (identifier,),
            ).fetchone()
            if row is None:
                raise AdaptiveMemoryError("principal was not found")
            replayed = row["status"] == "revoked"
            if replayed:
                revoked_at = microseconds_to_timestamp(row["revoked_at_us"])
            else:
                changed = connection.execute(
                    "UPDATE adaptive_principals SET status='revoked',revoked_at_us=? "
                    "WHERE principal_id=? AND status='active'",
                    (timestamp_to_microseconds(now), identifier),
                ).rowcount
                if changed != 1:
                    raise AdaptiveMemoryError("principal changed during revocation")
                revoked_at = now
        return {
            "contract_version": "agiwiki.local-principal-revocation.v1",
            "ok": True,
            "principal_id": identifier,
            "status": "revoked",
            "revoked_at": revoked_at,
            "replayed": replayed,
        }

    def remember(
        self,
        value: Mapping[str, Any],
        *,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        now = self._now()
        candidate = normalize_adaptive_write(value, now=now)
        selected_operation_id = _operation_id(operation_id)
        request_digest = _operation_request_digest(
            "remember",
            scope=candidate["scope"],
            value=value,
        )
        memory_id = f"mem_{secrets.token_hex(16)}"
        lineage_id = f"lineage_{secrets.token_hex(16)}"
        record = _new_record(
            candidate,
            memory_id=memory_id,
            lineage_id=lineage_id,
            revision=1,
            supersedes_memory_id=None,
            now=now,
        )
        with self._connect(write=True) as connection:
            replay = _replay_operation(
                connection,
                operation_id=selected_operation_id,
                operation_type="remember",
                scope=candidate["scope"],
                target_memory_id=None,
                request_digest=request_digest,
                now=now,
            )
            if replay is not None:
                return replay
            _insert_record(connection, record)
            _insert_event(connection, "remember", record, now=now)
            _insert_operation(
                connection,
                operation_id=selected_operation_id,
                operation_type="remember",
                scope=candidate["scope"],
                target_memory_id=None,
                request_digest=request_digest,
                result_memory_id=record["memory_id"],
                result_lineage_id=record["lineage_id"],
                result_revision_count=None,
                now=now,
            )
        return {
            "contract_version": "agiwiki.adaptive-remember.v1",
            "ok": True,
            "operation_id": selected_operation_id,
            "replayed": False,
            "memory": record,
        }

    def get(
        self,
        memory_id: str,
        *,
        scope_type: str,
        scope_key: str,
    ) -> dict[str, Any]:
        identifier = _memory_id(memory_id)
        scope_type, scope_key = _scope(scope_type, scope_key)
        now_us = timestamp_to_microseconds(self._now_timestamp())
        with self._connect() as connection:
            row = connection.execute(
                _SELECT
                + " WHERE memory_id=? AND scope_type=? AND scope_key=? "
                + _CURRENT,
                (identifier, scope_type, scope_key, now_us, now_us, now_us),
            ).fetchone()
        return {
            "contract_version": "agiwiki.adaptive-get.v1",
            "found": row is not None,
            **({"memory": _record(row)} if row is not None else {}),
        }

    def list(
        self,
        *,
        scope_type: str,
        scope_key: str,
        memory_class: str | None = None,
        status: str = "active",
        limit: int = 50,
        include_expired: bool = False,
    ) -> dict[str, Any]:
        scope_type, scope_key = _scope(scope_type, scope_key)
        if memory_class is not None and memory_class not in {"profile", "episode"}:
            raise AdaptiveMemoryError("memory_class is invalid")
        if status not in _STATUSES:
            raise AdaptiveMemoryError("status is invalid")
        if type(include_expired) is not bool or (
            include_expired and status != "active"
        ):
            raise AdaptiveMemoryError(
                "include_expired is only valid for active memories"
            )
        _limit(limit, maximum=100)
        clauses = ["scope_type=?", "scope_key=?", "status=?"]
        params: list[Any] = [scope_type, scope_key, status]
        if memory_class is not None:
            clauses.append("memory_class=?")
            params.append(memory_class)
        if status == "active":
            now_us = timestamp_to_microseconds(self._now_timestamp())
            clauses.append("valid_from_us<=?")
            params.append(now_us)
            if not include_expired:
                clauses.extend(
                    (
                        "(valid_to_us IS NULL OR valid_to_us>?)",
                        "(expires_at_us IS NULL OR expires_at_us>?)",
                    )
                )
                params.extend((now_us, now_us))
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                _SELECT
                + " WHERE "
                + " AND ".join(clauses)
                + " ORDER BY observed_at_us DESC,memory_id LIMIT ?",
                tuple(params),
            ).fetchall()
        memories = [_record(row) for row in rows]
        return {
            "contract_version": "agiwiki.adaptive-list.v1",
            "count": len(memories),
            "memories": memories,
        }

    def search(
        self,
        query: str,
        *,
        scope_type: str,
        scope_key: str,
        memory_class: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or "\x00" in query:
            raise AdaptiveMemoryError("query must contain safe text")
        normalized = query.strip()
        if len(normalized) > 1000:
            raise AdaptiveMemoryError("query is too long")
        scope_type, scope_key = _scope(scope_type, scope_key)
        if memory_class is not None and memory_class not in {"profile", "episode"}:
            raise AdaptiveMemoryError("memory_class is invalid")
        _limit(limit, maximum=50)
        now_us = timestamp_to_microseconds(self._now_timestamp())
        clauses = [
            "scope_type=?",
            "scope_key=?",
            "status='active'",
            "valid_from_us<=?",
            "(valid_to_us IS NULL OR valid_to_us>?)",
            "(expires_at_us IS NULL OR expires_at_us>?)",
            "instr(lower(content),lower(?))>0",
        ]
        params: list[Any] = [
            scope_type,
            scope_key,
            now_us,
            now_us,
            now_us,
            normalized,
        ]
        if memory_class is not None:
            clauses.append("memory_class=?")
            params.append(memory_class)
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                _SELECT
                + " WHERE "
                + " AND ".join(clauses)
                + " ORDER BY confidence_ppm DESC,observed_at_us DESC,memory_id LIMIT ?",
                tuple(params),
            ).fetchall()
        memories = [_record(row) for row in rows]
        return {
            "contract_version": "agiwiki.adaptive-search.v1",
            "query_digest": sha256_digest(normalized),
            "count": len(memories),
            "memories": memories,
        }

    def correct(
        self,
        memory_id: str,
        value: Mapping[str, Any],
        *,
        scope_type: str,
        scope_key: str,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        identifier = _memory_id(memory_id)
        scope_type, scope_key = _scope(scope_type, scope_key)
        correction = normalize_adaptive_correction(value)
        selected_operation_id = _operation_id(operation_id)
        scope = {"type": scope_type, "key": scope_key}
        request_digest = _operation_request_digest(
            "correct",
            scope=scope,
            target_memory_id=identifier,
            value=value,
        )
        now = self._now()
        with self._connect(write=True) as connection:
            replay = _replay_operation(
                connection,
                operation_id=selected_operation_id,
                operation_type="correct",
                scope=scope,
                target_memory_id=identifier,
                request_digest=request_digest,
                now=now,
            )
            if replay is not None:
                return replay
            old_row = connection.execute(
                _SELECT + " WHERE memory_id=? AND scope_type=? AND scope_key=?",
                (identifier, scope_type, scope_key),
            ).fetchone()
            if old_row is None or old_row["status"] != "active":
                raise AdaptiveMemoryError("active memory was not found in exact scope")
            old = _record(old_row)
            correction_observed = correction.get("observed_at", normalize_time(now))
            candidate = {
                "contract_version": "agiwiki.adaptive-write.v1",
                "memory_class": old["memory_class"],
                "scope": old["scope"],
                "content": correction["content"],
                "provenance": correction["provenance"],
                "observed_at": correction_observed,
                "valid_from": correction.get("valid_from", correction_observed),
                "valid_to": (
                    correction["valid_to"]
                    if "valid_to" in correction
                    else old["valid_to"]
                ),
                "confidence": correction.get("confidence", old["confidence"]),
                "sensitivity": correction.get("sensitivity", old["sensitivity"]),
                "retention": correction.get("retention", old["retention"]),
            }
            normalized = normalize_adaptive_write(candidate, now=now)
            changed = connection.execute(
                "UPDATE adaptive_memories SET status='superseded',updated_at_us=? "
                "WHERE memory_id=? AND status='active'",
                (timestamp_to_microseconds(normalize_time(now)), identifier),
            ).rowcount
            if changed != 1:
                raise AdaptiveMemoryError("memory changed during correction")
            record = _new_record(
                normalized,
                memory_id=f"mem_{secrets.token_hex(16)}",
                lineage_id=old["lineage_id"],
                revision=old["revision"] + 1,
                supersedes_memory_id=identifier,
                now=now,
            )
            _insert_record(connection, record)
            _insert_event(connection, "correct", record, now=now)
            _insert_operation(
                connection,
                operation_id=selected_operation_id,
                operation_type="correct",
                scope=scope,
                target_memory_id=identifier,
                request_digest=request_digest,
                result_memory_id=record["memory_id"],
                result_lineage_id=record["lineage_id"],
                result_revision_count=None,
                now=now,
            )
        return {
            "contract_version": "agiwiki.adaptive-correct.v1",
            "ok": True,
            "operation_id": selected_operation_id,
            "replayed": False,
            "superseded_memory_id": identifier,
            "memory": record,
        }

    def forget(
        self,
        memory_id: str,
        *,
        scope_type: str,
        scope_key: str,
        confirm: bool = False,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        identifier = _memory_id(memory_id)
        scope_type, scope_key = _scope(scope_type, scope_key)
        if confirm is not True:
            raise AdaptiveMemoryError("forget requires explicit confirmation")
        selected_operation_id = _operation_id(operation_id)
        scope = {"type": scope_type, "key": scope_key}
        request_digest = _operation_request_digest(
            "forget",
            scope=scope,
            target_memory_id=identifier,
            confirm=True,
        )
        now = self._now()
        now_us = timestamp_to_microseconds(normalize_time(now))
        with self._connect(write=True) as connection:
            replay = _replay_operation(
                connection,
                operation_id=selected_operation_id,
                operation_type="forget",
                scope=scope,
                target_memory_id=identifier,
                request_digest=request_digest,
                now=now,
            )
            if replay is not None:
                return replay
            target_row = connection.execute(
                _SELECT + " WHERE memory_id=? AND scope_type=? AND scope_key=?",
                (identifier, scope_type, scope_key),
            ).fetchone()
            if target_row is None:
                raise AdaptiveMemoryError("memory was not found in exact scope")
            target = _record(target_row)
            lineage_rows = connection.execute(
                _SELECT + " WHERE lineage_id=? ORDER BY revision",
                (target["lineage_id"],),
            ).fetchall()
            lineage = [_record(row) for row in lineage_rows]
            forgotten = sum(item["status"] != "deleted" for item in lineage)
            _scrub_lineage(
                connection,
                lineage_id=target["lineage_id"],
                updated_at_us=now_us,
            )
            _insert_event_values(
                connection,
                event_type="forget",
                memory_id=identifier,
                lineage_id=target["lineage_id"],
                now=now,
            )
            _insert_operation(
                connection,
                operation_id=selected_operation_id,
                operation_type="forget",
                scope=scope,
                target_memory_id=identifier,
                request_digest=request_digest,
                result_memory_id=identifier,
                result_lineage_id=target["lineage_id"],
                result_revision_count=forgotten,
                now=now,
            )
        return {
            "contract_version": "agiwiki.adaptive-forget.v1",
            "ok": True,
            "operation_id": selected_operation_id,
            "memory_id": identifier,
            "forgotten_revisions": forgotten,
            "replayed": False,
        }

    def review_plan(
        self,
        *,
        scope_type: str,
        scope_key: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return a content-free review proposal without persisting it."""

        scope_type, scope_key = _scope(scope_type, scope_key)
        _limit(limit, maximum=500)
        with self._connect() as connection:
            return self._build_review_proposal(
                connection,
                scope_type=scope_type,
                scope_key=scope_key,
                limit=limit,
                now=self._now(),
            )

    def review_due(
        self,
        *,
        scope_type: str,
        scope_key: str,
        interval: str = "weekly",
    ) -> dict[str, Any]:
        """Report deterministic review timing without creating proposals or mutations."""

        scope_type, scope_key = _scope(scope_type, scope_key)
        if interval not in {"daily", "weekly"}:
            raise AdaptiveMemoryError("review interval is invalid")
        checked_at = self._now_timestamp()
        with self._connect() as connection:
            proposal_row = connection.execute(
                "SELECT proposal_id,proposal_json,created_at_us "
                "FROM adaptive_review_proposals "
                "WHERE scope_type=? AND scope_key=? "
                "ORDER BY created_at_us DESC,proposal_id DESC LIMIT 1",
                (scope_type, scope_key),
            ).fetchone()
            if proposal_row is None:
                return validate_review_due(
                    {
                        "contract_version": "agiwiki.adaptive-review-due.v1",
                        "scope": {"type": scope_type, "key": scope_key},
                        "interval": interval,
                        "checked_at": checked_at,
                        "state": "never_reviewed",
                        "due": True,
                        "last_proposal_id": None,
                        "last_proposal_at": None,
                        "last_decision_id": None,
                        "last_decision_at": None,
                        "last_application_id": None,
                        "last_application_at": None,
                        "next_due_at": None,
                        "counts": {
                            "candidates": 0,
                            "accepted_actions": 0,
                            "applied_actions": 0,
                            "unapplied_supported_actions": 0,
                            "declared_corrections": 0,
                        },
                        "recommended_actions": ["create_proposal"],
                        "contains_memory_content": False,
                        "mutations_applied": False,
                        "model_invoked": False,
                    }
                )
            proposal = _stored_review_proposal(proposal_row["proposal_json"])
            if proposal["proposal_id"] != proposal_row["proposal_id"] or proposal[
                "generated_at"
            ] != microseconds_to_timestamp(proposal_row["created_at_us"]):
                raise AdaptiveMemoryError("stored review proposal binding is invalid")
            decision_row = connection.execute(
                "SELECT decision_id,receipt_json,decided_at_us "
                "FROM adaptive_review_decisions WHERE proposal_id=?",
                (proposal["proposal_id"],),
            ).fetchone()
            if decision_row is None:
                return validate_review_due(
                    {
                        "contract_version": "agiwiki.adaptive-review-due.v1",
                        "scope": {"type": scope_type, "key": scope_key},
                        "interval": interval,
                        "checked_at": checked_at,
                        "state": "pending_decision",
                        "due": False,
                        "last_proposal_id": proposal["proposal_id"],
                        "last_proposal_at": microseconds_to_timestamp(
                            proposal_row["created_at_us"]
                        ),
                        "last_decision_id": None,
                        "last_decision_at": None,
                        "last_application_id": None,
                        "last_application_at": None,
                        "next_due_at": None,
                        "counts": {
                            "candidates": len(proposal["candidates"]),
                            "accepted_actions": 0,
                            "applied_actions": 0,
                            "unapplied_supported_actions": 0,
                            "declared_corrections": 0,
                        },
                        "recommended_actions": ["decide_existing"],
                        "contains_memory_content": False,
                        "mutations_applied": False,
                        "model_invoked": False,
                    }
                )
            decision = _stored_review_receipt(decision_row["receipt_json"])
            if (
                decision["decision_id"] != decision_row["decision_id"]
                or decision["proposal_id"] != proposal["proposal_id"]
                or decision["proposal_digest"] != proposal["proposal_digest"]
                or decision["decided_at"]
                != microseconds_to_timestamp(decision_row["decided_at_us"])
            ):
                raise AdaptiveMemoryError("stored review decision binding is invalid")
            accepted = [
                item for item in decision["decisions"] if item["decision"] == "accepted"
            ]
            declared_corrections = sum(
                item["selected_action"] == "correct" for item in accepted
            )
            supported = [
                item for item in accepted if item["selected_action"] != "correct"
            ]
            application_row = connection.execute(
                "SELECT application_id,receipt_json,applied_at_us "
                "FROM adaptive_review_applications WHERE decision_id=?",
                (decision["decision_id"],),
            ).fetchone()
            application = (
                None
                if application_row is None
                else _stored_review_application_receipt(application_row["receipt_json"])
            )
            if application is not None:
                if (
                    application["application_id"] != application_row["application_id"]
                    or application["decision_id"] != decision["decision_id"]
                    or application["proposal_id"] != proposal["proposal_id"]
                    or application["proposal_digest"] != proposal["proposal_digest"]
                    or application["applied_at"]
                    != microseconds_to_timestamp(application_row["applied_at_us"])
                    or declared_corrections
                    or {item["candidate_id"] for item in application["results"]}
                    != {item["candidate_id"] for item in supported}
                ):
                    raise AdaptiveMemoryError(
                        "stored review application binding is invalid"
                    )
            decided_at = microseconds_to_timestamp(decision_row["decided_at_us"])
            next_due_at = normalize_time(
                datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
                + timedelta(days=1 if interval == "daily" else 7)
            )
            due = timestamp_to_microseconds(checked_at) >= timestamp_to_microseconds(
                next_due_at
            )
            unapplied_supported = 0 if application is not None else len(supported)
            recommendations: list[str] = []
            if unapplied_supported:
                recommendations.append("apply_supported_actions")
            if declared_corrections:
                recommendations.append("correct_manually")
            if due:
                recommendations.append("create_proposal")
            if not recommendations:
                recommendations.append("wait")
            return validate_review_due(
                {
                    "contract_version": "agiwiki.adaptive-review-due.v1",
                    "scope": {"type": scope_type, "key": scope_key},
                    "interval": interval,
                    "checked_at": checked_at,
                    "state": "reviewed",
                    "due": due,
                    "last_proposal_id": proposal["proposal_id"],
                    "last_proposal_at": microseconds_to_timestamp(
                        proposal_row["created_at_us"]
                    ),
                    "last_decision_id": decision["decision_id"],
                    "last_decision_at": decided_at,
                    "last_application_id": (
                        None
                        if application_row is None
                        else application_row["application_id"]
                    ),
                    "last_application_at": (
                        None
                        if application_row is None
                        else microseconds_to_timestamp(application_row["applied_at_us"])
                    ),
                    "next_due_at": next_due_at,
                    "counts": {
                        "candidates": len(proposal["candidates"]),
                        "accepted_actions": len(accepted),
                        "applied_actions": (
                            0 if application is None else len(application["results"])
                        ),
                        "unapplied_supported_actions": unapplied_supported,
                        "declared_corrections": declared_corrections,
                    },
                    "recommended_actions": recommendations,
                    "contains_memory_content": False,
                    "mutations_applied": False,
                    "model_invoked": False,
                }
            )

    def create_review_proposal(
        self,
        *,
        scope_type: str,
        scope_key: str,
        principal_id: str,
        credential: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Persist one immutable, content-free deterministic review proposal."""

        scope_type, scope_key = _scope(scope_type, scope_key)
        principal = _principal_id(principal_id, generate=False)
        _limit(limit, maximum=500)
        now = self._now()
        with self._connect(write=True) as connection:
            _authorize_principal(
                connection,
                principal_id=principal,
                credential=credential,
                permission="review_propose",
            )
            proposal = self._build_review_proposal(
                connection,
                scope_type=scope_type,
                scope_key=scope_key,
                limit=limit,
                now=now,
            )
            existing = connection.execute(
                "SELECT proposal_digest,proposal_json,proposed_by_principal_id "
                "FROM adaptive_review_proposals "
                "WHERE proposal_id=?",
                (proposal["proposal_id"],),
            ).fetchone()
            replayed = existing is not None
            if existing is not None:
                if (
                    existing["proposal_digest"] != proposal["proposal_digest"]
                    or existing["proposal_json"] != canonical_json(proposal)
                    or existing["proposed_by_principal_id"] != principal
                ):
                    raise AdaptiveMemoryError(
                        "review proposal identifier is bound to another snapshot"
                    )
            else:
                connection.execute(
                    "INSERT INTO adaptive_review_proposals("
                    "proposal_id,scope_type,scope_key,proposal_digest,proposal_json,"
                    "created_at_us,proposed_by_principal_id) VALUES(?,?,?,?,?,?,?)",
                    (
                        proposal["proposal_id"],
                        scope_type,
                        scope_key,
                        proposal["proposal_digest"],
                        canonical_json(proposal),
                        timestamp_to_microseconds(proposal["generated_at"]),
                        principal,
                    ),
                )
        return {
            "contract_version": "agiwiki.adaptive-review-proposal-record.v1",
            "ok": True,
            "replayed": replayed,
            "proposed_by_principal_id": principal,
            "proposal": proposal,
        }

    def decide_review(
        self,
        proposal_id: str,
        value: Mapping[str, Any],
        *,
        scope_type: str,
        scope_key: str,
        principal_id: str,
        credential: str,
        confirm: bool = False,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        """Record one human-declared decision receipt without applying actions."""

        proposal_identifier = _proposal_id(proposal_id)
        scope_type, scope_key = _scope(scope_type, scope_key)
        principal = _principal_id(principal_id, generate=False)
        if confirm is not True:
            raise AdaptiveMemoryError("review decision requires explicit confirmation")
        decision = normalize_review_decision(value)
        if decision["proposal_id"] != proposal_identifier:
            raise AdaptiveMemoryError("review decision proposal identifier mismatch")
        selected_decision_id = _decision_id(decision_id)
        request_digest = sha256_digest(
            {"decision": decision, "principal_id": principal, "confirm": True}
        )
        now = self._now()
        now_timestamp = normalize_time(now)
        with self._connect(write=True) as connection:
            _authorize_principal(
                connection,
                principal_id=principal,
                credential=credential,
                permission="review_approve",
            )
            proposal_row = connection.execute(
                "SELECT proposal_digest,proposal_json FROM adaptive_review_proposals "
                "WHERE proposal_id=? AND scope_type=? AND scope_key=?",
                (proposal_identifier, scope_type, scope_key),
            ).fetchone()
            if proposal_row is None:
                raise AdaptiveMemoryError(
                    "review proposal was not found in exact scope"
                )
            proposal = _stored_review_proposal(proposal_row["proposal_json"])
            if (
                proposal_row["proposal_digest"] != proposal["proposal_digest"]
                or decision["proposal_digest"] != proposal["proposal_digest"]
            ):
                raise AdaptiveMemoryError("review decision proposal digest mismatch")
            _validate_decisions_for_proposal(decision["decisions"], proposal)

            existing = connection.execute(
                "SELECT decision_id,request_digest,receipt_json "
                "FROM adaptive_review_decisions WHERE decision_id=?",
                (selected_decision_id,),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest:
                    raise AdaptiveMemoryError(
                        "decision_id is already bound to another request"
                    )
                receipt = _stored_review_receipt(existing["receipt_json"])
                replay = dict(receipt)
                replay["replayed"] = True
                return validate_review_receipt(replay)
            if connection.execute(
                "SELECT 1 FROM adaptive_review_decisions WHERE proposal_id=?",
                (proposal_identifier,),
            ).fetchone():
                raise AdaptiveMemoryError(
                    "review proposal already has a decision receipt"
                )

            receipt = validate_review_receipt(
                {
                    "contract_version": ("agiwiki.adaptive-review-decision-receipt.v2"),
                    "decision_id": selected_decision_id,
                    "proposal_id": proposal_identifier,
                    "proposal_digest": proposal["proposal_digest"],
                    "approved_by_principal_id": principal,
                    "decided_at": now_timestamp,
                    "decisions": decision["decisions"],
                    "contains_memory_content": False,
                    "mutations_applied": False,
                    "replayed": False,
                }
            )
            connection.execute(
                "INSERT INTO adaptive_review_decisions("
                "decision_id,proposal_id,proposal_digest,declared_reviewer,"
                "request_digest,receipt_json,decided_at_us,"
                "approved_by_principal_id) VALUES(?,?,?,?,?,?,?,?)",
                (
                    selected_decision_id,
                    proposal_identifier,
                    proposal["proposal_digest"],
                    "legacy:not-applicable",
                    request_digest,
                    canonical_json(receipt),
                    timestamp_to_microseconds(now_timestamp),
                    principal,
                ),
            )
        return receipt

    def apply_review_decision(
        self,
        decision_id: str,
        value: Mapping[str, Any],
        *,
        scope_type: str,
        scope_key: str,
        principal_id: str,
        credential: str,
        confirm: bool = False,
        application_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically apply supported accepted actions from one sealed decision."""

        decision_identifier = _decision_id(decision_id)
        scope_type, scope_key = _scope(scope_type, scope_key)
        principal = _principal_id(principal_id, generate=False)
        if confirm is not True:
            raise AdaptiveMemoryError(
                "review application requires explicit confirmation"
            )
        application = normalize_review_application(value)
        if application["decision_id"] != decision_identifier:
            raise AdaptiveMemoryError("review application decision identifier mismatch")
        selected_application_id = _application_id(application_id)
        request_digest = sha256_digest(
            {
                "application": application,
                "principal_id": principal,
                "confirm": True,
            }
        )
        now = self._now()
        now_timestamp = normalize_time(now)
        now_us = timestamp_to_microseconds(now_timestamp)
        with self._connect(write=True) as connection:
            _authorize_principal(
                connection,
                principal_id=principal,
                credential=credential,
                permission="review_apply",
            )
            row = connection.execute(
                "SELECT d.proposal_id,d.proposal_digest,d.receipt_json,"
                "d.approved_by_principal_id,p.proposal_json,"
                "p.proposed_by_principal_id FROM adaptive_review_decisions AS d "
                "JOIN adaptive_review_proposals AS p "
                "ON p.proposal_id=d.proposal_id "
                "AND p.proposal_digest=d.proposal_digest "
                "WHERE d.decision_id=? AND p.scope_type=? AND p.scope_key=?",
                (decision_identifier, scope_type, scope_key),
            ).fetchone()
            if row is None:
                raise AdaptiveMemoryError(
                    "review decision was not found in exact scope"
                )
            proposal = _stored_review_proposal(row["proposal_json"])
            decision = _stored_review_receipt(row["receipt_json"])
            if (
                row["proposed_by_principal_id"] is None
                or row["approved_by_principal_id"] is None
                or decision["contract_version"]
                != "agiwiki.adaptive-review-decision-receipt.v2"
                or decision["approved_by_principal_id"]
                != row["approved_by_principal_id"]
            ):
                raise AdaptiveMemoryError(
                    "legacy unseparated review decisions cannot be applied"
                )
            if (
                application["proposal_id"] != row["proposal_id"]
                or application["proposal_digest"] != row["proposal_digest"]
            ):
                raise AdaptiveMemoryError(
                    "review application proposal binding mismatch"
                )

            existing = connection.execute(
                "SELECT request_digest,receipt_json "
                "FROM adaptive_review_applications WHERE application_id=?",
                (selected_application_id,),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest:
                    raise AdaptiveMemoryError(
                        "application_id is already bound to another request"
                    )
                receipt = _stored_review_application_receipt(existing["receipt_json"])
                replay = dict(receipt)
                replay["replayed"] = True
                return validate_review_application_receipt(replay)
            if connection.execute(
                "SELECT 1 FROM adaptive_review_applications WHERE decision_id=?",
                (decision_identifier,),
            ).fetchone():
                raise AdaptiveMemoryError(
                    "review decision already has an application receipt"
                )

            plans = _review_application_plans(
                connection,
                application=application,
                proposal=proposal,
                decision=decision,
                scope_type=scope_type,
                scope_key=scope_key,
                now_us=now_us,
            )
            results: list[dict[str, Any]] = []
            for plan in plans:
                forgotten_memory_ids: list[str] = []
                forgotten_lineage_ids: list[str] = []
                forgotten_revision_count = 0
                for target in plan["targets"]:
                    _scrub_lineage(
                        connection,
                        lineage_id=target["lineage_id"],
                        updated_at_us=now_us,
                    )
                    _insert_event_values(
                        connection,
                        event_type="forget",
                        memory_id=target["memory_id"],
                        lineage_id=target["lineage_id"],
                        now=now,
                    )
                    operation_id = _review_apply_operation_id(
                        selected_application_id,
                        plan["candidate_id"],
                        target["memory_id"],
                    )
                    _insert_operation(
                        connection,
                        operation_id=operation_id,
                        operation_type="forget",
                        scope={"type": scope_type, "key": scope_key},
                        target_memory_id=target["memory_id"],
                        request_digest=_operation_request_digest(
                            "forget",
                            scope={"type": scope_type, "key": scope_key},
                            target_memory_id=target["memory_id"],
                            confirm=True,
                        ),
                        result_memory_id=target["memory_id"],
                        result_lineage_id=target["lineage_id"],
                        result_revision_count=target["revision_count"],
                        now=now,
                    )
                    forgotten_memory_ids.append(target["memory_id"])
                    forgotten_lineage_ids.append(target["lineage_id"])
                    forgotten_revision_count += target["revision_count"]
                results.append(
                    {
                        "candidate_id": plan["candidate_id"],
                        "action": plan["action"],
                        "kept_memory_id": plan["keep_memory_id"],
                        "forgotten_memory_ids": sorted(forgotten_memory_ids),
                        "forgotten_lineage_ids": sorted(forgotten_lineage_ids),
                        "forgotten_revision_count": forgotten_revision_count,
                    }
                )
            receipt = validate_review_application_receipt(
                {
                    "contract_version": (
                        "agiwiki.adaptive-review-application-receipt.v1"
                    ),
                    "application_id": selected_application_id,
                    "decision_id": decision_identifier,
                    "proposal_id": proposal["proposal_id"],
                    "proposal_digest": proposal["proposal_digest"],
                    "applied_by_principal_id": principal,
                    "applied_at": now_timestamp,
                    "results": results,
                    "contains_memory_content": False,
                    "mutations_applied": True,
                    "replayed": False,
                }
            )
            connection.execute(
                "INSERT INTO adaptive_review_applications("
                "application_id,decision_id,proposal_id,proposal_digest,"
                "request_digest,receipt_json,applied_at_us,"
                "applied_by_principal_id) VALUES(?,?,?,?,?,?,?,?)",
                (
                    selected_application_id,
                    decision_identifier,
                    proposal["proposal_id"],
                    proposal["proposal_digest"],
                    request_digest,
                    canonical_json(receipt),
                    now_us,
                    principal,
                ),
            )
        return receipt

    def show_review(
        self,
        proposal_id: str,
        *,
        scope_type: str,
        scope_key: str,
    ) -> dict[str, Any]:
        """Read one exact-scope proposal and its optional decision receipt."""

        proposal_identifier = _proposal_id(proposal_id)
        scope_type, scope_key = _scope(scope_type, scope_key)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT proposal_json,proposed_by_principal_id "
                "FROM adaptive_review_proposals "
                "WHERE proposal_id=? AND scope_type=? AND scope_key=?",
                (proposal_identifier, scope_type, scope_key),
            ).fetchone()
            if row is None:
                return {
                    "contract_version": "agiwiki.adaptive-review-show.v1",
                    "found": False,
                }
            proposal = _stored_review_proposal(row["proposal_json"])
            decision_row = connection.execute(
                "SELECT decision_id,receipt_json,approved_by_principal_id "
                "FROM adaptive_review_decisions WHERE proposal_id=?",
                (proposal_identifier,),
            ).fetchone()
            receipt = (
                None
                if decision_row is None
                else _stored_review_receipt(decision_row["receipt_json"])
            )
            application_row = (
                None
                if decision_row is None
                else connection.execute(
                    "SELECT receipt_json FROM adaptive_review_applications "
                    "WHERE decision_id=?",
                    (decision_row["decision_id"],),
                ).fetchone()
            )
            application_receipt = (
                None
                if application_row is None
                else _stored_review_application_receipt(application_row["receipt_json"])
            )
        return {
            "contract_version": "agiwiki.adaptive-review-show.v1",
            "found": True,
            "proposed_by_principal_id": row["proposed_by_principal_id"],
            "proposal": proposal,
            "decision_receipt": receipt,
            "application_receipt": application_receipt,
        }

    def _build_review_proposal(
        self,
        connection: sqlite3.Connection,
        *,
        scope_type: str,
        scope_key: str,
        limit: int,
        now: datetime,
    ) -> dict[str, Any]:
        now_timestamp = normalize_time(now)
        now_us = timestamp_to_microseconds(now_timestamp)
        rows = connection.execute(
            _SELECT
            + " WHERE scope_type=? AND scope_key=? AND status='active' "
            + "ORDER BY observed_at_us DESC,memory_id LIMIT ?",
            (scope_type, scope_key, limit + 1),
        ).fetchall()
        scan_truncated = len(rows) > limit
        memories = [_record(row) for row in rows[:limit]]
        current: list[dict[str, Any]] = []
        expired: list[dict[str, Any]] = []
        scheduled: list[dict[str, Any]] = []
        for memory in memories:
            valid_from_us = timestamp_to_microseconds(memory["valid_from"])
            valid_to_us = _timestamp_or_none(memory["valid_to"])
            expires_at_us = _timestamp_or_none(memory["retention"]["expires_at"])
            if valid_from_us > now_us:
                scheduled.append(memory)
            elif (valid_to_us is not None and valid_to_us <= now_us) or (
                expires_at_us is not None and expires_at_us <= now_us
            ):
                expired.append(memory)
            else:
                current.append(memory)

        candidate_bodies: list[dict[str, Any]] = [
            {
                "reason": "expired",
                "memory_ids": [memory["memory_id"]],
                "lineage_ids": [memory["lineage_id"]],
                "memory_classes": [memory["memory_class"]],
                "suggested_actions": ["forget", "correct"],
            }
            for memory in expired
        ]
        by_content: dict[str, list[dict[str, Any]]] = {}
        for memory in current:
            by_content.setdefault(memory["content_digest"], []).append(memory)
        duplicate_groups = [
            group
            for group in by_content.values()
            if len({memory["lineage_id"] for memory in group}) > 1
        ]
        for group in sorted(
            duplicate_groups,
            key=lambda items: tuple(sorted(item["memory_id"] for item in items)),
        ):
            candidate_bodies.append(
                {
                    "reason": "exact_duplicate_content",
                    "memory_ids": sorted(item["memory_id"] for item in group),
                    "lineage_ids": sorted(item["lineage_id"] for item in group),
                    "memory_classes": sorted({item["memory_class"] for item in group}),
                    "suggested_actions": ["keep_one", "forget_redundant"],
                }
            )

        snapshot = {
            "scope": {"type": scope_type, "key": scope_key},
            "generated_at": now_timestamp,
            "limit": limit,
            "scan_truncated": scan_truncated,
            "records": [
                {
                    "memory_id": memory["memory_id"],
                    "lineage_id": memory["lineage_id"],
                    "revision": memory["revision"],
                    "status": memory["status"],
                    "valid_from": memory["valid_from"],
                    "valid_to": memory["valid_to"],
                    "expires_at": memory["retention"]["expires_at"],
                    "updated_at": memory["updated_at"],
                }
                for memory in memories
            ],
        }
        snapshot_digest = sha256_digest(snapshot)
        candidates = [
            {
                "candidate_id": review_candidate_id(snapshot_digest, candidate),
                **candidate,
            }
            for candidate in candidate_bodies
        ]
        body = {
            "contract_version": "agiwiki.adaptive-review-proposal.v1",
            "scope": {"type": scope_type, "key": scope_key},
            "generated_at": now_timestamp,
            "snapshot_digest": snapshot_digest,
            "counts": {
                "scanned": len(memories),
                "current": len(current),
                "expired": len(expired),
                "scheduled": len(scheduled),
                "exact_duplicate_groups": len(duplicate_groups),
                "candidates": len(candidates),
            },
            "scan_truncated": scan_truncated,
            "candidates": candidates,
            "contains_memory_content": False,
            "mutations_applied": False,
            "model_invoked": False,
        }
        proposal_digest = sha256_digest(body)
        proposal = {
            **body,
            "proposal_id": "review_" + proposal_digest.removeprefix("sha256:")[:32],
            "proposal_digest": proposal_digest,
        }
        return validate_review_proposal(proposal)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise AdaptiveMemoryError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _now_timestamp(self) -> str:
        return normalize_time(self._now())

    @contextmanager
    def _connect(
        self,
        *,
        write: bool = False,
        initializing: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        if not initializing:
            require_private_regular_file(self.paths.adaptive_db)
        connection = sqlite3.connect(self.paths.adaptive_db)
        connection.row_factory = sqlite3.Row
        try:
            if not initializing:
                try:
                    metadata = connection.execute(
                        "SELECT schema_version,ledger_id FROM ledger_meta "
                        "WHERE singleton=1"
                    ).fetchone()
                except sqlite3.Error as exc:
                    raise AdaptiveMemoryError(
                        "adaptive database metadata is invalid"
                    ) from exc
                if (
                    metadata is None
                    or metadata["schema_version"] != ADAPTIVE_SCHEMA_VERSION
                    or not isinstance(metadata["ledger_id"], str)
                    or not metadata["ledger_id"]
                ):
                    raise AdaptiveMemoryError(
                        "adaptive database schema version is unsupported"
                    )
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA secure_delete=ON")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise AdaptiveMemoryError("SQLite foreign keys are unavailable")
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


def normalize_time(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _inspect_existing_database(
    database: Path,
    *,
    accepted_versions: set[int] | None = None,
) -> dict[str, Any]:
    """Recognize an existing ledger without modifying bytes or permissions."""

    versions = accepted_versions or {ADAPTIVE_SCHEMA_VERSION}
    require_private_regular_file(database)
    uri = f"file:{quote(str(database))}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise AdaptiveMemoryError("adaptive database integrity check failed")
            row = connection.execute(
                "SELECT schema_version,ledger_id FROM ledger_meta WHERE singleton=1"
            ).fetchone()
            if (
                row is None
                or row["schema_version"] not in versions
                or not isinstance(row["ledger_id"], str)
                or not row["ledger_id"]
            ):
                raise AdaptiveMemoryError(
                    "adaptive database schema version is unsupported"
                )
            _verify_table_columns(connection, schema_version=row["schema_version"])
            return {
                "schema_version": row["schema_version"],
                "ledger_id": row["ledger_id"],
            }
        finally:
            connection.close()
    except AdaptiveMemoryError:
        raise
    except sqlite3.Error as exc:
        raise AdaptiveMemoryError("adaptive database is invalid") from exc


def _verify_table_columns(
    connection: sqlite3.Connection,
    *,
    schema_version: int,
) -> None:
    expected = {
        "ledger_meta": {"singleton", "schema_version", "ledger_id"},
        "adaptive_memories": {
            "memory_id",
            "lineage_id",
            "revision",
            "memory_class",
            "scope_type",
            "scope_key",
            "content",
            "content_digest",
            "provenance_type",
            "provenance_digest",
            "observed_at_us",
            "valid_from_us",
            "valid_to_us",
            "confidence_ppm",
            "sensitivity",
            "retention_mode",
            "expires_at_us",
            "status",
            "supersedes_memory_id",
            "created_at_us",
            "updated_at_us",
        },
        "adaptive_memory_events": {
            "sequence",
            "event_id",
            "event_type",
            "memory_id",
            "lineage_id",
            "occurred_at_us",
        },
    }
    if schema_version in {
        _OPERATIONS_SCHEMA_VERSION,
        _REVIEW_SCHEMA_VERSION,
        _CAPABILITY_SCHEMA_VERSION,
        ADAPTIVE_SCHEMA_VERSION,
    }:
        expected["adaptive_operations"] = {
            "operation_id",
            "operation_type",
            "scope_type",
            "scope_key",
            "target_memory_id",
            "request_digest",
            "result_memory_id",
            "result_lineage_id",
            "result_revision_count",
            "completed_at_us",
        }
    if schema_version in {
        _REVIEW_SCHEMA_VERSION,
        _CAPABILITY_SCHEMA_VERSION,
        ADAPTIVE_SCHEMA_VERSION,
    }:
        expected["adaptive_review_proposals"] = {
            "proposal_id",
            "scope_type",
            "scope_key",
            "proposal_digest",
            "proposal_json",
            "created_at_us",
            *(
                {"proposed_by_principal_id"}
                if schema_version
                in {_CAPABILITY_SCHEMA_VERSION, ADAPTIVE_SCHEMA_VERSION}
                else set()
            ),
        }
        expected["adaptive_review_decisions"] = {
            "decision_id",
            "proposal_id",
            "proposal_digest",
            "declared_reviewer",
            "request_digest",
            "receipt_json",
            "decided_at_us",
            *(
                {"approved_by_principal_id"}
                if schema_version
                in {_CAPABILITY_SCHEMA_VERSION, ADAPTIVE_SCHEMA_VERSION}
                else set()
            ),
        }
    if schema_version in {_CAPABILITY_SCHEMA_VERSION, ADAPTIVE_SCHEMA_VERSION}:
        expected["adaptive_principals"] = {
            "principal_id",
            "token_hash",
            "permissions_json",
            "status",
            "created_at_us",
            "revoked_at_us",
        }
    elif schema_version not in {
        _LEGACY_ADAPTIVE_SCHEMA_VERSION,
        _OPERATIONS_SCHEMA_VERSION,
        _REVIEW_SCHEMA_VERSION,
    }:
        raise AdaptiveMemoryError("adaptive database schema version is unsupported")
    if schema_version == ADAPTIVE_SCHEMA_VERSION:
        expected["adaptive_review_applications"] = {
            "application_id",
            "decision_id",
            "proposal_id",
            "proposal_digest",
            "request_digest",
            "receipt_json",
            "applied_at_us",
            "applied_by_principal_id",
        }
    actual_tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if actual_tables != set(expected):
        raise AdaptiveMemoryError("adaptive database schema is incomplete")
    for table, columns in expected.items():
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if actual != columns:
            raise AdaptiveMemoryError("adaptive database schema is incomplete")
    if schema_version in {
        _REVIEW_SCHEMA_VERSION,
        _CAPABILITY_SCHEMA_VERSION,
        ADAPTIVE_SCHEMA_VERSION,
    }:
        required_triggers = {
            "trg_adaptive_review_proposals_no_update",
            "trg_adaptive_review_proposals_no_delete",
            "trg_adaptive_review_decisions_no_update",
            "trg_adaptive_review_decisions_no_delete",
        }
        actual_triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        if not required_triggers <= actual_triggers:
            raise AdaptiveMemoryError(
                "adaptive review append-only triggers are missing"
            )
        if schema_version == ADAPTIVE_SCHEMA_VERSION:
            application_triggers = {
                "trg_adaptive_review_applications_no_update",
                "trg_adaptive_review_applications_no_delete",
            }
            if not application_triggers <= actual_triggers:
                raise AdaptiveMemoryError(
                    "adaptive review application triggers are missing"
                )


def _migrate_to_current(database: Path) -> dict[str, Any]:
    """Upgrade a recognized older ledger during explicit initialization."""

    require_private_regular_file(database)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT schema_version,ledger_id FROM ledger_meta WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise AdaptiveMemoryError("adaptive database metadata is unsupported")
        source_version = row["schema_version"]
        if source_version == ADAPTIVE_SCHEMA_VERSION:
            _verify_table_columns(
                connection,
                schema_version=ADAPTIVE_SCHEMA_VERSION,
            )
            connection.commit()
            return {
                "schema_version": ADAPTIVE_SCHEMA_VERSION,
                "ledger_id": row["ledger_id"],
                "migrated_from": None,
            }
        if source_version not in {
            _LEGACY_ADAPTIVE_SCHEMA_VERSION,
            _OPERATIONS_SCHEMA_VERSION,
            _REVIEW_SCHEMA_VERSION,
            _CAPABILITY_SCHEMA_VERSION,
        }:
            raise AdaptiveMemoryError("adaptive database schema version is unsupported")
        _verify_table_columns(
            connection,
            schema_version=source_version,
        )
        if source_version == _LEGACY_ADAPTIVE_SCHEMA_VERSION:
            connection.execute(_CREATE_OPERATIONS_TABLE)
            connection.execute(_CREATE_OPERATIONS_LINEAGE_INDEX)
        if source_version in {
            _LEGACY_ADAPTIVE_SCHEMA_VERSION,
            _OPERATIONS_SCHEMA_VERSION,
            _REVIEW_SCHEMA_VERSION,
        }:
            connection.execute(_CREATE_PRINCIPALS_TABLE)
        if source_version in {
            _LEGACY_ADAPTIVE_SCHEMA_VERSION,
            _OPERATIONS_SCHEMA_VERSION,
        }:
            for statement in _CREATE_REVIEW_STATEMENTS:
                connection.execute(statement)
        elif source_version == _REVIEW_SCHEMA_VERSION:
            connection.execute(
                "ALTER TABLE adaptive_review_proposals "
                "ADD COLUMN proposed_by_principal_id TEXT "
                "REFERENCES adaptive_principals(principal_id)"
            )
            connection.execute(
                "ALTER TABLE adaptive_review_decisions "
                "ADD COLUMN approved_by_principal_id TEXT "
                "REFERENCES adaptive_principals(principal_id)"
            )
        for statement in _CREATE_REVIEW_APPLICATION_STATEMENTS:
            connection.execute(statement)
        changed = connection.execute(
            "UPDATE ledger_meta SET schema_version=? "
            "WHERE singleton=1 AND schema_version=?",
            (ADAPTIVE_SCHEMA_VERSION, source_version),
        ).rowcount
        if changed != 1:
            raise AdaptiveMemoryError("adaptive database changed during migration")
        _verify_table_columns(connection, schema_version=ADAPTIVE_SCHEMA_VERSION)
        connection.commit()
        return {
            "schema_version": ADAPTIVE_SCHEMA_VERSION,
            "ledger_id": row["ledger_id"],
            "migrated_from": source_version,
        }
    except AdaptiveMemoryError:
        connection.rollback()
        raise
    except sqlite3.Error as exc:
        connection.rollback()
        raise AdaptiveMemoryError("adaptive database migration failed") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _new_record(
    candidate: Mapping[str, Any],
    *,
    memory_id: str,
    lineage_id: str,
    revision: int,
    supersedes_memory_id: str | None,
    now: datetime,
) -> dict[str, Any]:
    timestamp = normalize_time(now)
    record = {
        "contract_version": "agiwiki.adaptive-memory.v1",
        "memory_id": memory_id,
        "lineage_id": lineage_id,
        "revision": revision,
        "memory_class": candidate["memory_class"],
        "scope": candidate["scope"],
        "content": candidate["content"],
        "content_digest": sha256_digest(candidate["content"]),
        "provenance": candidate["provenance"],
        "observed_at": candidate["observed_at"],
        "valid_from": candidate["valid_from"],
        "valid_to": candidate["valid_to"],
        "confidence": candidate["confidence"],
        "sensitivity": candidate["sensitivity"],
        "retention": candidate["retention"],
        "status": "active",
        "supersedes_memory_id": supersedes_memory_id,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    return validate_adaptive_record(record)


def _insert_record(connection: sqlite3.Connection, record: Mapping[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO adaptive_memories(
          memory_id,lineage_id,revision,memory_class,scope_type,scope_key,
          content,content_digest,provenance_type,provenance_digest,
          observed_at_us,valid_from_us,valid_to_us,confidence_ppm,sensitivity,
          retention_mode,expires_at_us,status,supersedes_memory_id,
          created_at_us,updated_at_us
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            record["memory_id"],
            record["lineage_id"],
            record["revision"],
            record["memory_class"],
            record["scope"]["type"],
            record["scope"]["key"],
            record["content"],
            record["content_digest"],
            record["provenance"]["type"],
            record["provenance"]["digest"],
            timestamp_to_microseconds(record["observed_at"]),
            timestamp_to_microseconds(record["valid_from"]),
            _timestamp_or_none(record["valid_to"]),
            round(record["confidence"] * 1_000_000),
            record["sensitivity"],
            record["retention"]["mode"],
            _timestamp_or_none(record["retention"]["expires_at"]),
            record["status"],
            record["supersedes_memory_id"],
            timestamp_to_microseconds(record["created_at"]),
            timestamp_to_microseconds(record["updated_at"]),
        ),
    )


def _insert_event(
    connection: sqlite3.Connection,
    event_type: str,
    record: Mapping[str, Any],
    *,
    now: datetime,
) -> None:
    _insert_event_values(
        connection,
        event_type=event_type,
        memory_id=record["memory_id"],
        lineage_id=record["lineage_id"],
        now=now,
    )


def _insert_event_values(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    memory_id: str,
    lineage_id: str,
    now: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO adaptive_memory_events(
          event_id,event_type,memory_id,lineage_id,occurred_at_us
        ) VALUES(?,?,?,?,?)
        """,
        (
            f"event_{secrets.token_hex(16)}",
            event_type,
            memory_id,
            lineage_id,
            timestamp_to_microseconds(normalize_time(now)),
        ),
    )


def _insert_operation(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    operation_type: str,
    scope: Mapping[str, str],
    target_memory_id: str | None,
    request_digest: str,
    result_memory_id: str,
    result_lineage_id: str,
    result_revision_count: int | None,
    now: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO adaptive_operations(
          operation_id,operation_type,scope_type,scope_key,target_memory_id,
          request_digest,result_memory_id,result_lineage_id,
          result_revision_count,completed_at_us
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            operation_id,
            operation_type,
            scope["type"],
            scope["key"],
            target_memory_id,
            request_digest,
            result_memory_id,
            result_lineage_id,
            result_revision_count,
            timestamp_to_microseconds(normalize_time(now)),
        ),
    )


def _replay_operation(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    operation_type: str,
    scope: Mapping[str, str],
    target_memory_id: str | None,
    request_digest: str,
    now: datetime,
) -> dict[str, Any] | None:
    operation = connection.execute(
        "SELECT operation_id,operation_type,scope_type,scope_key,target_memory_id,"
        "request_digest,result_memory_id,result_lineage_id,result_revision_count "
        "FROM adaptive_operations WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if operation is None:
        return None
    if operation["request_digest"] is None:
        raise AdaptiveMemoryError("operation binding was erased by a confirmed forget")
    expected = (
        operation_type,
        scope["type"],
        scope["key"],
        target_memory_id,
        request_digest,
    )
    actual = (
        operation["operation_type"],
        operation["scope_type"],
        operation["scope_key"],
        operation["target_memory_id"],
        operation["request_digest"],
    )
    if actual != expected:
        raise AdaptiveMemoryError("operation_id is already bound to another request")
    if operation_type == "forget":
        target_row = connection.execute(
            _SELECT + " WHERE memory_id=?",
            (operation["result_memory_id"],),
        ).fetchone()
        if target_row is None:
            raise AdaptiveMemoryError("operation result memory is missing")
        target = _record(target_row)
        if (
            operation["result_memory_id"] != target_memory_id
            or target["lineage_id"] != operation["result_lineage_id"]
            or target["scope"] != dict(scope)
            or type(operation["result_revision_count"]) is not int
            or operation["result_revision_count"] < 0
        ):
            raise AdaptiveMemoryError("operation result binding is inconsistent")
        _scrub_lineage(
            connection,
            lineage_id=operation["result_lineage_id"],
            updated_at_us=timestamp_to_microseconds(normalize_time(now)),
        )
        return {
            "contract_version": "agiwiki.adaptive-forget.v1",
            "ok": True,
            "operation_id": operation_id,
            "memory_id": operation["result_memory_id"],
            "forgotten_revisions": operation["result_revision_count"],
            "replayed": True,
        }
    row = connection.execute(
        _SELECT + " WHERE memory_id=?",
        (operation["result_memory_id"],),
    ).fetchone()
    if row is None:
        raise AdaptiveMemoryError("operation result memory is missing")
    record = _record(row)
    if record["lineage_id"] != operation["result_lineage_id"] or record[
        "scope"
    ] != dict(scope):
        raise AdaptiveMemoryError("operation result binding is inconsistent")
    if operation_type == "remember":
        if record["revision"] != 1 or record["supersedes_memory_id"] is not None:
            raise AdaptiveMemoryError("operation result binding is inconsistent")
        return {
            "contract_version": "agiwiki.adaptive-remember.v1",
            "ok": True,
            "operation_id": operation_id,
            "replayed": True,
            "memory": record,
        }
    if operation_type == "correct":
        if record["supersedes_memory_id"] != target_memory_id:
            raise AdaptiveMemoryError("operation result binding is inconsistent")
        return {
            "contract_version": "agiwiki.adaptive-correct.v1",
            "ok": True,
            "operation_id": operation_id,
            "replayed": True,
            "superseded_memory_id": operation["target_memory_id"],
            "memory": record,
        }
    raise AdaptiveMemoryError("operation type is unsupported")


def _scrub_lineage(
    connection: sqlite3.Connection,
    *,
    lineage_id: str,
    updated_at_us: int,
) -> None:
    connection.execute(
        """
        UPDATE adaptive_memories
        SET status='deleted',content=NULL,content_digest=NULL,
            provenance_type=NULL,provenance_digest=NULL,updated_at_us=?
        WHERE lineage_id=? AND
              (status<>'deleted' OR content IS NOT NULL OR
               content_digest IS NOT NULL OR provenance_type IS NOT NULL OR
               provenance_digest IS NOT NULL)
        """,
        (updated_at_us, lineage_id),
    )
    connection.execute(
        """
        UPDATE adaptive_operations
        SET request_digest=NULL
        WHERE result_lineage_id=? AND operation_type IN ('remember','correct')
              AND request_digest IS NOT NULL
        """,
        (lineage_id,),
    )


def _record(row: sqlite3.Row) -> dict[str, Any]:
    provenance = (
        None
        if row["provenance_type"] is None
        else {"type": row["provenance_type"], "digest": row["provenance_digest"]}
    )
    record = {
        "contract_version": "agiwiki.adaptive-memory.v1",
        "memory_id": row["memory_id"],
        "lineage_id": row["lineage_id"],
        "revision": row["revision"],
        "memory_class": row["memory_class"],
        "scope": {"type": row["scope_type"], "key": row["scope_key"]},
        "content": row["content"],
        "content_digest": row["content_digest"],
        "provenance": provenance,
        "observed_at": microseconds_to_timestamp(row["observed_at_us"]),
        "valid_from": microseconds_to_timestamp(row["valid_from_us"]),
        "valid_to": _time_or_none(row["valid_to_us"]),
        "confidence": row["confidence_ppm"] / 1_000_000,
        "sensitivity": row["sensitivity"],
        "retention": {
            "mode": row["retention_mode"],
            "expires_at": _time_or_none(row["expires_at_us"]),
        },
        "status": row["status"],
        "supersedes_memory_id": row["supersedes_memory_id"],
        "created_at": microseconds_to_timestamp(row["created_at_us"]),
        "updated_at": microseconds_to_timestamp(row["updated_at_us"]),
    }
    try:
        normalized = validate_adaptive_record(record)
    except AdaptiveContractError as exc:
        raise AdaptiveMemoryError(str(exc)) from exc
    return normalized


def _scope(scope_type: str, scope_key: str) -> tuple[str, str]:
    if scope_type not in _SCOPE_TYPES:
        raise AdaptiveMemoryError("scope_type is invalid")
    if (
        not isinstance(scope_key, str)
        or not scope_key
        or len(scope_key) > 256
        or scope_key != scope_key.strip()
        or any(ord(character) < 32 for character in scope_key)
    ):
        raise AdaptiveMemoryError("scope_key is invalid")
    return scope_type, scope_key


def _memory_id(value: str) -> str:
    if not isinstance(value, str) or _MEMORY_ID.fullmatch(value) is None:
        raise AdaptiveMemoryError("memory_id is invalid")
    return value


def _operation_id(value: str | None) -> str:
    if value is None:
        return f"op_{secrets.token_hex(16)}"
    if not isinstance(value, str) or _OPERATION_ID.fullmatch(value) is None:
        raise AdaptiveMemoryError("operation_id is invalid")
    return value


def generate_local_capability(
    principal_id: str | None = None,
) -> tuple[str, str]:
    """Generate a principal ID and high-entropy token without persisting either."""

    return _principal_id(principal_id), f"agwcap_{secrets.token_hex(32)}"


def write_local_capability(
    path: str | os.PathLike[str],
    *,
    principal_id: str,
    token: str,
) -> None:
    """Create one owner-only closed credential file without replacing a target."""

    target = Path(path)
    if ".." in target.parts:
        raise AdaptiveMemoryError("credential path must not contain parent traversal")
    parent = target.absolute().parent
    current = Path(parent.anchor)
    for part in parent.parts[1:]:
        current /= part
        if current.is_symlink():
            raise AdaptiveMemoryError("credential path must not contain symlinks")
    if not parent.is_dir():
        raise AdaptiveMemoryError("credential output parent does not exist")
    write_json_new(
        target,
        {
            "contract_version": "agiwiki.local-capability.v1",
            "principal_id": _principal_id(principal_id, generate=False),
            "token": _capability_token(token),
        },
    )


def load_local_capability(path: str | os.PathLike[str]) -> tuple[str, str]:
    """Load one private closed credential file without exposing its token."""

    target = require_private_regular_file(path)
    document = load_json_document(target, max_bytes=4096)
    if (
        set(document) != {"contract_version", "principal_id", "token"}
        or document["contract_version"] != "agiwiki.local-capability.v1"
    ):
        raise AdaptiveMemoryError("local capability document is invalid")
    return (
        _principal_id(document["principal_id"], generate=False),
        _capability_token(document["token"]),
    )


def _principal_id(value: str | None, *, generate: bool = True) -> str:
    if value is None and generate:
        return f"principal_{secrets.token_hex(16)}"
    if not isinstance(value, str) or _PRINCIPAL_ID.fullmatch(value) is None:
        raise AdaptiveMemoryError("principal_id is invalid")
    return value


def _capability_token(value: str) -> str:
    if not isinstance(value, str) or _CAPABILITY_TOKEN.fullmatch(value) is None:
        raise AdaptiveMemoryError("local capability credential is invalid")
    return value


def _permissions(value: list[str]) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(item not in _PRINCIPAL_PERMISSIONS for item in value)
    ):
        raise AdaptiveMemoryError("principal permissions are invalid")
    return sorted(set(value))


def _authorize_principal(
    connection: sqlite3.Connection,
    *,
    principal_id: str,
    credential: str,
    permission: str,
) -> None:
    if permission not in _PRINCIPAL_PERMISSIONS:
        raise AdaptiveMemoryError("required principal permission is invalid")
    token = _capability_token(credential)
    row = connection.execute(
        "SELECT token_hash,permissions_json,status FROM adaptive_principals "
        "WHERE principal_id=?",
        (principal_id,),
    ).fetchone()
    if row is None or row["status"] != "active":
        raise AdaptiveMemoryError("principal capability is unavailable")
    expected_hash = sha256_digest(token.encode("utf-8"))
    if not hmac.compare_digest(row["token_hash"], expected_hash):
        raise AdaptiveMemoryError("principal capability is invalid")
    try:
        permissions = json.loads(row["permissions_json"])
    except json.JSONDecodeError as exc:
        raise AdaptiveMemoryError("principal permissions are invalid") from exc
    if (
        not isinstance(permissions, list)
        or canonical_json(permissions) != row["permissions_json"]
        or _permissions(permissions) != permissions
        or permission not in permissions
    ):
        raise AdaptiveMemoryError("principal does not have the required permission")


def _proposal_id(value: str) -> str:
    if not isinstance(value, str) or _PROPOSAL_ID.fullmatch(value) is None:
        raise AdaptiveMemoryError("proposal_id is invalid")
    return value


def _decision_id(value: str | None) -> str:
    if value is None:
        return f"decision_{secrets.token_hex(16)}"
    if not isinstance(value, str) or _DECISION_ID.fullmatch(value) is None:
        raise AdaptiveMemoryError("decision_id is invalid")
    return value


def _application_id(value: str | None) -> str:
    if value is None:
        return f"application_{secrets.token_hex(16)}"
    if not isinstance(value, str) or _APPLICATION_ID.fullmatch(value) is None:
        raise AdaptiveMemoryError("application_id is invalid")
    return value


def _stored_review_proposal(value: str) -> dict[str, Any]:
    try:
        document = json.loads(value)
        if not isinstance(document, dict) or canonical_json(document) != value:
            raise AdaptiveMemoryError("stored review proposal is not canonical")
        return validate_review_proposal(document)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AdaptiveMemoryError("stored review proposal is invalid") from exc


def _stored_review_receipt(value: str) -> dict[str, Any]:
    try:
        document = json.loads(value)
        if not isinstance(document, dict) or canonical_json(document) != value:
            raise AdaptiveMemoryError("stored review receipt is not canonical")
        return validate_review_receipt(document)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AdaptiveMemoryError("stored review receipt is invalid") from exc


def _stored_review_application_receipt(value: str) -> dict[str, Any]:
    try:
        document = json.loads(value)
        if not isinstance(document, dict) or canonical_json(document) != value:
            raise AdaptiveMemoryError(
                "stored review application receipt is not canonical"
            )
        return validate_review_application_receipt(document)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AdaptiveMemoryError(
            "stored review application receipt is invalid"
        ) from exc


def _review_application_plans(
    connection: sqlite3.Connection,
    *,
    application: Mapping[str, Any],
    proposal: Mapping[str, Any],
    decision: Mapping[str, Any],
    scope_type: str,
    scope_key: str,
    now_us: int,
) -> list[dict[str, Any]]:
    candidates = {item["candidate_id"]: item for item in proposal["candidates"]}
    accepted = {
        item["candidate_id"]: item
        for item in decision["decisions"]
        if item["decision"] == "accepted"
    }
    if not accepted:
        raise AdaptiveMemoryError("review decision has no accepted actions to apply")
    unsupported = [
        item["candidate_id"]
        for item in accepted.values()
        if item["selected_action"] == "correct"
    ]
    if unsupported:
        raise AdaptiveMemoryError(
            "accepted correction requires the explicit adaptive correct command"
        )
    supplied = {item["candidate_id"] for item in application["applications"]}
    if supplied != set(accepted):
        raise AdaptiveMemoryError(
            "review application must cover every accepted candidate exactly once"
        )

    plans: list[dict[str, Any]] = []
    targeted_lineages: set[str] = set()
    for item in application["applications"]:
        candidate_id = item["candidate_id"]
        if candidate_id not in candidates:
            raise AdaptiveMemoryError("review application candidate was not proposed")
        selected_action = accepted[candidate_id]["selected_action"]
        if item["action"] != selected_action:
            raise AdaptiveMemoryError(
                "review application action differs from the accepted decision"
            )
        candidate = candidates[candidate_id]
        memories: list[dict[str, Any]] = []
        for memory_id in candidate["memory_ids"]:
            row = connection.execute(
                _SELECT + " WHERE memory_id=? AND scope_type=? AND scope_key=?",
                (memory_id, scope_type, scope_key),
            ).fetchone()
            if row is None:
                raise AdaptiveMemoryError(
                    "review application memory was not found in exact scope"
                )
            memories.append(_record(row))
        if (
            sorted(memory["memory_id"] for memory in memories)
            != sorted(candidate["memory_ids"])
            or sorted(memory["lineage_id"] for memory in memories)
            != sorted(candidate["lineage_ids"])
            or sorted({memory["memory_class"] for memory in memories})
            != sorted(candidate["memory_classes"])
            or any(memory["status"] != "active" for memory in memories)
        ):
            raise AdaptiveMemoryError("review application candidate is stale")

        keep_memory_id = item["keep_memory_id"]
        if candidate["reason"] == "expired":
            if item["action"] != "forget" or len(memories) != 1:
                raise AdaptiveMemoryError("expired candidate only supports forget")
            memory = memories[0]
            valid_to_us = _timestamp_or_none(memory["valid_to"])
            expires_at_us = _timestamp_or_none(memory["retention"]["expires_at"])
            if not (
                (valid_to_us is not None and valid_to_us <= now_us)
                or (expires_at_us is not None and expires_at_us <= now_us)
            ):
                raise AdaptiveMemoryError("expired review candidate is no longer valid")
            targets = memories
        elif candidate["reason"] == "exact_duplicate_content":
            if item["action"] not in {"keep_one", "forget_redundant"}:
                raise AdaptiveMemoryError(
                    "duplicate candidate only supports retaining one memory"
                )
            if keep_memory_id not in candidate["memory_ids"]:
                raise AdaptiveMemoryError(
                    "kept memory must belong to the duplicate candidate"
                )
            if len({memory["content_digest"] for memory in memories}) != 1:
                raise AdaptiveMemoryError("duplicate review candidate is stale")
            if len({memory["lineage_id"] for memory in memories}) != len(memories):
                raise AdaptiveMemoryError("duplicate review candidate is invalid")
            if any(
                timestamp_to_microseconds(memory["valid_from"]) > now_us
                or (
                    memory["valid_to"] is not None
                    and timestamp_to_microseconds(memory["valid_to"]) <= now_us
                )
                or (
                    memory["retention"]["expires_at"] is not None
                    and timestamp_to_microseconds(memory["retention"]["expires_at"])
                    <= now_us
                )
                for memory in memories
            ):
                raise AdaptiveMemoryError("duplicate review candidate is stale")
            targets = [
                memory for memory in memories if memory["memory_id"] != keep_memory_id
            ]
        else:
            raise AdaptiveMemoryError("review application candidate is unsupported")

        target_plans: list[dict[str, Any]] = []
        for target in targets:
            if target["lineage_id"] in targeted_lineages:
                raise AdaptiveMemoryError(
                    "review applications cannot target a lineage twice"
                )
            lineage_rows = connection.execute(
                _SELECT + " WHERE lineage_id=? ORDER BY revision",
                (target["lineage_id"],),
            ).fetchall()
            lineage = [_record(row) for row in lineage_rows]
            if not lineage or any(
                memory["scope"] != {"type": scope_type, "key": scope_key}
                or memory["lineage_id"] != target["lineage_id"]
                for memory in lineage
            ):
                raise AdaptiveMemoryError("review application lineage is invalid")
            revision_count = sum(memory["status"] != "deleted" for memory in lineage)
            if revision_count < 1:
                raise AdaptiveMemoryError(
                    "review application target is already deleted"
                )
            targeted_lineages.add(target["lineage_id"])
            target_plans.append(
                {
                    "memory_id": target["memory_id"],
                    "lineage_id": target["lineage_id"],
                    "revision_count": revision_count,
                }
            )
        if not target_plans:
            raise AdaptiveMemoryError("review application would not mutate any memory")
        plans.append(
            {
                "candidate_id": candidate_id,
                "action": item["action"],
                "keep_memory_id": keep_memory_id,
                "targets": target_plans,
            }
        )
    return plans


def _review_apply_operation_id(
    application_id: str,
    candidate_id: str,
    memory_id: str,
) -> str:
    digest = sha256_digest(
        {
            "application_id": application_id,
            "candidate_id": candidate_id,
            "memory_id": memory_id,
        }
    )
    return "op_" + digest.removeprefix("sha256:")[:32]


def _validate_decisions_for_proposal(
    decisions: list[dict[str, Any]],
    proposal: Mapping[str, Any],
) -> None:
    candidates = {item["candidate_id"]: item for item in proposal["candidates"]}
    if not candidates:
        raise AdaptiveMemoryError("review proposal has no candidates to decide")
    supplied = {item["candidate_id"] for item in decisions}
    if supplied != set(candidates):
        raise AdaptiveMemoryError(
            "review decision must cover every candidate exactly once"
        )
    for item in decisions:
        selected = item["selected_action"]
        if (
            selected is not None
            and selected not in candidates[item["candidate_id"]]["suggested_actions"]
        ):
            raise AdaptiveMemoryError("selected review action was not proposed")


def _operation_request_digest(
    operation_type: str,
    *,
    scope: Mapping[str, str],
    target_memory_id: str | None = None,
    value: Mapping[str, Any] | None = None,
    confirm: bool | None = None,
) -> str:
    return sha256_digest(
        {
            "operation_type": operation_type,
            "scope": dict(scope),
            "target_memory_id": target_memory_id,
            "value": value,
            "confirm": confirm,
        }
    )


def _limit(value: int, *, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise AdaptiveMemoryError(f"limit must be between 1 and {maximum}")
    return value


def _timestamp_or_none(value: str | None) -> int | None:
    return None if value is None else timestamp_to_microseconds(value)


def _time_or_none(value: int | None) -> str | None:
    return None if value is None else microseconds_to_timestamp(value)


_SELECT = """
SELECT memory_id,lineage_id,revision,memory_class,scope_type,scope_key,
       content,content_digest,provenance_type,provenance_digest,
       observed_at_us,valid_from_us,valid_to_us,confidence_ppm,sensitivity,
       retention_mode,expires_at_us,status,supersedes_memory_id,
       created_at_us,updated_at_us
FROM adaptive_memories
"""

_CURRENT = """
AND status='active'
AND valid_from_us<=?
AND (valid_to_us IS NULL OR valid_to_us>?)
AND (expires_at_us IS NULL OR expires_at_us>?)
"""

_SCHEMA_PREFIX = """
PRAGMA journal_mode=DELETE;
PRAGMA synchronous=FULL;
PRAGMA secure_delete=ON;
CREATE TABLE IF NOT EXISTS ledger_meta(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  schema_version INTEGER NOT NULL,
  ledger_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS adaptive_memories(
  memory_id TEXT PRIMARY KEY,
  lineage_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision>=1),
  memory_class TEXT NOT NULL CHECK(memory_class IN ('profile','episode')),
  scope_type TEXT NOT NULL CHECK(scope_type IN ('user','agent','run','workspace')),
  scope_key TEXT NOT NULL CHECK(length(scope_key) BETWEEN 1 AND 256),
  content TEXT,
  content_digest TEXT,
  provenance_type TEXT,
  provenance_digest TEXT,
  observed_at_us INTEGER NOT NULL,
  valid_from_us INTEGER NOT NULL,
  valid_to_us INTEGER,
  confidence_ppm INTEGER NOT NULL CHECK(confidence_ppm BETWEEN 0 AND 1000000),
  sensitivity TEXT NOT NULL CHECK(sensitivity IN ('private','sensitive')),
  retention_mode TEXT NOT NULL CHECK(retention_mode IN ('durable','expiring')),
  expires_at_us INTEGER,
  status TEXT NOT NULL CHECK(status IN ('active','superseded','deleted')),
  supersedes_memory_id TEXT UNIQUE,
  created_at_us INTEGER NOT NULL,
  updated_at_us INTEGER NOT NULL,
  UNIQUE(lineage_id,revision),
  FOREIGN KEY(supersedes_memory_id) REFERENCES adaptive_memories(memory_id),
  CHECK((retention_mode='durable' AND expires_at_us IS NULL) OR
        (retention_mode='expiring' AND expires_at_us IS NOT NULL)),
  CHECK(valid_to_us IS NULL OR valid_to_us>valid_from_us),
  CHECK((status='deleted' AND content IS NULL AND content_digest IS NULL AND
         provenance_type IS NULL AND provenance_digest IS NULL) OR
        (status<>'deleted' AND content IS NOT NULL AND content_digest IS NOT NULL AND
         provenance_type IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_memory_per_lineage
  ON adaptive_memories(lineage_id) WHERE status='active';
CREATE INDEX IF NOT EXISTS adaptive_memory_scope
  ON adaptive_memories(scope_type,scope_key,status,memory_class);
CREATE INDEX IF NOT EXISTS adaptive_memory_expiry
  ON adaptive_memories(status,expires_at_us);
CREATE TABLE IF NOT EXISTS adaptive_memory_events(
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL CHECK(event_type IN ('remember','correct','forget')),
  memory_id TEXT NOT NULL,
  lineage_id TEXT NOT NULL,
  occurred_at_us INTEGER NOT NULL,
  FOREIGN KEY(memory_id) REFERENCES adaptive_memories(memory_id)
);
"""

_CREATE_OPERATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS adaptive_operations(
  operation_id TEXT PRIMARY KEY,
  operation_type TEXT NOT NULL
    CHECK(operation_type IN ('remember','correct','forget')),
  scope_type TEXT NOT NULL CHECK(scope_type IN ('user','agent','run','workspace')),
  scope_key TEXT NOT NULL CHECK(length(scope_key) BETWEEN 1 AND 256),
  target_memory_id TEXT,
  request_digest TEXT,
  result_memory_id TEXT NOT NULL,
  result_lineage_id TEXT NOT NULL,
  result_revision_count INTEGER,
  completed_at_us INTEGER NOT NULL,
  FOREIGN KEY(target_memory_id) REFERENCES adaptive_memories(memory_id),
  FOREIGN KEY(result_memory_id) REFERENCES adaptive_memories(memory_id),
  CHECK(request_digest IS NULL OR
        (length(request_digest)=71 AND request_digest LIKE 'sha256:%')),
  CHECK(operation_type<>'forget' OR request_digest IS NOT NULL),
  CHECK((operation_type='remember' AND target_memory_id IS NULL AND
         result_revision_count IS NULL) OR
        (operation_type='correct' AND target_memory_id IS NOT NULL AND
         result_revision_count IS NULL) OR
        (operation_type='forget' AND target_memory_id IS NOT NULL AND
         result_revision_count>=0))
)
"""
_CREATE_OPERATIONS_LINEAGE_INDEX = """
CREATE INDEX IF NOT EXISTS adaptive_operation_lineage
  ON adaptive_operations(result_lineage_id,operation_type)
"""
_CREATE_PRINCIPALS_TABLE = """
CREATE TABLE IF NOT EXISTS adaptive_principals(
  principal_id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  permissions_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active','revoked')),
  created_at_us INTEGER NOT NULL,
  revoked_at_us INTEGER,
  CHECK(length(token_hash)=71 AND token_hash LIKE 'sha256:%'),
  CHECK((status='active' AND revoked_at_us IS NULL) OR
        (status='revoked' AND revoked_at_us IS NOT NULL))
)
"""
_CREATE_REVIEW_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS adaptive_review_proposals(
      proposal_id TEXT PRIMARY KEY,
      scope_type TEXT NOT NULL
        CHECK(scope_type IN ('user','agent','run','workspace')),
      scope_key TEXT NOT NULL CHECK(length(scope_key) BETWEEN 1 AND 256),
      proposal_digest TEXT NOT NULL,
      proposal_json TEXT NOT NULL,
      created_at_us INTEGER NOT NULL,
      proposed_by_principal_id TEXT REFERENCES adaptive_principals(principal_id),
      UNIQUE(proposal_id,proposal_digest),
      CHECK(length(proposal_digest)=71 AND proposal_digest LIKE 'sha256:%')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS adaptive_review_decisions(
      decision_id TEXT PRIMARY KEY,
      proposal_id TEXT NOT NULL UNIQUE,
      proposal_digest TEXT NOT NULL,
      declared_reviewer TEXT NOT NULL,
      request_digest TEXT NOT NULL,
      receipt_json TEXT NOT NULL,
      decided_at_us INTEGER NOT NULL,
      approved_by_principal_id TEXT REFERENCES adaptive_principals(principal_id),
      FOREIGN KEY(proposal_id,proposal_digest)
        REFERENCES adaptive_review_proposals(proposal_id,proposal_digest),
      CHECK(length(request_digest)=71 AND request_digest LIKE 'sha256:%')
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_adaptive_review_proposals_no_update
    BEFORE UPDATE ON adaptive_review_proposals
    BEGIN SELECT RAISE(ABORT,'adaptive review proposals are append-only'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_adaptive_review_proposals_no_delete
    BEFORE DELETE ON adaptive_review_proposals
    BEGIN SELECT RAISE(ABORT,'adaptive review proposals are append-only'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_adaptive_review_decisions_no_update
    BEFORE UPDATE ON adaptive_review_decisions
    BEGIN SELECT RAISE(ABORT,'adaptive review decisions are append-only'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_adaptive_review_decisions_no_delete
    BEFORE DELETE ON adaptive_review_decisions
    BEGIN SELECT RAISE(ABORT,'adaptive review decisions are append-only'); END
    """,
)
_CREATE_REVIEW_APPLICATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS adaptive_review_applications(
      application_id TEXT PRIMARY KEY,
      decision_id TEXT NOT NULL UNIQUE,
      proposal_id TEXT NOT NULL,
      proposal_digest TEXT NOT NULL,
      request_digest TEXT NOT NULL,
      receipt_json TEXT NOT NULL,
      applied_at_us INTEGER NOT NULL,
      applied_by_principal_id TEXT NOT NULL
        REFERENCES adaptive_principals(principal_id),
      FOREIGN KEY(decision_id) REFERENCES adaptive_review_decisions(decision_id),
      FOREIGN KEY(proposal_id,proposal_digest)
        REFERENCES adaptive_review_proposals(proposal_id,proposal_digest),
      CHECK(length(request_digest)=71 AND request_digest LIKE 'sha256:%')
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_adaptive_review_applications_no_update
    BEFORE UPDATE ON adaptive_review_applications
    BEGIN SELECT RAISE(ABORT,'adaptive review applications are append-only'); END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_adaptive_review_applications_no_delete
    BEFORE DELETE ON adaptive_review_applications
    BEGIN SELECT RAISE(ABORT,'adaptive review applications are append-only'); END
    """,
)
_SCHEMA = "\n".join(
    (
        _SCHEMA_PREFIX,
        _CREATE_OPERATIONS_TABLE + ";",
        _CREATE_OPERATIONS_LINEAGE_INDEX + ";",
        _CREATE_PRINCIPALS_TABLE + ";",
        *(statement + ";" for statement in _CREATE_REVIEW_STATEMENTS),
        *(statement + ";" for statement in _CREATE_REVIEW_APPLICATION_STATEMENTS),
    )
)


__all__ = [
    "ADAPTIVE_SCHEMA_VERSION",
    "AdaptiveMemoryError",
    "AdaptiveMemoryStore",
    "generate_local_capability",
    "load_local_capability",
    "write_local_capability",
]

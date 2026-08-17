from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agiwiki.adaptive import AdaptiveMemoryError, AdaptiveMemoryStore
from agiwiki.cli import main
from agiwiki.codec import sha256_digest
from agiwiki.paths import resolve_home_paths


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def _request(
    content: str = "用户明确要求默认使用中文回答。",
    *,
    scope_key: str = "user-demo",
) -> dict:
    return {
        "contract_version": "agiwiki.adaptive-write.v1",
        "memory_class": "profile",
        "scope": {"type": "user", "key": scope_key},
        "content": content,
        "provenance": {"type": "explicit_user"},
    }


def _store(tmp_path: Path) -> tuple[AdaptiveMemoryStore, Clock]:
    clock = Clock()
    paths = resolve_home_paths({"AGIWIKI_HOME": str(tmp_path / "home")})
    store = AdaptiveMemoryStore(paths, clock=clock)
    metadata = store.initialize(ledger_id="ledger_00000000000000000000000000000000")
    assert metadata["schema_version"] == 5
    assert os.stat(paths.adaptive_db).st_mode & 0o077 == 0
    return store, clock


def _principal(
    store: AdaptiveMemoryStore,
    *,
    principal_id: str = "principal_11111111111111111111111111111111",
    token: str = "agwcap_" + "1" * 64,
    permissions: list[str] | None = None,
) -> tuple[str, str]:
    store.create_principal(
        token=token,
        permissions=permissions or ["review_propose", "review_approve"],
        confirm=True,
        principal_id=principal_id,
    )
    return principal_id, token


def test_remember_get_list_search_are_exact_scope_and_expiry_safe(
    tmp_path: Path,
) -> None:
    store, clock = _store(tmp_path)
    first = store.remember(_request())
    memory_id = first["memory"]["memory_id"]
    store.remember(_request("另一个用户喜欢英文。", scope_key="other-user"))

    assert store.get(memory_id, scope_type="user", scope_key="user-demo")["found"]
    assert not store.get(memory_id, scope_type="user", scope_key="other-user")["found"]
    assert store.list(scope_type="user", scope_key="user-demo")["count"] == 1
    assert store.search("中文", scope_type="user", scope_key="user-demo")["count"] == 1
    assert store.search("中文", scope_type="user", scope_key="other-user")["count"] == 0

    expiring = _request("这个任务需要在今天结束。")
    expiring["memory_class"] = "episode"
    expiring["retention"] = {
        "mode": "expiring",
        "expires_at": "2026-08-15T13:00:00Z",
    }
    expiring_id = store.remember(expiring)["memory"]["memory_id"]
    clock.value += timedelta(hours=2)
    assert not store.get(expiring_id, scope_type="user", scope_key="user-demo")["found"]
    assert store.list(scope_type="user", scope_key="user-demo")["count"] == 1
    assert (
        store.list(
            scope_type="user",
            scope_key="user-demo",
            include_expired=True,
        )["count"]
        == 2
    )
    with pytest.raises(AdaptiveMemoryError, match="only valid for active"):
        store.list(
            scope_type="user",
            scope_key="user-demo",
            status="superseded",
            include_expired=True,
        )


def test_extreme_valid_timestamps_remain_readable_and_forgettable(
    tmp_path: Path,
) -> None:
    store, _ = _store(tmp_path)
    request = _request("极限时间边界仍必须保持可读取和可遗忘。")
    request.update(
        {
            "observed_at": "9999-12-31T23:59:59.999999Z",
            "valid_from": "2026-08-15T00:00:00.000000Z",
            "valid_to": "9999-12-31T23:59:59.999999Z",
            "retention": {
                "mode": "expiring",
                "expires_at": "9999-12-31T23:59:59.999999Z",
            },
        }
    )
    memory = store.remember(request)["memory"]

    fetched = store.get(memory["memory_id"], scope_type="user", scope_key="user-demo")[
        "memory"
    ]
    assert fetched["observed_at"] == "9999-12-31T23:59:59.999999Z"
    assert fetched["valid_to"] == "9999-12-31T23:59:59.999999Z"
    assert fetched["retention"]["expires_at"] == "9999-12-31T23:59:59.999999Z"

    forgotten = store.forget(
        memory["memory_id"],
        scope_type="user",
        scope_key="user-demo",
        confirm=True,
    )
    assert forgotten["forgotten_revisions"] == 1


def test_correction_appends_and_forget_scrubs_the_complete_lineage(
    tmp_path: Path,
) -> None:
    store, clock = _store(tmp_path)
    original = _request()
    original["valid_to"] = "2026-08-20T00:00:00Z"
    old = store.remember(original)["memory"]
    clock.value += timedelta(minutes=1)
    correction = {
        "contract_version": "agiwiki.adaptive-correction.v1",
        "content": "用户明确要求默认使用中英文双语回答。",
        "provenance": {"type": "explicit_user"},
    }
    new = store.correct(
        old["memory_id"],
        correction,
        scope_type="user",
        scope_key="user-demo",
    )["memory"]

    assert new["lineage_id"] == old["lineage_id"]
    assert new["revision"] == 2
    assert new["supersedes_memory_id"] == old["memory_id"]
    assert new["valid_to"] == "2026-08-20T00:00:00.000000Z"
    with pytest.raises(AdaptiveMemoryError, match="active memory"):
        store.correct(
            old["memory_id"],
            correction,
            scope_type="user",
            scope_key="user-demo",
        )
    assert not store.get(old["memory_id"], scope_type="user", scope_key="user-demo")[
        "found"
    ]
    assert store.get(new["memory_id"], scope_type="user", scope_key="user-demo")[
        "found"
    ]

    before = store.list(scope_type="user", scope_key="user-demo", status="superseded")[
        "count"
    ]
    with pytest.raises(AdaptiveMemoryError, match="confirmation"):
        store.forget(old["memory_id"], scope_type="user", scope_key="user-demo")
    assert (
        store.list(scope_type="user", scope_key="user-demo", status="superseded")[
            "count"
        ]
        == before
    )

    forgotten = store.forget(
        old["memory_id"],
        scope_type="user",
        scope_key="user-demo",
        confirm=True,
    )
    assert forgotten["forgotten_revisions"] == 2
    assert store.list(scope_type="user", scope_key="user-demo")["count"] == 0

    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        rows = connection.execute(
            "SELECT status,content,content_digest,provenance_type,provenance_digest "
            "FROM adaptive_memories WHERE lineage_id=?",
            (old["lineage_id"],),
        ).fetchall()
        event_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(adaptive_memory_events)")
        }
        memory_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(adaptive_memories)")
        }
    finally:
        connection.close()
    assert len(rows) == 2
    assert all(row == ("deleted", None, None, None, None) for row in rows)
    assert "content" not in event_columns
    assert "content_digest" not in event_columns
    assert "actor_type" not in event_columns
    assert "created_by" not in memory_columns


def test_invalid_correction_window_rolls_back_without_superseding(
    tmp_path: Path,
) -> None:
    store, _ = _store(tmp_path)
    old = store.remember(_request())["memory"]
    invalid = {
        "contract_version": "agiwiki.adaptive-correction.v1",
        "content": "不会提交的错误时间窗口。",
        "provenance": {"type": "explicit_user"},
        "valid_from": "2026-08-16T00:00:00Z",
        "valid_to": "2026-08-15T00:00:00Z",
    }

    with pytest.raises(ValueError, match="valid_to"):
        store.correct(
            old["memory_id"],
            invalid,
            scope_type="user",
            scope_key="user-demo",
        )
    assert store.get(old["memory_id"], scope_type="user", scope_key="user-demo")[
        "found"
    ]


def test_write_operations_are_request_bound_and_safely_replayed(tmp_path: Path) -> None:
    store, clock = _store(tmp_path)
    remember_operation = "op_11111111111111111111111111111111"
    first = store.remember(_request(), operation_id=remember_operation)
    replay = store.remember(_request(), operation_id=remember_operation)

    assert first["operation_id"] == remember_operation
    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert replay["memory"]["memory_id"] == first["memory"]["memory_id"]
    with pytest.raises(AdaptiveMemoryError, match="another request"):
        store.remember(
            _request("different content"),
            operation_id=remember_operation,
        )

    clock.value += timedelta(minutes=1)
    correction = {
        "contract_version": "agiwiki.adaptive-correction.v1",
        "content": "用户明确要求默认使用中英文双语回答。",
        "provenance": {"type": "explicit_user"},
    }
    correct_operation = "op_22222222222222222222222222222222"
    corrected = store.correct(
        first["memory"]["memory_id"],
        correction,
        scope_type="user",
        scope_key="user-demo",
        operation_id=correct_operation,
    )
    corrected_replay = store.correct(
        first["memory"]["memory_id"],
        correction,
        scope_type="user",
        scope_key="user-demo",
        operation_id=correct_operation,
    )
    assert corrected["replayed"] is False
    assert corrected_replay["replayed"] is True
    assert corrected_replay["memory"]["memory_id"] == corrected["memory"]["memory_id"]
    with pytest.raises(AdaptiveMemoryError, match="another request"):
        store.correct(
            first["memory"]["memory_id"],
            {**correction, "content": "conflicting correction"},
            scope_type="user",
            scope_key="user-demo",
            operation_id=correct_operation,
        )

    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        assert (
            connection.execute("SELECT count(*) FROM adaptive_memories").fetchone()[0]
            == 2
        )
        assert (
            connection.execute("SELECT count(*) FROM adaptive_operations").fetchone()[0]
            == 2
        )
    finally:
        connection.close()


def test_concurrent_same_operation_creates_one_memory(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    operation_id = "op_cccccccccccccccccccccccccccccccc"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: store.remember(_request(), operation_id=operation_id),
                range(2),
            )
        )

    assert {result["memory"]["memory_id"] for result in results} == {
        results[0]["memory"]["memory_id"]
    }
    assert sorted(result["replayed"] for result in results) == [False, True]
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        assert (
            connection.execute("SELECT count(*) FROM adaptive_memories").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT count(*) FROM adaptive_operations").fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_forget_replay_scrubs_content_derived_operation_bindings(
    tmp_path: Path,
) -> None:
    store, _ = _store(tmp_path)
    remember_operation = "op_33333333333333333333333333333333"
    memory = store.remember(
        _request("short preference that must leave no plain request hash"),
        operation_id=remember_operation,
    )["memory"]
    forget_operation = "op_44444444444444444444444444444444"

    forgotten = store.forget(
        memory["memory_id"],
        scope_type="user",
        scope_key="user-demo",
        confirm=True,
        operation_id=forget_operation,
    )
    before_replay = store.paths.adaptive_db.read_bytes()
    before_replay_mtime = store.paths.adaptive_db.stat().st_mtime_ns
    replay = store.forget(
        memory["memory_id"],
        scope_type="user",
        scope_key="user-demo",
        confirm=True,
        operation_id=forget_operation,
    )
    assert forgotten["replayed"] is False
    assert replay["replayed"] is True
    assert replay["forgotten_revisions"] == forgotten["forgotten_revisions"] == 1
    assert store.paths.adaptive_db.read_bytes() == before_replay
    assert store.paths.adaptive_db.stat().st_mtime_ns == before_replay_mtime

    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        bindings = dict(
            connection.execute(
                "SELECT operation_type,request_digest FROM adaptive_operations"
            ).fetchall()
        )
        content = connection.execute(
            "SELECT content,content_digest,provenance_type "
            "FROM adaptive_memories WHERE memory_id=?",
            (memory["memory_id"],),
        ).fetchone()
    finally:
        connection.close()
    assert bindings == {"remember": None, "forget": bindings["forget"]}
    assert bindings["forget"].startswith("sha256:")
    assert content == (None, None, None)
    with pytest.raises(AdaptiveMemoryError, match="erased by a confirmed forget"):
        store.remember(_request(), operation_id=remember_operation)


def test_forget_replay_rejects_tampered_result_binding_before_scrub(
    tmp_path: Path,
) -> None:
    store, _ = _store(tmp_path)
    first = store.remember(_request("first scope content"))["memory"]
    second = store.remember(_request("second scope content", scope_key="other-user"))[
        "memory"
    ]
    operation_id = "op_55555555555555555555555555555555"
    store.forget(
        first["memory_id"],
        scope_type="user",
        scope_key="user-demo",
        confirm=True,
        operation_id=operation_id,
    )
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        connection.execute(
            "UPDATE adaptive_operations SET result_memory_id=?,result_lineage_id=? "
            "WHERE operation_id=?",
            (second["memory_id"], second["lineage_id"], operation_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(AdaptiveMemoryError, match="binding is inconsistent"):
        store.forget(
            first["memory_id"],
            scope_type="user",
            scope_key="user-demo",
            confirm=True,
            operation_id=operation_id,
        )
    assert store.get(second["memory_id"], scope_type="user", scope_key="other-user")[
        "found"
    ]


def test_explicit_init_migrates_v1_without_losing_memory(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    memory = store.remember(_request())["memory"]
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        connection.execute("DROP TABLE adaptive_review_applications")
        connection.execute("DROP TABLE adaptive_review_decisions")
        connection.execute("DROP TABLE adaptive_review_proposals")
        connection.execute("DROP TABLE adaptive_principals")
        connection.execute("DROP INDEX adaptive_operation_lineage")
        connection.execute("DROP TABLE adaptive_operations")
        connection.execute("UPDATE ledger_meta SET schema_version=1")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(AdaptiveMemoryError, match="unsupported"):
        store.get(memory["memory_id"], scope_type="user", scope_key="user-demo")
    migrated = store.initialize()
    assert migrated == {
        "schema_version": 5,
        "ledger_id": "ledger_00000000000000000000000000000000",
        "migrated_from": 1,
    }
    assert store.get(memory["memory_id"], scope_type="user", scope_key="user-demo")[
        "found"
    ]
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        assert {
            row[1]
            for row in connection.execute("PRAGMA table_info(adaptive_operations)")
        } == {
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
    finally:
        connection.close()


def test_explicit_init_migrates_v2_review_schema(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    memory = store.remember(_request())["memory"]
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        connection.execute("DROP TABLE adaptive_review_applications")
        connection.execute("DROP TABLE adaptive_review_decisions")
        connection.execute("DROP TABLE adaptive_review_proposals")
        connection.execute("DROP TABLE adaptive_principals")
        connection.execute("UPDATE ledger_meta SET schema_version=2")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(AdaptiveMemoryError, match="unsupported"):
        store.review_plan(scope_type="user", scope_key="user-demo")
    migrated = store.initialize()
    assert migrated == {
        "schema_version": 5,
        "ledger_id": "ledger_00000000000000000000000000000000",
        "migrated_from": 2,
    }
    assert store.get(memory["memory_id"], scope_type="user", scope_key="user-demo")[
        "found"
    ]


def test_explicit_init_migrates_v3_capability_schema(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        connection.execute("DROP TABLE adaptive_review_applications")
        connection.execute(
            "ALTER TABLE adaptive_review_decisions DROP COLUMN approved_by_principal_id"
        )
        connection.execute(
            "ALTER TABLE adaptive_review_proposals DROP COLUMN proposed_by_principal_id"
        )
        connection.execute("DROP TABLE adaptive_principals")
        connection.execute("UPDATE ledger_meta SET schema_version=3")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(AdaptiveMemoryError, match="unsupported"):
        store.review_plan(scope_type="user", scope_key="user-demo")
    migrated = store.initialize()
    assert migrated == {
        "schema_version": 5,
        "ledger_id": "ledger_00000000000000000000000000000000",
        "migrated_from": 3,
    }
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        assert {
            row[1]
            for row in connection.execute("PRAGMA table_info(adaptive_principals)")
        } == {
            "principal_id",
            "token_hash",
            "permissions_json",
            "status",
            "created_at_us",
            "revoked_at_us",
        }
    finally:
        connection.close()


def test_explicit_init_migrates_v4_application_schema(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        connection.execute("DROP TABLE adaptive_review_applications")
        connection.execute("UPDATE ledger_meta SET schema_version=4")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(AdaptiveMemoryError, match="unsupported"):
        store.review_plan(scope_type="user", scope_key="user-demo")
    migrated = store.initialize()
    assert migrated == {
        "schema_version": 5,
        "ledger_id": "ledger_00000000000000000000000000000000",
        "migrated_from": 4,
    }
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        assert {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(adaptive_review_applications)"
            )
        } == {
            "application_id",
            "decision_id",
            "proposal_id",
            "proposal_digest",
            "request_digest",
            "receipt_json",
            "applied_at_us",
            "applied_by_principal_id",
        }
    finally:
        connection.close()


def test_review_plan_is_content_free_scoped_and_read_only(tmp_path: Path) -> None:
    store, clock = _store(tmp_path)
    duplicate = "identical private preference for deterministic duplicate review"
    first = store.remember(_request(duplicate))["memory"]
    second = store.remember(_request(duplicate))["memory"]
    expiring = _request("expired private episode")
    expiring["memory_class"] = "episode"
    expiring["retention"] = {
        "mode": "expiring",
        "expires_at": "2026-08-15T13:00:00Z",
    }
    expired = store.remember(expiring)["memory"]
    future = _request("future private profile")
    future["valid_from"] = "2026-08-16T12:00:00Z"
    scheduled = store.remember(future)["memory"]
    store.remember(_request("other scope secret", scope_key="other-user"))
    clock.value += timedelta(hours=2)
    before = store.paths.adaptive_db.read_bytes()
    before_mtime = store.paths.adaptive_db.stat().st_mtime_ns

    plan = store.review_plan(scope_type="user", scope_key="user-demo")

    assert plan["contract_version"] == "agiwiki.adaptive-review-proposal.v1"
    assert plan["counts"] == {
        "scanned": 4,
        "current": 2,
        "expired": 1,
        "scheduled": 1,
        "exact_duplicate_groups": 1,
        "candidates": 2,
    }
    assert plan["contains_memory_content"] is False
    assert plan["mutations_applied"] is False
    assert plan["model_invoked"] is False
    assert plan["scan_truncated"] is False
    assert plan["proposal_id"].startswith("review_")
    assert plan["proposal_digest"].startswith("sha256:")
    assert all(
        candidate["candidate_id"].startswith("candidate_")
        for candidate in plan["candidates"]
    )
    assert {candidate["reason"] for candidate in plan["candidates"]} == {
        "expired",
        "exact_duplicate_content",
    }
    exposed_ids = {
        memory_id
        for candidate in plan["candidates"]
        for memory_id in candidate["memory_ids"]
    }
    assert exposed_ids == {
        first["memory_id"],
        second["memory_id"],
        expired["memory_id"],
    }
    assert scheduled["memory_id"] not in exposed_ids
    serialized = json.dumps(plan, ensure_ascii=False)
    for private_content in (
        duplicate,
        "expired private episode",
        "future private profile",
        "other scope secret",
    ):
        assert private_content not in serialized
    assert store.paths.adaptive_db.read_bytes() == before
    assert store.paths.adaptive_db.stat().st_mtime_ns == before_mtime


def test_review_due_is_read_only_scoped_and_reports_pending_work(
    tmp_path: Path,
) -> None:
    store, clock = _store(tmp_path)
    before = store.paths.adaptive_db.read_bytes()
    before_mtime = store.paths.adaptive_db.stat().st_mtime_ns
    initial = store.review_due(
        scope_type="user", scope_key="user-demo", interval="weekly"
    )
    assert initial["state"] == "never_reviewed"
    assert initial["due"] is True
    assert initial["recommended_actions"] == ["create_proposal"]
    assert store.paths.adaptive_db.read_bytes() == before
    assert store.paths.adaptive_db.stat().st_mtime_ns == before_mtime

    principal_id, token = _principal(store)
    apply_id, apply_token = _principal(
        store,
        principal_id="principal_99999999999999999999999999999999",
        token="agwcap_" + "9" * 64,
        permissions=["review_apply"],
    )
    first = store.remember(_request("due status duplicate"))["memory"]
    store.remember(_request("due status duplicate"))
    proposal = store.create_review_proposal(
        scope_type="user",
        scope_key="user-demo",
        principal_id=principal_id,
        credential=token,
    )["proposal"]
    pending = store.review_due(scope_type="user", scope_key="user-demo")
    assert pending["state"] == "pending_decision"
    assert pending["due"] is False
    assert pending["recommended_actions"] == ["decide_existing"]
    candidate = proposal["candidates"][0]
    decision_id = "decision_77777777777777777777777777777777"
    store.decide_review(
        proposal["proposal_id"],
        {
            "contract_version": "agiwiki.adaptive-review-decision.v1",
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["proposal_digest"],
            "decisions": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "decision": "accepted",
                    "selected_action": "keep_one",
                }
            ],
        },
        scope_type="user",
        scope_key="user-demo",
        principal_id=principal_id,
        credential=token,
        confirm=True,
        decision_id=decision_id,
    )
    reviewed = store.review_due(scope_type="user", scope_key="user-demo")
    assert reviewed["state"] == "reviewed"
    assert reviewed["due"] is False
    assert reviewed["counts"]["unapplied_supported_actions"] == 1
    assert reviewed["recommended_actions"] == ["apply_supported_actions"]

    clock.value += timedelta(days=7)
    due = store.review_due(scope_type="user", scope_key="user-demo")
    assert due["due"] is True
    assert due["recommended_actions"] == [
        "apply_supported_actions",
        "create_proposal",
    ]
    store.apply_review_decision(
        decision_id,
        {
            "contract_version": "agiwiki.adaptive-review-application.v1",
            "decision_id": decision_id,
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["proposal_digest"],
            "applications": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "action": "keep_one",
                    "keep_memory_id": first["memory_id"],
                }
            ],
        },
        scope_type="user",
        scope_key="user-demo",
        principal_id=apply_id,
        credential=apply_token,
        confirm=True,
    )
    applied = store.review_due(scope_type="user", scope_key="user-demo")
    assert applied["counts"]["applied_actions"] == 1
    assert applied["counts"]["unapplied_supported_actions"] == 0
    assert applied["recommended_actions"] == ["create_proposal"]
    assert "due status duplicate" not in json.dumps(applied)
    assert (
        store.review_due(scope_type="user", scope_key="another-user")["state"]
        == "never_reviewed"
    )


def test_review_capabilities_separate_propose_approve_and_revoke(
    tmp_path: Path,
) -> None:
    store, _ = _store(tmp_path)
    proposer_id, proposer_token = _principal(
        store,
        principal_id="principal_22222222222222222222222222222222",
        token="agwcap_" + "2" * 64,
        permissions=["review_propose"],
    )
    approver_id, approver_token = _principal(
        store,
        principal_id="principal_33333333333333333333333333333333",
        token="agwcap_" + "3" * 64,
        permissions=["review_approve"],
    )
    store.remember(_request("capability duplicate"))
    store.remember(_request("capability duplicate"))

    with pytest.raises(AdaptiveMemoryError, match="required permission"):
        store.create_review_proposal(
            scope_type="user",
            scope_key="user-demo",
            principal_id=approver_id,
            credential=approver_token,
        )
    with pytest.raises(AdaptiveMemoryError, match="capability is invalid"):
        store.create_review_proposal(
            scope_type="user",
            scope_key="user-demo",
            principal_id=proposer_id,
            credential="agwcap_" + "f" * 64,
        )
    proposal = store.create_review_proposal(
        scope_type="user",
        scope_key="user-demo",
        principal_id=proposer_id,
        credential=proposer_token,
    )["proposal"]
    candidate = proposal["candidates"][0]
    decision = {
        "contract_version": "agiwiki.adaptive-review-decision.v1",
        "proposal_id": proposal["proposal_id"],
        "proposal_digest": proposal["proposal_digest"],
        "decisions": [
            {
                "candidate_id": candidate["candidate_id"],
                "decision": "rejected",
                "selected_action": None,
            }
        ],
    }
    with pytest.raises(AdaptiveMemoryError, match="required permission"):
        store.decide_review(
            proposal["proposal_id"],
            decision,
            scope_type="user",
            scope_key="user-demo",
            principal_id=proposer_id,
            credential=proposer_token,
            confirm=True,
        )
    receipt = store.decide_review(
        proposal["proposal_id"],
        decision,
        scope_type="user",
        scope_key="user-demo",
        principal_id=approver_id,
        credential=approver_token,
        confirm=True,
    )
    assert receipt["approved_by_principal_id"] == approver_id
    assert proposer_token.encode() not in store.paths.adaptive_db.read_bytes()
    assert approver_token.encode() not in store.paths.adaptive_db.read_bytes()

    revoked = store.revoke_principal(approver_id, confirm=True)
    assert revoked["status"] == "revoked"
    with pytest.raises(AdaptiveMemoryError, match="unavailable"):
        store.decide_review(
            proposal["proposal_id"],
            decision,
            scope_type="user",
            scope_key="user-demo",
            principal_id=approver_id,
            credential=approver_token,
            confirm=True,
        )


def test_review_proposal_and_human_decision_are_bound_and_non_applying(
    tmp_path: Path,
) -> None:
    store, clock = _store(tmp_path)
    principal_id, token = _principal(store)
    duplicate = "private duplicate content for review receipt"
    first = store.remember(_request(duplicate))["memory"]
    second = store.remember(_request(duplicate))["memory"]
    expiring = _request("private expired content for review receipt")
    expiring["memory_class"] = "episode"
    expiring["retention"] = {
        "mode": "expiring",
        "expires_at": "2026-08-15T13:00:00Z",
    }
    expired = store.remember(expiring)["memory"]
    clock.value += timedelta(hours=2)

    created = store.create_review_proposal(
        scope_type="user",
        scope_key="user-demo",
        principal_id=principal_id,
        credential=token,
    )
    replay = store.create_review_proposal(
        scope_type="user",
        scope_key="user-demo",
        principal_id=principal_id,
        credential=token,
    )
    proposal = created["proposal"]
    assert created["replayed"] is False
    assert replay["replayed"] is True
    assert replay["proposal"] == proposal
    assert "private duplicate" not in json.dumps(proposal)
    assert "private expired" not in json.dumps(proposal)

    decisions = []
    for candidate in proposal["candidates"]:
        if candidate["reason"] == "expired":
            decisions.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "decision": "accepted",
                    "selected_action": "forget",
                }
            )
        else:
            decisions.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "decision": "rejected",
                    "selected_action": None,
                }
            )
    request = {
        "contract_version": "agiwiki.adaptive-review-decision.v1",
        "proposal_id": proposal["proposal_id"],
        "proposal_digest": proposal["proposal_digest"],
        "decisions": decisions,
    }
    decision_id = "decision_11111111111111111111111111111111"
    with pytest.raises(AdaptiveMemoryError, match="confirmation"):
        store.decide_review(
            proposal["proposal_id"],
            request,
            scope_type="user",
            scope_key="user-demo",
            principal_id=principal_id,
            credential=token,
        )
    receipt = store.decide_review(
        proposal["proposal_id"],
        request,
        scope_type="user",
        scope_key="user-demo",
        principal_id=principal_id,
        credential=token,
        confirm=True,
        decision_id=decision_id,
    )
    replayed = store.decide_review(
        proposal["proposal_id"],
        request,
        scope_type="user",
        scope_key="user-demo",
        principal_id=principal_id,
        credential=token,
        confirm=True,
        decision_id=decision_id,
    )
    assert receipt["mutations_applied"] is False
    assert receipt["contains_memory_content"] is False
    assert receipt["replayed"] is False
    assert replayed["replayed"] is True
    assert store.get(first["memory_id"], scope_type="user", scope_key="user-demo")[
        "found"
    ]
    assert store.get(second["memory_id"], scope_type="user", scope_key="user-demo")[
        "found"
    ]
    assert (
        store.list(
            scope_type="user",
            scope_key="user-demo",
            include_expired=True,
        )["count"]
        == 3
    )
    assert not store.get(
        expired["memory_id"], scope_type="user", scope_key="user-demo"
    )["found"]

    shown = store.show_review(
        proposal["proposal_id"],
        scope_type="user",
        scope_key="user-demo",
    )
    assert shown["found"] is True
    assert shown["decision_receipt"]["decision_id"] == decision_id
    assert (
        store.show_review(
            proposal["proposal_id"],
            scope_type="user",
            scope_key="another-user",
        )["found"]
        is False
    )

    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE adaptive_review_proposals SET scope_key='forged'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM adaptive_review_decisions")
    finally:
        connection.close()


def test_review_decision_rejects_partial_wrong_or_conflicting_receipts(
    tmp_path: Path,
) -> None:
    store, clock = _store(tmp_path)
    principal_id, token = _principal(store)
    duplicate = "another exact duplicate"
    store.remember(_request(duplicate))
    store.remember(_request(duplicate))
    clock.value += timedelta(minutes=1)
    proposal = store.create_review_proposal(
        scope_type="user",
        scope_key="user-demo",
        principal_id=principal_id,
        credential=token,
    )["proposal"]
    candidate = proposal["candidates"][0]
    partial = {
        "contract_version": "agiwiki.adaptive-review-decision.v1",
        "proposal_id": proposal["proposal_id"],
        "proposal_digest": proposal["proposal_digest"],
        "decisions": [
            {
                "candidate_id": candidate["candidate_id"],
                "decision": "accepted",
                "selected_action": "forget",
            }
        ],
    }
    with pytest.raises(AdaptiveMemoryError, match="not proposed"):
        store.decide_review(
            proposal["proposal_id"],
            partial,
            scope_type="user",
            scope_key="user-demo",
            principal_id=principal_id,
            credential=token,
            confirm=True,
        )
    partial["decisions"][0]["selected_action"] = "keep_one"
    accepted = store.decide_review(
        proposal["proposal_id"],
        partial,
        scope_type="user",
        scope_key="user-demo",
        principal_id=principal_id,
        credential=token,
        confirm=True,
        decision_id="decision_22222222222222222222222222222222",
    )
    assert accepted["mutations_applied"] is False
    partial["decisions"][0]["decision"] = "deferred"
    partial["decisions"][0]["selected_action"] = None
    with pytest.raises(AdaptiveMemoryError, match="already has"):
        store.decide_review(
            proposal["proposal_id"],
            partial,
            scope_type="user",
            scope_key="user-demo",
            principal_id=principal_id,
            credential=token,
            confirm=True,
            decision_id="decision_33333333333333333333333333333333",
        )


def test_review_apply_uses_separate_capability_and_is_atomic_and_idempotent(
    tmp_path: Path,
) -> None:
    store, _ = _store(tmp_path)
    proposer_id, proposer_token = _principal(
        store,
        principal_id="principal_44444444444444444444444444444444",
        token="agwcap_" + "4" * 64,
        permissions=["review_propose"],
    )
    approver_id, approver_token = _principal(
        store,
        principal_id="principal_55555555555555555555555555555555",
        token="agwcap_" + "5" * 64,
        permissions=["review_approve"],
    )
    applier_id, applier_token = _principal(
        store,
        principal_id="principal_66666666666666666666666666666666",
        token="agwcap_" + "6" * 64,
        permissions=["review_apply"],
    )
    first = store.remember(_request("review apply duplicate"))["memory"]
    second = store.remember(_request("review apply duplicate"))["memory"]
    proposal = store.create_review_proposal(
        scope_type="user",
        scope_key="user-demo",
        principal_id=proposer_id,
        credential=proposer_token,
    )["proposal"]
    candidate = proposal["candidates"][0]
    decision_request = {
        "contract_version": "agiwiki.adaptive-review-decision.v1",
        "proposal_id": proposal["proposal_id"],
        "proposal_digest": proposal["proposal_digest"],
        "decisions": [
            {
                "candidate_id": candidate["candidate_id"],
                "decision": "accepted",
                "selected_action": "keep_one",
            }
        ],
    }
    decision_id = "decision_44444444444444444444444444444444"
    store.decide_review(
        proposal["proposal_id"],
        decision_request,
        scope_type="user",
        scope_key="user-demo",
        principal_id=approver_id,
        credential=approver_token,
        confirm=True,
        decision_id=decision_id,
    )
    application_request = {
        "contract_version": "agiwiki.adaptive-review-application.v1",
        "decision_id": decision_id,
        "proposal_id": proposal["proposal_id"],
        "proposal_digest": proposal["proposal_digest"],
        "applications": [
            {
                "candidate_id": candidate["candidate_id"],
                "action": "keep_one",
                "keep_memory_id": first["memory_id"],
            }
        ],
    }
    with pytest.raises(AdaptiveMemoryError, match="confirmation"):
        store.apply_review_decision(
            decision_id,
            application_request,
            scope_type="user",
            scope_key="user-demo",
            principal_id=applier_id,
            credential=applier_token,
        )
    with pytest.raises(AdaptiveMemoryError, match="required permission"):
        store.apply_review_decision(
            decision_id,
            application_request,
            scope_type="user",
            scope_key="user-demo",
            principal_id=approver_id,
            credential=approver_token,
            confirm=True,
        )
    application_id = "application_11111111111111111111111111111111"
    receipt = store.apply_review_decision(
        decision_id,
        application_request,
        scope_type="user",
        scope_key="user-demo",
        principal_id=applier_id,
        credential=applier_token,
        confirm=True,
        application_id=application_id,
    )
    replay = store.apply_review_decision(
        decision_id,
        application_request,
        scope_type="user",
        scope_key="user-demo",
        principal_id=applier_id,
        credential=applier_token,
        confirm=True,
        application_id=application_id,
    )
    assert receipt["mutations_applied"] is True
    assert receipt["contains_memory_content"] is False
    assert receipt["applied_by_principal_id"] == applier_id
    assert receipt["results"][0]["kept_memory_id"] == first["memory_id"]
    assert receipt["results"][0]["forgotten_memory_ids"] == [second["memory_id"]]
    assert replay["replayed"] is True
    assert store.get(first["memory_id"], scope_type="user", scope_key="user-demo")[
        "found"
    ]
    assert not store.get(second["memory_id"], scope_type="user", scope_key="user-demo")[
        "found"
    ]
    assert store.list(scope_type="user", scope_key="user-demo")["count"] == 1
    shown = store.show_review(
        proposal["proposal_id"],
        scope_type="user",
        scope_key="user-demo",
    )
    assert shown["application_receipt"]["application_id"] == application_id
    assert applier_token.encode() not in store.paths.adaptive_db.read_bytes()

    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM adaptive_review_applications")
    finally:
        connection.close()


def test_review_apply_rejects_content_generating_actions(
    tmp_path: Path,
) -> None:
    store, clock = _store(tmp_path)
    principal_id, token = _principal(store)
    apply_id, apply_token = _principal(
        store,
        principal_id="principal_77777777777777777777777777777777",
        token="agwcap_" + "7" * 64,
        permissions=["review_apply"],
    )
    expiring = _request("expired review application")
    expiring["memory_class"] = "episode"
    expiring["retention"] = {
        "mode": "expiring",
        "expires_at": "2026-08-15T13:00:00Z",
    }
    memory = store.remember(expiring)["memory"]
    clock.value += timedelta(hours=2)
    proposal = store.create_review_proposal(
        scope_type="user",
        scope_key="user-demo",
        principal_id=principal_id,
        credential=token,
    )["proposal"]
    candidate = proposal["candidates"][0]
    decision_id = "decision_55555555555555555555555555555555"
    store.decide_review(
        proposal["proposal_id"],
        {
            "contract_version": "agiwiki.adaptive-review-decision.v1",
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["proposal_digest"],
            "decisions": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "decision": "accepted",
                    "selected_action": "correct",
                }
            ],
        },
        scope_type="user",
        scope_key="user-demo",
        principal_id=principal_id,
        credential=token,
        confirm=True,
        decision_id=decision_id,
    )
    application = {
        "contract_version": "agiwiki.adaptive-review-application.v1",
        "decision_id": decision_id,
        "proposal_id": proposal["proposal_id"],
        "proposal_digest": proposal["proposal_digest"],
        "applications": [
            {
                "candidate_id": candidate["candidate_id"],
                "action": "forget",
                "keep_memory_id": None,
            }
        ],
    }
    with pytest.raises(AdaptiveMemoryError, match="explicit adaptive correct"):
        store.apply_review_decision(
            decision_id,
            application,
            scope_type="user",
            scope_key="user-demo",
            principal_id=apply_id,
            credential=apply_token,
            confirm=True,
        )
    assert (
        store.list(scope_type="user", scope_key="user-demo", include_expired=True)[
            "count"
        ]
        == 1
    )
    assert memory["memory_id"] in {
        item["memory_id"]
        for item in store.list(
            scope_type="user", scope_key="user-demo", include_expired=True
        )["memories"]
    }


def test_review_apply_rejects_a_stale_candidate_without_partial_mutation(
    tmp_path: Path,
) -> None:
    store, clock = _store(tmp_path)
    principal_id, token = _principal(store)
    apply_id, apply_token = _principal(
        store,
        principal_id="principal_88888888888888888888888888888888",
        token="agwcap_" + "8" * 64,
        permissions=["review_apply"],
    )
    first = store.remember(_request("stale duplicate"))["memory"]
    second = store.remember(_request("stale duplicate"))["memory"]
    proposal = store.create_review_proposal(
        scope_type="user",
        scope_key="user-demo",
        principal_id=principal_id,
        credential=token,
    )["proposal"]
    candidate = proposal["candidates"][0]
    decision_id = "decision_66666666666666666666666666666666"
    store.decide_review(
        proposal["proposal_id"],
        {
            "contract_version": "agiwiki.adaptive-review-decision.v1",
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["proposal_digest"],
            "decisions": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "decision": "accepted",
                    "selected_action": "forget_redundant",
                }
            ],
        },
        scope_type="user",
        scope_key="user-demo",
        principal_id=principal_id,
        credential=token,
        confirm=True,
        decision_id=decision_id,
    )
    clock.value += timedelta(minutes=1)
    corrected = store.correct(
        second["memory_id"],
        {
            "contract_version": "agiwiki.adaptive-correction.v1",
            "content": "stale duplicate corrected",
            "provenance": {"type": "explicit_user"},
        },
        scope_type="user",
        scope_key="user-demo",
    )["memory"]
    with pytest.raises(AdaptiveMemoryError, match="stale"):
        store.apply_review_decision(
            decision_id,
            {
                "contract_version": "agiwiki.adaptive-review-application.v1",
                "decision_id": decision_id,
                "proposal_id": proposal["proposal_id"],
                "proposal_digest": proposal["proposal_digest"],
                "applications": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "action": "forget_redundant",
                        "keep_memory_id": first["memory_id"],
                    }
                ],
            },
            scope_type="user",
            scope_key="user-demo",
            principal_id=apply_id,
            credential=apply_token,
            confirm=True,
        )
    assert store.get(first["memory_id"], scope_type="user", scope_key="user-demo")[
        "found"
    ]
    assert store.get(corrected["memory_id"], scope_type="user", scope_key="user-demo")[
        "found"
    ]
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        assert (
            connection.execute(
                "SELECT count(*) FROM adaptive_review_applications"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def test_existing_future_or_overbroad_database_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    paths = resolve_home_paths({"AGIWIKI_HOME": str(tmp_path / "future-home")})
    paths.root.mkdir(mode=0o700)
    connection = sqlite3.connect(paths.adaptive_db)
    try:
        connection.execute(
            "CREATE TABLE ledger_meta(singleton INTEGER PRIMARY KEY,"
            "schema_version INTEGER NOT NULL,ledger_id TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO ledger_meta VALUES(1,999,'ledger_future')")
        connection.commit()
    finally:
        connection.close()
    os.chmod(paths.adaptive_db, 0o600)
    before = paths.adaptive_db.read_bytes()
    before_stat = paths.adaptive_db.stat()

    with pytest.raises(AdaptiveMemoryError, match="unsupported"):
        AdaptiveMemoryStore(paths).initialize()

    after_stat = paths.adaptive_db.stat()
    assert paths.adaptive_db.read_bytes() == before
    assert after_stat.st_mode == before_stat.st_mode
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns

    os.chmod(paths.adaptive_db, 0o644)
    broad_mode = paths.adaptive_db.stat().st_mode
    with pytest.raises(ValueError, match="permissions"):
        AdaptiveMemoryStore(paths).initialize()
    assert paths.adaptive_db.stat().st_mode == broad_mode

    ordinary, _ = _store(tmp_path / "ordinary")
    connection = sqlite3.connect(ordinary.paths.adaptive_db)
    try:
        connection.execute("UPDATE ledger_meta SET schema_version=999")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(AdaptiveMemoryError, match="unsupported"):
        ordinary.list(scope_type="user", scope_key="user-demo")


def test_persistent_content_and_provenance_tampering_fail_closed(
    tmp_path: Path,
) -> None:
    store, _ = _store(tmp_path)
    memory_id = store.remember(_request())["memory"]["memory_id"]
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        connection.execute(
            "UPDATE adaptive_memories SET content='tampered' WHERE memory_id=?",
            (memory_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(AdaptiveMemoryError, match="digest mismatch"):
        store.get(memory_id, scope_type="user", scope_key="user-demo")
    with pytest.raises(AdaptiveMemoryError, match="digest mismatch"):
        store.correct(
            memory_id,
            {
                "contract_version": "agiwiki.adaptive-correction.v1",
                "content": "corrected",
                "provenance": {"type": "explicit_user"},
            },
            scope_type="user",
            scope_key="user-demo",
        )
    with pytest.raises(AdaptiveMemoryError, match="digest mismatch"):
        store.forget(
            memory_id,
            scope_type="user",
            scope_key="user-demo",
            confirm=True,
        )

    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        connection.execute(
            "UPDATE adaptive_memories SET content=?,content_digest=?,"
            "provenance_type='task_result',provenance_digest=NULL WHERE memory_id=?",
            (_request()["content"], sha256_digest(_request()["content"]), memory_id),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(AdaptiveMemoryError, match="provenance"):
        store.get(memory_id, scope_type="user", scope_key="user-demo")


def test_forget_scope_authorizes_target_then_converges_entire_lineage(
    tmp_path: Path,
) -> None:
    store, clock = _store(tmp_path)
    old = store.remember(_request())["memory"]
    clock.value += timedelta(minutes=1)
    new = store.correct(
        old["memory_id"],
        {
            "contract_version": "agiwiki.adaptive-correction.v1",
            "content": "用户现在要求双语回答。",
            "provenance": {"type": "explicit_user"},
        },
        scope_type="user",
        scope_key="user-demo",
    )["memory"]
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        connection.execute(
            "UPDATE adaptive_memories SET scope_key='other-scope' WHERE memory_id=?",
            (old["memory_id"],),
        )
        connection.commit()
    finally:
        connection.close()

    first = store.forget(
        new["memory_id"],
        scope_type="user",
        scope_key="user-demo",
        confirm=True,
    )
    assert first["forgotten_revisions"] == 2

    resurrected = "resurrected cross-scope content"
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        connection.execute(
            "UPDATE adaptive_memories SET status='active',content=?,content_digest=?,"
            "provenance_type='explicit_user' WHERE memory_id=?",
            (resurrected, sha256_digest(resurrected), old["memory_id"]),
        )
        connection.commit()
    finally:
        connection.close()

    replay = store.forget(
        new["memory_id"],
        scope_type="user",
        scope_key="user-demo",
        confirm=True,
    )
    assert replay["forgotten_revisions"] == 1
    assert replay["replayed"] is False
    connection = sqlite3.connect(store.paths.adaptive_db)
    try:
        remaining = connection.execute(
            "SELECT count(*) FROM adaptive_memories "
            "WHERE lineage_id=? AND content IS NOT NULL",
            (old["lineage_id"],),
        ).fetchone()[0]
    finally:
        connection.close()
    assert remaining == 0


def test_cli_uses_closed_file_input_and_keeps_scope_explicit(
    tmp_path: Path, capsys
) -> None:
    home = tmp_path / "home"
    request = tmp_path / "remember.json"
    request.write_text(json.dumps(_request(), ensure_ascii=False), encoding="utf-8")

    assert main(["--home", str(home), "home", "init"]) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["schema_version"] == 1
    assert not (home / "adaptive.sqlite3").exists()

    assert (
        main(
            [
                "--home",
                str(home),
                "adaptive",
                "remember",
                "--input",
                str(request),
            ]
        )
        == 2
    )
    assert not (home / "adaptive.sqlite3").exists()
    capsys.readouterr()

    assert main(["--home", str(home), "adaptive", "init"]) == 0
    adaptive_initialized = json.loads(capsys.readouterr().out)
    assert adaptive_initialized["schema_version"] == 5

    assert (
        main(
            [
                "--home",
                str(home),
                "adaptive",
                "remember",
                "--input",
                str(request),
                "--operation-id",
                "op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ]
        )
        == 0
    )
    remembered = json.loads(capsys.readouterr().out)
    memory_id = remembered["memory"]["memory_id"]
    assert remembered["replayed"] is False
    assert (
        main(
            [
                "--home",
                str(home),
                "adaptive",
                "remember",
                "--input",
                str(request),
                "--operation-id",
                "op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ]
        )
        == 0
    )
    remembered_replay = json.loads(capsys.readouterr().out)
    assert remembered_replay["replayed"] is True
    assert remembered_replay["memory"]["memory_id"] == memory_id
    assert (home / "adaptive.sqlite3").is_file()

    assert (
        main(
            [
                "--home",
                str(home),
                "adaptive",
                "get",
                memory_id,
                "--scope-type",
                "user",
                "--scope-key",
                "user-demo",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["found"] is True

    assert (
        main(
            [
                "--home",
                str(home),
                "adaptive",
                "review-plan",
                "--scope-type",
                "user",
                "--scope-key",
                "user-demo",
            ]
        )
        == 0
    )
    review = json.loads(capsys.readouterr().out)
    assert review["mutations_applied"] is False
    assert review["contains_memory_content"] is False
    assert (
        main(
            [
                "--home",
                str(home),
                "adaptive",
                "review-due",
                "--scope-type",
                "user",
                "--scope-key",
                "user-demo",
                "--interval",
                "daily",
            ]
        )
        == 0
    )
    due = json.loads(capsys.readouterr().out)
    assert due["state"] == "never_reviewed"
    assert due["mutations_applied"] is False

    forget_args = [
        "--home",
        str(home),
        "adaptive",
        "forget",
        memory_id,
        "--scope-type",
        "user",
        "--scope-key",
        "user-demo",
    ]
    assert main(forget_args) == 2
    assert "confirmation" in capsys.readouterr().err
    assert (
        main(
            [
                *forget_args,
                "--confirm",
                "--operation-id",
                "op_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["forgotten_revisions"] == 1


def test_cli_records_then_separately_applies_review_decision(
    tmp_path: Path,
    capsys,
) -> None:
    home = tmp_path / "home"
    request_path = tmp_path / "memory.json"
    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    assert main(["--home", str(home), "home", "init"]) == 0
    capsys.readouterr()
    assert main(["--home", str(home), "adaptive", "init"]) == 0
    capsys.readouterr()
    proposer_credential = tmp_path / "proposer.credential.json"
    approver_credential = tmp_path / "approver.credential.json"
    applier_credential = tmp_path / "applier.credential.json"
    assert (
        main(
            [
                "--home",
                str(home),
                "adaptive",
                "principal-create",
                "--permissions",
                "review_propose",
                "--principal-id",
                "principal_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "--credential-output",
                str(proposer_credential),
                "--confirm",
            ]
        )
        == 0
    )
    proposer = json.loads(capsys.readouterr().out)
    assert "agwcap_" not in json.dumps(proposer)
    assert proposer["raw_token_stored_in_ledger"] is False
    assert os.stat(proposer_credential).st_mode & 0o077 == 0
    os.chmod(proposer_credential, 0o644)
    assert (
        main(
            [
                "--home",
                str(home),
                "adaptive",
                "review-create",
                "--scope-type",
                "user",
                "--scope-key",
                "user-demo",
                "--credential-file",
                str(proposer_credential),
            ]
        )
        == 2
    )
    assert "permissions" in capsys.readouterr().err
    os.chmod(proposer_credential, 0o600)
    assert (
        main(
            [
                "--home",
                str(home),
                "adaptive",
                "principal-create",
                "--permissions",
                "review_approve",
                "--principal-id",
                "principal_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "--credential-output",
                str(approver_credential),
                "--confirm",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "--home",
                str(home),
                "adaptive",
                "principal-create",
                "--permissions",
                "review_apply",
                "--principal-id",
                "principal_cccccccccccccccccccccccccccccccc",
                "--credential-output",
                str(applier_credential),
                "--confirm",
            ]
        )
        == 0
    )
    capsys.readouterr()
    remembered_ids: list[str] = []
    for operation_id in (
        "op_66666666666666666666666666666666",
        "op_77777777777777777777777777777777",
    ):
        assert (
            main(
                [
                    "--home",
                    str(home),
                    "adaptive",
                    "remember",
                    "--input",
                    str(request_path),
                    "--operation-id",
                    operation_id,
                ]
            )
            == 0
        )
        remembered_ids.append(
            json.loads(capsys.readouterr().out)["memory"]["memory_id"]
        )

    scope = ["--scope-type", "user", "--scope-key", "user-demo"]
    assert (
        main(
            [
                "--home",
                str(home),
                "adaptive",
                "review-create",
                *scope,
                "--credential-file",
                str(proposer_credential),
            ]
        )
        == 0
    )
    proposal_result = json.loads(capsys.readouterr().out)
    proposal = proposal_result["proposal"]
    candidate = proposal["candidates"][0]
    decision_path = tmp_path / "decision.json"
    decision_path.write_text(
        json.dumps(
            {
                "contract_version": "agiwiki.adaptive-review-decision.v1",
                "proposal_id": proposal["proposal_id"],
                "proposal_digest": proposal["proposal_digest"],
                "decisions": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "decision": "accepted",
                        "selected_action": "keep_one",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    decide = [
        "--home",
        str(home),
        "adaptive",
        "review-decide",
        proposal["proposal_id"],
        "--input",
        str(decision_path),
        "--credential-file",
        str(approver_credential),
        "--decision-id",
        "decision_44444444444444444444444444444444",
        *scope,
    ]
    assert main(decide) == 2
    assert "confirmation" in capsys.readouterr().err
    assert main([*decide, "--confirm"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["mutations_applied"] is False
    assert receipt["approved_by_principal_id"] == (
        "principal_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    )
    assert (
        main(
            [
                "--home",
                str(home),
                "adaptive",
                "review-show",
                proposal["proposal_id"],
                *scope,
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["decision_receipt"]["decision_id"] == receipt["decision_id"]
    assert main(["--home", str(home), "adaptive", "list", *scope]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 2
    application_path = tmp_path / "application.json"
    application_path.write_text(
        json.dumps(
            {
                "contract_version": "agiwiki.adaptive-review-application.v1",
                "decision_id": receipt["decision_id"],
                "proposal_id": proposal["proposal_id"],
                "proposal_digest": proposal["proposal_digest"],
                "applications": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "action": "keep_one",
                        "keep_memory_id": remembered_ids[0],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    apply = [
        "--home",
        str(home),
        "adaptive",
        "review-apply",
        receipt["decision_id"],
        "--input",
        str(application_path),
        "--credential-file",
        str(applier_credential),
        "--application-id",
        "application_22222222222222222222222222222222",
        *scope,
    ]
    assert main(apply) == 2
    assert "confirmation" in capsys.readouterr().err
    assert main([*apply, "--confirm"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["mutations_applied"] is True
    assert applied["applied_by_principal_id"] == (
        "principal_cccccccccccccccccccccccccccccccc"
    )
    assert main(["--home", str(home), "adaptive", "list", *scope]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1

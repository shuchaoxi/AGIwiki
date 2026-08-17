from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agiwiki.adaptive_contracts import (
    AdaptiveContractError,
    adaptive_validators,
    microseconds_to_timestamp,
    normalize_adaptive_correction,
    normalize_adaptive_write,
    normalize_review_application,
    parse_adaptive_input,
    timestamp_to_microseconds,
    validate_adaptive_record,
    validate_review_application_receipt,
    validate_review_due,
)
from agiwiki.codec import sha256_digest

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _write() -> dict:
    return {
        "contract_version": "agiwiki.adaptive-write.v1",
        "memory_class": "profile",
        "scope": {"type": "user", "key": "user-demo"},
        "content": "用户明确要求默认使用中文回答。",
        "provenance": {"type": "explicit_user"},
    }


def _record() -> dict:
    content = "用户明确要求默认使用中文回答。"
    return {
        "contract_version": "agiwiki.adaptive-memory.v1",
        "memory_id": "mem_00000000000000000000000000000000",
        "lineage_id": "lineage_00000000000000000000000000000000",
        "revision": 1,
        "memory_class": "profile",
        "scope": {"type": "user", "key": "user-demo"},
        "content": content,
        "content_digest": sha256_digest(content),
        "provenance": {"type": "explicit_user", "digest": None},
        "observed_at": "2026-08-15T12:00:00.000000Z",
        "valid_from": "2026-08-15T12:00:00.000000Z",
        "valid_to": None,
        "confidence": 1.0,
        "sensitivity": "private",
        "retention": {"mode": "durable", "expires_at": None},
        "status": "active",
        "supersedes_memory_id": None,
        "created_at": "2026-08-15T12:00:00.000000Z",
        "updated_at": "2026-08-15T12:00:00.000000Z",
    }


def test_adaptive_schemas_are_closed_and_defaults_are_normalized() -> None:
    assert set(adaptive_validators()) == {
        "adaptive-write",
        "adaptive-correction",
        "adaptive-record",
        "adaptive-review-proposal",
        "adaptive-review-decision",
        "adaptive-review-receipt",
        "adaptive-review-receipt-v2",
        "adaptive-review-application",
        "adaptive-review-application-receipt",
        "adaptive-review-due",
    }

    normalized = normalize_adaptive_write(_write(), now=NOW)

    assert normalized["observed_at"] == "2026-08-15T12:00:00.000000Z"
    assert normalized["valid_from"] == normalized["observed_at"]
    assert normalized["retention"] == {"mode": "durable", "expires_at": None}
    assert normalized["confidence"] == 1.0
    assert normalized["provenance"]["digest"] is None
    assert "created_by" not in normalized


def test_adaptive_input_rejects_unknown_actor_duplicate_and_weak_provenance() -> None:
    candidate = _write()
    candidate["created_by"] = "agent"
    with pytest.raises(AdaptiveContractError, match="Additional properties"):
        normalize_adaptive_write(candidate, now=NOW)

    with pytest.raises(AdaptiveContractError, match="duplicate JSON object key"):
        parse_adaptive_input(b'{"content":"first","content":"second"}')

    candidate = _write()
    candidate["provenance"] = {"type": "authorized_interaction"}
    with pytest.raises(AdaptiveContractError, match="requires a sha256"):
        normalize_adaptive_write(candidate, now=NOW)

    candidate = _write()
    candidate["content"] = "unsafe\x1fcontent"
    with pytest.raises(AdaptiveContractError, match="control character"):
        normalize_adaptive_write(candidate, now=NOW)


def test_adaptive_time_windows_and_correction_shape_are_strict() -> None:
    candidate = _write()
    candidate["valid_from"] = "2026-08-16T00:00:00Z"
    candidate["valid_to"] = "2026-08-15T00:00:00Z"
    with pytest.raises(AdaptiveContractError, match="valid_to"):
        normalize_adaptive_write(candidate, now=NOW)

    correction = {
        "contract_version": "agiwiki.adaptive-correction.v1",
        "content": "改为默认使用中英文双语回答。",
        "provenance": {"type": "explicit_user"},
        "scope": {"type": "user", "key": "other"},
    }
    with pytest.raises(AdaptiveContractError, match="Additional properties"):
        normalize_adaptive_correction(correction)


def test_review_application_contract_is_closed_and_action_specific() -> None:
    request = {
        "contract_version": "agiwiki.adaptive-review-application.v1",
        "decision_id": "decision_" + "1" * 32,
        "proposal_id": "review_" + "2" * 32,
        "proposal_digest": "sha256:" + "3" * 64,
        "applications": [
            {
                "candidate_id": "candidate_" + "4" * 32,
                "action": "keep_one",
                "keep_memory_id": "mem_" + "5" * 32,
            }
        ],
    }
    assert normalize_review_application(request) == request

    invalid = {**request, "applications": [dict(request["applications"][0])]}
    invalid["applications"][0]["keep_memory_id"] = None
    with pytest.raises(AdaptiveContractError, match="requires a kept memory"):
        normalize_review_application(invalid)

    receipt = {
        "contract_version": ("agiwiki.adaptive-review-application-receipt.v1"),
        "application_id": "application_" + "6" * 32,
        "decision_id": request["decision_id"],
        "proposal_id": request["proposal_id"],
        "proposal_digest": request["proposal_digest"],
        "applied_by_principal_id": "principal_" + "7" * 32,
        "applied_at": "2026-08-15T12:00:00.000000Z",
        "results": [
            {
                "candidate_id": request["applications"][0]["candidate_id"],
                "action": "keep_one",
                "kept_memory_id": request["applications"][0]["keep_memory_id"],
                "forgotten_memory_ids": ["mem_" + "8" * 32],
                "forgotten_lineage_ids": ["lineage_" + "9" * 32],
                "forgotten_revision_count": 1,
            }
        ],
        "contains_memory_content": False,
        "mutations_applied": True,
        "replayed": False,
    }
    assert validate_review_application_receipt(receipt) == receipt


def test_review_due_contract_recomputes_time_and_recommendations() -> None:
    status = {
        "contract_version": "agiwiki.adaptive-review-due.v1",
        "scope": {"type": "user", "key": "user-demo"},
        "interval": "weekly",
        "checked_at": "2026-08-22T12:00:00.000000Z",
        "state": "reviewed",
        "due": True,
        "last_proposal_id": "review_" + "1" * 32,
        "last_proposal_at": "2026-08-15T11:00:00.000000Z",
        "last_decision_id": "decision_" + "2" * 32,
        "last_decision_at": "2026-08-15T12:00:00.000000Z",
        "last_application_id": None,
        "last_application_at": None,
        "next_due_at": "2026-08-22T12:00:00.000000Z",
        "counts": {
            "candidates": 2,
            "accepted_actions": 2,
            "applied_actions": 0,
            "unapplied_supported_actions": 1,
            "declared_corrections": 1,
        },
        "recommended_actions": [
            "apply_supported_actions",
            "correct_manually",
            "create_proposal",
        ],
        "contains_memory_content": False,
        "mutations_applied": False,
        "model_invoked": False,
    }
    assert validate_review_due(status) == status
    status["due"] = False
    with pytest.raises(AdaptiveContractError, match="time binding"):
        validate_review_due(status)


def test_timestamp_microseconds_round_trip_full_supported_datetime_range() -> None:
    for value in (
        "0001-01-01T00:00:00.000001Z",
        "2026-08-15T12:00:00.123456Z",
        "9999-12-31T23:59:59.999999Z",
    ):
        assert microseconds_to_timestamp(timestamp_to_microseconds(value)) == value


def test_adaptive_record_rejects_inconsistent_lifecycle_semantics() -> None:
    assert validate_adaptive_record(_record())["status"] == "active"

    tombstone = _record()
    tombstone.update(
        status="deleted", content=None, content_digest=None, provenance=None
    )
    assert validate_adaptive_record(tombstone)["status"] == "deleted"

    deleted_with_content = _record()
    deleted_with_content["status"] = "deleted"
    with pytest.raises(AdaptiveContractError, match="None was expected"):
        validate_adaptive_record(deleted_with_content)

    null_valid_from = _record()
    null_valid_from["valid_from"] = None
    with pytest.raises(AdaptiveContractError, match="not of type 'string'"):
        validate_adaptive_record(null_valid_from)

    invalid_retention = _record()
    invalid_retention["retention"] = {
        "mode": "durable",
        "expires_at": "2026-08-16T12:00:00Z",
    }
    with pytest.raises(AdaptiveContractError, match="None was expected"):
        validate_adaptive_record(invalid_retention)

    missing_predecessor = _record()
    missing_predecessor["revision"] = 2
    with pytest.raises(AdaptiveContractError, match="not of type 'string'"):
        validate_adaptive_record(missing_predecessor)

    wrong_digest = _record()
    wrong_digest["content_digest"] = "sha256:" + "f" * 64
    with pytest.raises(AdaptiveContractError, match="digest mismatch"):
        validate_adaptive_record(wrong_digest)

    invalid_window = _record()
    invalid_window["valid_to"] = invalid_window["valid_from"]
    with pytest.raises(AdaptiveContractError, match="valid_to must follow"):
        validate_adaptive_record(invalid_window)

    self_superseding = _record()
    self_superseding["revision"] = 2
    self_superseding["supersedes_memory_id"] = self_superseding["memory_id"]
    with pytest.raises(AdaptiveContractError, match="cannot supersede itself"):
        validate_adaptive_record(self_superseding)

    incomplete_provenance = _record()
    incomplete_provenance["provenance"] = {
        "type": "authorized_interaction",
        "digest": None,
    }
    with pytest.raises(AdaptiveContractError, match="not valid under"):
        validate_adaptive_record(incomplete_provenance)

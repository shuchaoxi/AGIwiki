"""Closed contracts for explicitly authored local Adaptive Memory."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .codec import JSONDocumentError, canonical_json, load_json_document, sha256_digest

ADAPTIVE_WRITE_CONTRACT = "agiwiki.adaptive-write.v1"
ADAPTIVE_CORRECTION_CONTRACT = "agiwiki.adaptive-correction.v1"
ADAPTIVE_RECORD_CONTRACT = "agiwiki.adaptive-memory.v1"
ADAPTIVE_REVIEW_PROPOSAL_CONTRACT = "agiwiki.adaptive-review-proposal.v1"
ADAPTIVE_REVIEW_DECISION_CONTRACT = "agiwiki.adaptive-review-decision.v1"
ADAPTIVE_REVIEW_RECEIPT_CONTRACT = "agiwiki.adaptive-review-decision-receipt.v1"
ADAPTIVE_REVIEW_RECEIPT_V2_CONTRACT = "agiwiki.adaptive-review-decision-receipt.v2"
ADAPTIVE_REVIEW_APPLICATION_CONTRACT = "agiwiki.adaptive-review-application.v1"
ADAPTIVE_REVIEW_APPLICATION_RECEIPT_CONTRACT = (
    "agiwiki.adaptive-review-application-receipt.v1"
)
ADAPTIVE_REVIEW_DUE_CONTRACT = "agiwiki.adaptive-review-due.v1"
MAX_ADAPTIVE_INPUT_BYTES = 64 * 1024

_SCHEMAS = {
    "adaptive-write": "adaptive-write.schema.json",
    "adaptive-correction": "adaptive-correction.schema.json",
    "adaptive-record": "adaptive-record.schema.json",
    "adaptive-review-proposal": "adaptive-review-proposal.schema.json",
    "adaptive-review-decision": "adaptive-review-decision.schema.json",
    "adaptive-review-receipt": "adaptive-review-decision-receipt.schema.json",
    "adaptive-review-receipt-v2": ("adaptive-review-decision-receipt-v2.schema.json"),
    "adaptive-review-application": "adaptive-review-application.schema.json",
    "adaptive-review-application-receipt": (
        "adaptive-review-application-receipt.schema.json"
    ),
    "adaptive-review-due": "adaptive-review-due.schema.json",
}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class AdaptiveContractError(JSONDocumentError):
    """An Adaptive Memory request or record violates its closed contract."""


@lru_cache(maxsize=1)
def adaptive_validators() -> dict[str, Draft202012Validator]:
    result: dict[str, Draft202012Validator] = {}
    root = Path(__file__).with_name("schemas")
    for name, filename in _SCHEMAS.items():
        try:
            document = json.loads((root / filename).read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(document)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise AdaptiveContractError(
                f"packaged adaptive schema is invalid: {filename}"
            ) from exc
        result[name] = Draft202012Validator(
            document,
            format_checker=FormatChecker(),
        )
    return result


def load_adaptive_input(path: str) -> dict[str, Any]:
    """Load one bounded request file; ``-`` is handled by the CLI."""

    return load_json_document(path, max_bytes=MAX_ADAPTIVE_INPUT_BYTES)


def parse_adaptive_input(payload: bytes) -> dict[str, Any]:
    """Parse a bounded stdin payload without accepting duplicate keys."""

    if len(payload) > MAX_ADAPTIVE_INPUT_BYTES:
        raise AdaptiveContractError("adaptive input exceeds the size limit")
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_closed_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdaptiveContractError("adaptive input is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise AdaptiveContractError("adaptive input root must be an object")
    canonical_json(value)
    return value


def normalize_adaptive_write(
    value: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    _validate("adaptive-write", candidate)
    current = normalize_timestamp(now)
    candidate["content"] = _trimmed_content(candidate["content"])
    candidate["scope"]["key"] = _scope_key(candidate["scope"]["key"])
    _normalize_provenance(candidate["provenance"])
    candidate["observed_at"] = _optional_timestamp(
        candidate.get("observed_at"), current
    )
    candidate["valid_from"] = _optional_timestamp(
        candidate.get("valid_from"), candidate["observed_at"]
    )
    candidate["valid_to"] = _nullable_timestamp(candidate.get("valid_to"))
    candidate["confidence"] = float(candidate.get("confidence", 1.0))
    candidate["sensitivity"] = candidate.get("sensitivity", "private")
    candidate["retention"] = _normalize_retention(candidate.get("retention"))
    _validate_time_window(candidate, now=current)
    _validate("adaptive-write", candidate)
    canonical_json(candidate)
    return candidate


def normalize_adaptive_correction(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    _validate("adaptive-correction", candidate)
    candidate["content"] = _trimmed_content(candidate["content"])
    _normalize_provenance(candidate["provenance"])
    for key in ("observed_at", "valid_from"):
        if key in candidate and candidate[key] is not None:
            candidate[key] = normalize_timestamp(candidate[key])
    if "valid_to" in candidate:
        candidate["valid_to"] = _nullable_timestamp(candidate["valid_to"])
    if "confidence" in candidate:
        candidate["confidence"] = float(candidate["confidence"])
    if "retention" in candidate:
        candidate["retention"] = _normalize_retention(candidate["retention"])
    _validate("adaptive-correction", candidate)
    canonical_json(candidate)
    return candidate


def validate_adaptive_record(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    _validate("adaptive-record", candidate)
    _validate_record_semantics(candidate)
    canonical_json(candidate)
    return candidate


def validate_review_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    _validate("adaptive-review-proposal", candidate)
    body = {
        key: item
        for key, item in candidate.items()
        if key not in {"proposal_id", "proposal_digest"}
    }
    expected_digest = sha256_digest(body)
    expected_id = "review_" + expected_digest.removeprefix("sha256:")[:32]
    if candidate["proposal_digest"] != expected_digest:
        raise AdaptiveContractError("review proposal digest mismatch")
    if candidate["proposal_id"] != expected_id:
        raise AdaptiveContractError("review proposal identifier mismatch")
    if candidate["counts"]["candidates"] != len(candidate["candidates"]):
        raise AdaptiveContractError("review proposal candidate count mismatch")
    if (
        candidate["counts"]["current"]
        + candidate["counts"]["expired"]
        + candidate["counts"]["scheduled"]
        != candidate["counts"]["scanned"]
    ):
        raise AdaptiveContractError("review proposal scan counts mismatch")
    identifiers: set[str] = set()
    for item in candidate["candidates"]:
        body = {key: value for key, value in item.items() if key != "candidate_id"}
        expected = _review_candidate_id(candidate["snapshot_digest"], body)
        if item["candidate_id"] != expected or expected in identifiers:
            raise AdaptiveContractError("review proposal candidate binding mismatch")
        identifiers.add(expected)
    canonical_json(candidate)
    return candidate


def normalize_review_decision(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    _validate("adaptive-review-decision", candidate)
    _validate_decision_items(candidate["decisions"], subject="decision")
    canonical_json(candidate)
    return candidate


def validate_review_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    contract = candidate.get("contract_version")
    if contract == ADAPTIVE_REVIEW_RECEIPT_CONTRACT:
        schema = "adaptive-review-receipt"
    elif contract == ADAPTIVE_REVIEW_RECEIPT_V2_CONTRACT:
        schema = "adaptive-review-receipt-v2"
    else:
        raise AdaptiveContractError("review receipt contract version is unsupported")
    _validate(schema, candidate)
    _validate_decision_items(candidate["decisions"], subject="receipt")
    canonical_json(candidate)
    return candidate


def normalize_review_application(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    _validate("adaptive-review-application", candidate)
    identifiers = [item["candidate_id"] for item in candidate["applications"]]
    if len(identifiers) != len(set(identifiers)):
        raise AdaptiveContractError("review application candidate IDs must be unique")
    for item in candidate["applications"]:
        if item["action"] == "forget" and item["keep_memory_id"] is not None:
            raise AdaptiveContractError(
                "forget review application cannot select a kept memory"
            )
        if item["action"] != "forget" and item["keep_memory_id"] is None:
            raise AdaptiveContractError(
                "duplicate review application requires a kept memory"
            )
    canonical_json(candidate)
    return candidate


def validate_review_application_receipt(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    _validate("adaptive-review-application-receipt", candidate)
    identifiers = [item["candidate_id"] for item in candidate["results"]]
    if len(identifiers) != len(set(identifiers)):
        raise AdaptiveContractError(
            "review application receipt candidate IDs must be unique"
        )
    for item in candidate["results"]:
        if item["action"] == "forget" and item["kept_memory_id"] is not None:
            raise AdaptiveContractError(
                "forget application receipt cannot retain a memory"
            )
        if item["action"] != "forget" and item["kept_memory_id"] is None:
            raise AdaptiveContractError(
                "duplicate application receipt requires a kept memory"
            )
    canonical_json(candidate)
    return candidate


def validate_review_due(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    _validate("adaptive-review-due", candidate)
    counts = candidate["counts"]
    if counts["accepted_actions"] != (
        counts["applied_actions"]
        + counts["unapplied_supported_actions"]
        + counts["declared_corrections"]
    ):
        raise AdaptiveContractError("review due action counts are inconsistent")
    state = candidate["state"]
    if state == "never_reviewed":
        if (
            candidate["due"] is not True
            or any(
                candidate[field] is not None
                for field in (
                    "last_proposal_id",
                    "last_proposal_at",
                    "last_decision_id",
                    "last_decision_at",
                    "last_application_id",
                    "last_application_at",
                    "next_due_at",
                )
            )
            or any(counts.values())
            or candidate["recommended_actions"] != ["create_proposal"]
        ):
            raise AdaptiveContractError("never-reviewed due status is inconsistent")
    elif state == "pending_decision":
        if (
            candidate["due"] is not False
            or candidate["last_proposal_id"] is None
            or candidate["last_proposal_at"] is None
            or any(
                candidate[field] is not None
                for field in (
                    "last_decision_id",
                    "last_decision_at",
                    "last_application_id",
                    "last_application_at",
                    "next_due_at",
                )
            )
            or any(
                counts[field] != 0
                for field in (
                    "accepted_actions",
                    "applied_actions",
                    "unapplied_supported_actions",
                    "declared_corrections",
                )
            )
            or candidate["recommended_actions"] != ["decide_existing"]
        ):
            raise AdaptiveContractError("pending review due status is inconsistent")
    else:
        if (
            candidate["last_proposal_id"] is None
            or candidate["last_proposal_at"] is None
            or candidate["last_decision_id"] is None
            or candidate["last_decision_at"] is None
            or candidate["next_due_at"] is None
            or (
                (candidate["last_application_id"] is None)
                != (candidate["last_application_at"] is None)
            )
        ):
            raise AdaptiveContractError("reviewed due status is incomplete")
        interval = timedelta(days=1 if candidate["interval"] == "daily" else 7)
        expected_next = normalize_timestamp(
            datetime.fromisoformat(candidate["last_decision_at"].replace("Z", "+00:00"))
            + interval
        )
        if candidate["next_due_at"] != expected_next or candidate["due"] != (
            timestamp_to_microseconds(candidate["checked_at"])
            >= timestamp_to_microseconds(expected_next)
        ):
            raise AdaptiveContractError("review due time binding is inconsistent")
        expected_actions: list[str] = []
        if counts["unapplied_supported_actions"]:
            expected_actions.append("apply_supported_actions")
        if counts["declared_corrections"]:
            expected_actions.append("correct_manually")
        if candidate["due"]:
            expected_actions.append("create_proposal")
        if not expected_actions:
            expected_actions.append("wait")
        if candidate["recommended_actions"] != expected_actions:
            raise AdaptiveContractError("review due recommendations are inconsistent")
    canonical_json(candidate)
    return candidate


def review_candidate_id(snapshot_digest: str, value: Mapping[str, Any]) -> str:
    return _review_candidate_id(snapshot_digest, dict(value))


def _review_candidate_id(snapshot_digest: str, value: Mapping[str, Any]) -> str:
    digest = sha256_digest(
        {"snapshot_digest": snapshot_digest, "candidate": dict(value)}
    )
    return "candidate_" + digest.removeprefix("sha256:")[:32]


def _validate_decision_items(
    decisions: list[Mapping[str, Any]],
    *,
    subject: str,
) -> None:
    identifiers = [item["candidate_id"] for item in decisions]
    if len(identifiers) != len(set(identifiers)):
        raise AdaptiveContractError(f"review {subject} candidate IDs must be unique")
    for item in decisions:
        selected = item["selected_action"]
        if item["decision"] == "accepted" and selected is None:
            raise AdaptiveContractError(
                f"accepted review {subject} requires selected_action"
            )
        if item["decision"] != "accepted" and selected is not None:
            raise AdaptiveContractError(
                f"rejected or deferred review {subject} cannot select an action"
            )


def normalize_timestamp(value: str | datetime) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AdaptiveContractError("timestamp must be ISO 8601") from exc
    else:
        raise AdaptiveContractError("timestamp must be ISO 8601")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdaptiveContractError("timestamp must include a timezone")
    return (
        parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def timestamp_to_microseconds(value: str) -> int:
    parsed = datetime.fromisoformat(normalize_timestamp(value).replace("Z", "+00:00"))
    delta = parsed - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def microseconds_to_timestamp(value: int) -> str:
    if type(value) is not int:
        raise AdaptiveContractError("timestamp microseconds must be an integer")
    try:
        parsed = _EPOCH + timedelta(microseconds=value)
    except OverflowError as exc:
        raise AdaptiveContractError("timestamp microseconds are out of range") from exc
    return normalize_timestamp(parsed)


def _validate_record_semantics(value: Mapping[str, Any]) -> None:
    status = value["status"]
    if status == "deleted":
        if any(
            value[field] is not None
            for field in ("content", "content_digest", "provenance")
        ):
            raise AdaptiveContractError(
                "deleted adaptive record must not retain content or provenance"
            )
    else:
        if value["content_digest"] != sha256_digest(value["content"]):
            raise AdaptiveContractError("adaptive record content digest mismatch")
        provenance = value["provenance"]
        if provenance["type"] != "explicit_user" and provenance["digest"] is None:
            raise AdaptiveContractError(
                "non-user adaptive record provenance requires a digest"
            )

    revision = value["revision"]
    supersedes = value["supersedes_memory_id"]
    if (revision == 1 and supersedes is not None) or (
        revision > 1 and supersedes is None
    ):
        raise AdaptiveContractError(
            "adaptive record revision and supersedes_memory_id disagree"
        )
    if supersedes == value["memory_id"]:
        raise AdaptiveContractError("adaptive record cannot supersede itself")

    valid_from = timestamp_to_microseconds(value["valid_from"])
    valid_to = value["valid_to"]
    if valid_to is not None and timestamp_to_microseconds(valid_to) <= valid_from:
        raise AdaptiveContractError("adaptive record valid_to must follow valid_from")

    retention = value["retention"]
    expires_at = retention["expires_at"]
    if (retention["mode"] == "durable") != (expires_at is None):
        raise AdaptiveContractError("adaptive record retention fields disagree")
    if expires_at is not None and timestamp_to_microseconds(expires_at) <= valid_from:
        raise AdaptiveContractError("adaptive record expires_at must follow valid_from")

    if timestamp_to_microseconds(value["updated_at"]) < timestamp_to_microseconds(
        value["created_at"]
    ):
        raise AdaptiveContractError(
            "adaptive record updated_at must not precede created_at"
        )


def _validate(name: str, value: Mapping[str, Any]) -> None:
    errors = sorted(
        adaptive_validators()[name].iter_errors(dict(value)),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            str(error.validator),
            error.message,
        ),
    )
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(
            str(part).replace("~", "~0").replace("/", "~1")
            for part in error.absolute_path
        )
        raise AdaptiveContractError(
            f"{name} invalid at {pointer or '/'}: {error.message}"
        )


def _trimmed_content(value: str) -> str:
    if value != value.strip() or any(
        ord(character) < 32 and character not in "\t\n\r" for character in value
    ):
        raise AdaptiveContractError(
            "content must be trimmed and contain no unsafe control character"
        )
    return value


def _scope_key(value: str) -> str:
    if value != value.strip() or any(ord(character) < 32 for character in value):
        raise AdaptiveContractError("scope key must be trimmed safe text")
    return value


def _normalize_provenance(value: dict[str, Any]) -> None:
    value.setdefault("digest", None)
    if value["type"] != "explicit_user" and value["digest"] is None:
        raise AdaptiveContractError(
            "non-user provenance requires a sha256 source digest"
        )


def _normalize_retention(value: Any) -> dict[str, Any]:
    result = (
        {"mode": "durable", "expires_at": None}
        if value is None
        else deepcopy(dict(value))
    )
    result["expires_at"] = _nullable_timestamp(result.get("expires_at"))
    if result.get("mode") == "durable" and result["expires_at"] is not None:
        raise AdaptiveContractError("durable memory cannot have expires_at")
    if result.get("mode") == "expiring" and result["expires_at"] is None:
        raise AdaptiveContractError("expiring memory requires expires_at")
    return result


def _validate_time_window(value: Mapping[str, Any], *, now: str) -> None:
    valid_from = timestamp_to_microseconds(value["valid_from"])
    valid_to = value["valid_to"]
    if valid_to is not None and timestamp_to_microseconds(valid_to) <= valid_from:
        raise AdaptiveContractError("valid_to must be later than valid_from")
    expires_at = value["retention"]["expires_at"]
    if expires_at is not None and timestamp_to_microseconds(expires_at) <= max(
        valid_from, timestamp_to_microseconds(now)
    ):
        raise AdaptiveContractError("expires_at must be in the future")


def _optional_timestamp(value: Any, default: str) -> str:
    return default if value is None else normalize_timestamp(value)


def _nullable_timestamp(value: Any) -> str | None:
    return None if value is None else normalize_timestamp(value)


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdaptiveContractError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise AdaptiveContractError(f"non-finite JSON number is forbidden: {value}")


__all__ = [
    "ADAPTIVE_CORRECTION_CONTRACT",
    "ADAPTIVE_RECORD_CONTRACT",
    "ADAPTIVE_REVIEW_DECISION_CONTRACT",
    "ADAPTIVE_REVIEW_APPLICATION_CONTRACT",
    "ADAPTIVE_REVIEW_APPLICATION_RECEIPT_CONTRACT",
    "ADAPTIVE_REVIEW_DUE_CONTRACT",
    "ADAPTIVE_REVIEW_PROPOSAL_CONTRACT",
    "ADAPTIVE_REVIEW_RECEIPT_CONTRACT",
    "ADAPTIVE_REVIEW_RECEIPT_V2_CONTRACT",
    "ADAPTIVE_WRITE_CONTRACT",
    "AdaptiveContractError",
    "adaptive_validators",
    "load_adaptive_input",
    "microseconds_to_timestamp",
    "normalize_adaptive_correction",
    "normalize_adaptive_write",
    "normalize_review_decision",
    "normalize_review_application",
    "normalize_timestamp",
    "parse_adaptive_input",
    "timestamp_to_microseconds",
    "review_candidate_id",
    "validate_adaptive_record",
    "validate_review_proposal",
    "validate_review_application_receipt",
    "validate_review_due",
    "validate_review_receipt",
]

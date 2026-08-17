from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

import agiwiki.authoring as authoring_module
from agiwiki.authoring import AuthoringController, AuthoringError
from agiwiki.authoring_contracts import (
    AuthoringContractError,
    normalize_author_batch_result,
    validate_author_plan,
)
from agiwiki.cli import main
from agiwiki.codec import load_json_document, sha256_digest, stable_id, write_json_new
from agiwiki.workspace import initialize_workspace, validate_workspace

ENTRY_ID = "entry_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    initialize_workspace(root, slug="book-memory", title="Book Memory")
    return root


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "manual.md"
    path.write_text(
        "第一行事实。\n第二行概念。\n第三行步骤。\n第四行验证。\n", encoding="utf-8"
    )
    return path


def _entry(source_id: str) -> dict:
    return {
        "contract_version": "agiwiki.entry.v1",
        "entry_id": ENTRY_ID,
        "kind": "fact",
        "title": "手册中的可复用事实",
        "summary": "这条带有精确来源定位的事实用于验证分批资料编译流程。",
        "content": {
            "statement": "手册明确说明第一批资料包含可独立复用并可再次核验的事实。",
            "qualifiers": [{"name": "版本", "value": "测试版"}],
        },
        "keywords": ["手册事实", "分批编译"],
        "applies_to": [],
        "relations": [],
        "source_refs": [
            {
                "source_id": source_id,
                "locator": {"type": "line_range", "value": "1-2"},
                "support_level": "direct",
            }
        ],
    }


def _result(plan_id: str, batch_id: str, *, skipped: bool = False) -> dict:
    return {
        "contract_version": "agiwiki.author-batch-result.v1",
        "plan_id": plan_id,
        "batch_id": batch_id,
        "outcome": "skipped" if skipped else "completed",
        "measurement_source": "provider",
        "input_tokens": 5,
        "output_tokens": 5,
        "entry_ids": [] if skipped else [ENTRY_ID],
    }


def _revised_entry(source_id: str, *, marker: str = "reviewed") -> dict:
    value = _entry(source_id)
    value["summary"] = (
        "独立复核后修订的事实记忆保留原始适用范围、来源定位和可再次核验的完整结论。"
        f" 标记：{marker}。"
    )
    value["content"]["statement"] = (
        "独立复核确认这条事实必须保留其限定条件，并以修订后的完整表述供后续任务复用。"
        f" 标记：{marker}。"
    )
    return value


def _recompute_plan_identity(value: dict) -> dict:
    candidate = json.loads(json.dumps(value))
    seed = {
        key: item
        for key, item in candidate.items()
        if key not in {"plan_id", "batches", "plan_digest"}
    }
    candidate["plan_id"] = stable_id("authorplan", seed)
    for batch in candidate["batches"]:
        batch["batch_id"] = stable_id(
            "authorbatch",
            {
                "plan_id": candidate["plan_id"],
                "ordinal": batch["ordinal"],
                "locator": batch["locator"],
            },
        )
    body = {key: item for key, item in candidate.items() if key != "plan_digest"}
    candidate["plan_digest"] = sha256_digest(body)
    return validate_author_plan(candidate)


def _legacy_plan(value: dict) -> dict:
    candidate = json.loads(json.dumps(value))
    candidate["contract_version"] = "agiwiki.author-plan.v1"
    candidate["source"].pop("canonical_uri")
    return _recompute_plan_identity(candidate)


def test_plan_next_record_resume_and_complete_without_copying_source(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    source = _source(tmp_path)
    controller = AuthoringController()

    receipt = controller.plan(
        source,
        workspace=workspace,
        batch_size=2,
        tokens_per_unit=10,
        budget_tokens=1_000,
        max_entries=2,
        language="zh-CN",
    )
    replay = controller.plan(
        source,
        workspace=workspace,
        batch_size=2,
        tokens_per_unit=10,
        budget_tokens=1_000,
        max_entries=2,
        language="zh-CN",
    )
    assert replay["plan_id"] == receipt["plan_id"]
    assert replay["replayed"] is True
    assert receipt["batch_count"] == 2
    assert os.stat(Path(receipt["plan_path"])).st_mode & 0o077 == 0

    first = controller.next_batch(receipt["plan_id"], workspace=workspace)
    assert first["status"] == "batch_ready"
    assert first["locator"] == {"type": "line_range", "start": 1, "end": 2}
    assert first["result_seed"] == {
        "contract_version": "agiwiki.author-batch-result.v1",
        "plan_id": receipt["plan_id"],
        "batch_id": first["batch_id"],
    }
    assert "第一行事实" not in json.dumps(first, ensure_ascii=False)
    assert (
        controller.next_batch(receipt["plan_id"], workspace=workspace)["batch_id"]
        == first["batch_id"]
    )

    write_json_new(
        workspace / "entries" / "manual-fact.json", _entry(receipt["source_id"])
    )
    recorded = controller.record(
        receipt["plan_id"],
        _result(receipt["plan_id"], first["batch_id"]),
        workspace=workspace,
    )
    assert recorded["entry_count"] == 1
    assert controller.record(
        receipt["plan_id"],
        _result(receipt["plan_id"], first["batch_id"]),
        workspace=workspace,
    )["replayed"]

    second = controller.next_batch(receipt["plan_id"], workspace=workspace)
    controller.record(
        receipt["plan_id"],
        _result(receipt["plan_id"], second["batch_id"], skipped=True),
        workspace=workspace,
    )
    complete = controller.next_batch(receipt["plan_id"], workspace=workspace)
    assert complete["status"] == "complete"
    assert complete["workspace_valid"] is True
    assert (
        controller.status(receipt["plan_id"], workspace=workspace)["completed_batches"]
        == 2
    )
    status = controller.status(receipt["plan_id"], workspace=workspace)
    assert status["progress_basis_points"] == 10_000
    assert status["remaining_batches"] == 0
    assert status["skipped_batches"] == 1
    assert status["recorded_entries_ok"] is True

    portable = validate_workspace(workspace).to_portable_dict()
    serialized = json.dumps(portable, ensure_ascii=False)
    assert str(source) not in serialized
    assert ".agiwiki-author" not in serialized


def test_plan_v2_binds_public_uri_and_v1_remains_resumable(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(
        _source(tmp_path),
        workspace=workspace,
        canonical_uri="https://example.test/manual",
        batch_size=2,
    )
    plan = validate_author_plan(load_json_document(receipt["plan_path"]))
    assert plan["contract_version"] == "agiwiki.author-plan.v2"
    assert plan["policy"]["prompt_set_id"] == "agiwiki-author-memory.v4"
    assert plan["source"]["canonical_uri"] == "https://example.test/manual"
    registered_path = workspace / "sources" / f"{receipt['source_id']}.json"
    registered = load_json_document(registered_path)
    assert registered["canonical_uri"] == "https://example.test/manual"

    legacy = _legacy_plan({**plan, "source": {**plan["source"], "canonical_uri": None}})
    registered["canonical_uri"] = None
    registered_path.write_text(json.dumps(registered), encoding="utf-8")
    legacy_root = workspace / ".agiwiki-author" / "plans" / legacy["plan_id"]
    legacy_root.mkdir(mode=0o700)
    for name in ("claims", "results", "budgets"):
        (legacy_root / name).mkdir(mode=0o700)
    write_json_new(legacy_root / "plan.json", legacy)

    resumed = controller.next_batch(legacy["plan_id"], workspace=workspace)
    assert resumed["status"] == "batch_ready"
    assert resumed["plan_id"] == legacy["plan_id"]


def test_shipped_author_skill_declares_default_prompt_set() -> None:
    skill_path = (
        Path(__file__).parents[1] / "skills" / "agiwiki-author-memory" / "SKILL.md"
    )
    match = re.search(
        r"This workflow implements prompt set `([^`]+)`",
        skill_path.read_text(encoding="utf-8"),
    )

    assert match is not None
    assert match.group(1) == authoring_module.DEFAULT_PROMPT_SET


def test_explicit_v3_prompt_plan_is_preserved_and_resumable(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(
        _source(tmp_path),
        workspace=workspace,
        batch_size=2,
        prompt_set_id="agiwiki-author-memory.v3",
    )

    stored = validate_author_plan(load_json_document(receipt["plan_path"]))
    assert stored["policy"]["prompt_set_id"] == "agiwiki-author-memory.v3"
    first = controller.next_batch(receipt["plan_id"], workspace=workspace)
    resumed = AuthoringController().next_batch(receipt["plan_id"], workspace=workspace)
    assert resumed["status"] == "batch_ready"
    assert resumed["batch_id"] == first["batch_id"]


def test_plan_rejects_credential_bearing_canonical_uri(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(AuthoringError, match="Source metadata"):
        AuthoringController().plan(
            _source(tmp_path),
            workspace=workspace,
            canonical_uri="https://example.test/manual?token=secret",
        )
    assert not list((workspace / "sources").iterdir())

    valid = AuthoringController().plan(
        _source(tmp_path),
        workspace=workspace,
        canonical_uri="https://example.test/manual",
    )
    tampered = load_json_document(valid["plan_path"])
    tampered["source"]["canonical_uri"] = (
        "https://example.test/manual?X-Amz-Credential=secret"
    )
    with pytest.raises(AuthoringContractError, match="portable Source contract"):
        _recompute_plan_identity(tampered)


def test_budget_stop_and_idempotent_extension(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    receipt = AuthoringController().plan(
        _source(tmp_path),
        workspace=workspace,
        batch_size=2,
        tokens_per_unit=200,
        budget_tokens=256,
    )
    controller = AuthoringController()
    stopped = controller.next_batch(receipt["plan_id"], workspace=workspace)
    assert stopped["status"] == "budget_exhausted"

    extension = controller.add_budget(
        receipt["plan_id"],
        workspace=workspace,
        added_tokens=500,
        operation_id="quota-day-0001",
    )
    assert extension["remaining_budget_tokens"] == 756
    assert controller.add_budget(
        receipt["plan_id"],
        workspace=workspace,
        added_tokens=500,
        operation_id="quota-day-0001",
    )["replayed"]
    with pytest.raises(AuthoringError, match="conflicts"):
        controller.add_budget(
            receipt["plan_id"],
            workspace=workspace,
            added_tokens=600,
            operation_id="quota-day-0001",
        )
    assert (
        controller.next_batch(receipt["plan_id"], workspace=workspace)["status"]
        == "batch_ready"
    )


def test_source_change_and_progress_tampering_fail_closed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source = _source(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(source, workspace=workspace, batch_size=2)
    source.write_text("changed\n", encoding="utf-8")
    assert (
        controller.status(receipt["plan_id"], workspace=workspace)["source_ok"] is False
    )
    with pytest.raises(AuthoringError, match="changed"):
        controller.next_batch(receipt["plan_id"], workspace=workspace)

    source.write_text(
        "第一行事实。\n第二行概念。\n第三行步骤。\n第四行验证。\n",
        encoding="utf-8",
    )
    source_record = workspace / "sources" / f"{receipt['source_id']}.json"
    registered = load_json_document(source_record)
    registered["title"] = "tampered registration"
    source_record.write_text(json.dumps(registered), encoding="utf-8")
    assert (
        controller.status(receipt["plan_id"], workspace=workspace)["source_ok"] is False
    )
    with pytest.raises(AuthoringError, match="registration changed"):
        controller.next_batch(receipt["plan_id"], workspace=workspace)

    with pytest.raises(AuthoringError, match="plan_id"):
        controller.status("authorplan_../../escape", workspace=workspace)

    plan_path = Path(receipt["plan_path"])
    plan = load_json_document(plan_path)
    plan["source"]["title"] = "tampered"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(AuthoringContractError, match="digest"):
        controller.status(receipt["plan_id"], workspace=workspace)


def test_recomputed_plan_and_progress_tampering_still_fail_closed(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(_source(tmp_path), workspace=workspace, batch_size=2)
    plan_path = Path(receipt["plan_path"])
    plan = load_json_document(plan_path)
    plan["batches"][0]["estimated_input_tokens"] += 1
    body = {key: value for key, value in plan.items() if key != "plan_digest"}
    plan["plan_digest"] = sha256_digest(body)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(AuthoringContractError, match="estimate"):
        controller.status(receipt["plan_id"], workspace=workspace)

    second = tmp_path / "second"
    second.mkdir()
    workspace_two = _workspace(second)
    source_two = _source(second)
    receipt_two = controller.plan(source_two, workspace=workspace_two, batch_size=2)
    batch = controller.next_batch(receipt_two["plan_id"], workspace=workspace_two)
    controller.record(
        receipt_two["plan_id"],
        _result(receipt_two["plan_id"], batch["batch_id"], skipped=True),
        workspace=workspace_two,
    )
    result_path = (
        Path(receipt_two["plan_path"]).parent / "results" / f"{batch['batch_id']}.json"
    )
    stored = load_json_document(result_path)
    stored["input_tokens"] += 1
    result_path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(AuthoringContractError, match="digest"):
        controller.status(receipt_two["plan_id"], workspace=workspace_two)


def test_result_requires_claim_workspace_entry_and_exact_replay(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(_source(tmp_path), workspace=workspace, batch_size=2)
    plan = validate_author_plan(load_json_document(receipt["plan_path"]))
    first = plan["batches"][0]
    result = _result(receipt["plan_id"], first["batch_id"])
    with pytest.raises(AuthoringError, match="claimed"):
        controller.record(receipt["plan_id"], result, workspace=workspace)

    controller.next_batch(receipt["plan_id"], workspace=workspace)
    with pytest.raises(ValueError, match="at least one Entry"):
        controller.record(receipt["plan_id"], result, workspace=workspace)

    write_json_new(
        workspace / "entries" / "manual-fact.json", _entry(receipt["source_id"])
    )
    controller.record(receipt["plan_id"], result, workspace=workspace)
    conflicting = {**result, "input_tokens": 6}
    with pytest.raises(AuthoringError, match="conflicts"):
        controller.record(receipt["plan_id"], conflicting, workspace=workspace)


def test_result_entry_locator_must_stay_inside_claimed_batch(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(_source(tmp_path), workspace=workspace, batch_size=2)
    first = controller.next_batch(receipt["plan_id"], workspace=workspace)
    outside = _entry(receipt["source_id"])
    outside["source_refs"][0]["locator"]["value"] = "3-4"
    write_json_new(workspace / "entries" / "outside.json", outside)

    with pytest.raises(AuthoringError, match="inside the claimed batch"):
        controller.record(
            receipt["plan_id"],
            _result(receipt["plan_id"], first["batch_id"]),
            workspace=workspace,
        )


def test_recorded_entry_binding_drift_is_visible_and_stops_resume(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(_source(tmp_path), workspace=workspace, batch_size=2)
    first = controller.next_batch(receipt["plan_id"], workspace=workspace)
    entry_path = workspace / "entries" / "manual-fact.json"
    write_json_new(entry_path, _entry(receipt["source_id"]))
    controller.record(
        receipt["plan_id"],
        _result(receipt["plan_id"], first["batch_id"]),
        workspace=workspace,
    )
    second = controller.next_batch(receipt["plan_id"], workspace=workspace)

    changed = load_json_document(entry_path)
    changed["source_refs"][0]["locator"]["value"] = "3-4"
    entry_path.write_text(json.dumps(changed), encoding="utf-8")
    status = controller.status(receipt["plan_id"], workspace=workspace)
    assert status["recorded_entries_ok"] is False
    with pytest.raises(AuthoringError, match="claimed batch"):
        controller.record(
            receipt["plan_id"],
            _result(receipt["plan_id"], second["batch_id"], skipped=True),
            workspace=workspace,
        )
    with pytest.raises(AuthoringError, match="claimed batch"):
        controller.next_batch(receipt["plan_id"], workspace=workspace)


def test_new_result_seals_entry_content_and_same_locator_drift_fails(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(_source(tmp_path), workspace=workspace, batch_size=2)
    first = controller.next_batch(receipt["plan_id"], workspace=workspace)
    entry_path = workspace / "entries" / "manual-fact.json"
    write_json_new(entry_path, _entry(receipt["source_id"]))
    controller.record(
        receipt["plan_id"],
        _result(receipt["plan_id"], first["batch_id"]),
        workspace=workspace,
    )

    result_path = (
        Path(receipt["plan_path"]).parent / "results" / f"{first['batch_id']}.json"
    )
    stored = load_json_document(result_path)
    recorded_entry = validate_workspace(workspace).entry(ENTRY_ID)
    assert stored["contract_version"] == "agiwiki.author-batch-result.v2"
    assert stored["entry_bindings"] == [
        {"entry_id": ENTRY_ID, "entry_digest": sha256_digest(recorded_entry)}
    ]
    status = controller.status(receipt["plan_id"], workspace=workspace)
    assert status["recorded_entries_digest_bound"] is True
    assert status["record_sealed_entry_count"] == 1
    assert status["legacy_unsealed_entry_count"] == 0

    changed = _revised_entry(receipt["source_id"])
    entry_path.write_text(json.dumps(changed), encoding="utf-8")
    assert (
        controller.status(receipt["plan_id"], workspace=workspace)[
            "recorded_entries_ok"
        ]
        is False
    )
    with pytest.raises(AuthoringError, match="effective content binding"):
        controller.next_batch(receipt["plan_id"], workspace=workspace)


def test_entry_status_reports_one_exact_plan_binding_without_content(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(_source(tmp_path), workspace=workspace, batch_size=2)
    first = controller.next_batch(receipt["plan_id"], workspace=workspace)
    write_json_new(
        workspace / "entries" / "manual-fact.json", _entry(receipt["source_id"])
    )
    controller.record(
        receipt["plan_id"],
        _result(receipt["plan_id"], first["batch_id"]),
        workspace=workspace,
    )

    status = controller.entry_status(receipt["plan_id"], ENTRY_ID, workspace=workspace)
    assert status == {
        "contract_version": "agiwiki.author-entry-status.v1",
        "plan_id": receipt["plan_id"],
        "entry_id": ENTRY_ID,
        "batch_id": first["batch_id"],
        "current_entry_digest": status["current_entry_digest"],
        "effective_entry_digest": status["current_entry_digest"],
        "binding_state": "sealed",
        "latest_amendment_id": None,
        "amendment_count": 0,
    }
    serialized = json.dumps(status)
    assert "source_path" not in serialized
    assert "summary" not in serialized
    assert "content" not in serialized
    with pytest.raises(AuthoringError, match="not recorded by this author plan"):
        controller.entry_status(
            receipt["plan_id"], "entry_" + "b" * 32, workspace=workspace
        )


def test_amend_replaces_one_recorded_entry_and_replays_idempotently(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(_source(tmp_path), workspace=workspace, batch_size=2)
    first = controller.next_batch(receipt["plan_id"], workspace=workspace)
    entry_path = workspace / "entries" / "manual-fact.json"
    original = _entry(receipt["source_id"])
    write_json_new(entry_path, original)
    controller.record(
        receipt["plan_id"],
        _result(receipt["plan_id"], first["batch_id"]),
        workspace=workspace,
    )
    old_digest = sha256_digest(validate_workspace(workspace).entry(ENTRY_ID))
    replacement = _revised_entry(receipt["source_id"])

    amended = controller.amend(
        receipt["plan_id"],
        replacement,
        workspace=workspace,
        entry_id=ENTRY_ID,
        expected_old_digest=old_digest,
        operation_id="review-fix-0001",
    )
    assert amended["replayed"] is False
    assert (
        sha256_digest(validate_workspace(workspace).entry(ENTRY_ID))
        == amended["new_entry_digest"]
    )
    stored = load_json_document(
        Path(receipt["plan_path"]).parent
        / "amendments"
        / f"{amended['amendment_id']}.json"
    )
    assert stored["old_digest_basis"] == "recorded_result"
    assert stored["old_entry_digest"] == old_digest
    assert stored["new_entry_digest"] == amended["new_entry_digest"]
    status = controller.status(receipt["plan_id"], workspace=workspace)
    assert status["recorded_entries_ok"] is True
    assert status["amendment_count"] == 1
    entry_status = controller.entry_status(
        receipt["plan_id"], ENTRY_ID, workspace=workspace
    )
    assert entry_status["binding_state"] == "sealed"
    assert entry_status["latest_amendment_id"] == amended["amendment_id"]
    assert entry_status["amendment_count"] == 1
    assert entry_status["current_entry_digest"] == amended["new_entry_digest"]
    assert entry_status["effective_entry_digest"] == amended["new_entry_digest"]

    replay = controller.amend(
        receipt["plan_id"],
        replacement,
        workspace=workspace,
        entry_id=ENTRY_ID,
        expected_old_digest=old_digest,
        operation_id="review-fix-0001",
    )
    assert replay["replayed"] is True
    conflicting = _revised_entry(receipt["source_id"], marker="different")
    with pytest.raises(AuthoringError, match="replay conflicts"):
        controller.amend(
            receipt["plan_id"],
            conflicting,
            workspace=workspace,
            entry_id=ENTRY_ID,
            expected_old_digest=old_digest,
            operation_id="review-fix-0001",
        )
    second_replacement = _revised_entry(receipt["source_id"], marker="second")
    second = controller.amend(
        receipt["plan_id"],
        second_replacement,
        workspace=workspace,
        entry_id=ENTRY_ID,
        expected_old_digest=amended["new_entry_digest"],
        operation_id="review-fix-0006",
    )
    second_stored = load_json_document(
        Path(receipt["plan_path"]).parent
        / "amendments"
        / f"{second['amendment_id']}.json"
    )
    assert second_stored["sequence"] == 2
    assert second_stored["previous_amendment_id"] == amended["amendment_id"]
    assert second_stored["old_digest_basis"] == "prior_amendment"
    assert (
        controller.status(receipt["plan_id"], workspace=workspace)["amendment_count"]
        == 2
    )
    second_stored["previous_amendment_id"] = None
    amendment_body = {
        key: value for key, value in second_stored.items() if key != "amendment_digest"
    }
    second_stored["amendment_digest"] = sha256_digest(amendment_body)
    amendment_path = (
        Path(receipt["plan_path"]).parent
        / "amendments"
        / f"{second['amendment_id']}.json"
    )
    amendment_path.write_text(json.dumps(second_stored), encoding="utf-8")
    with pytest.raises(AuthoringError, match="chain"):
        controller.status(receipt["plan_id"], workspace=workspace)


def test_amend_receipt_first_failure_converges_on_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(_source(tmp_path), workspace=workspace, batch_size=2)
    first = controller.next_batch(receipt["plan_id"], workspace=workspace)
    write_json_new(
        workspace / "entries" / "manual-fact.json", _entry(receipt["source_id"])
    )
    controller.record(
        receipt["plan_id"],
        _result(receipt["plan_id"], first["batch_id"]),
        workspace=workspace,
    )
    old_digest = sha256_digest(validate_workspace(workspace).entry(ENTRY_ID))
    replacement = _revised_entry(receipt["source_id"])
    original_replace = authoring_module._replace_entry_atomically

    def interrupted(*args, **kwargs) -> None:
        raise OSError("simulated interruption after receipt")

    monkeypatch.setattr(authoring_module, "_replace_entry_atomically", interrupted)
    with pytest.raises(OSError, match="simulated interruption"):
        controller.amend(
            receipt["plan_id"],
            replacement,
            workspace=workspace,
            entry_id=ENTRY_ID,
            expected_old_digest=old_digest,
            operation_id="review-fix-0002",
        )
    assert (
        controller.status(receipt["plan_id"], workspace=workspace)[
            "recorded_entries_ok"
        ]
        is False
    )

    monkeypatch.setattr(authoring_module, "_replace_entry_atomically", original_replace)
    replay = controller.amend(
        receipt["plan_id"],
        replacement,
        workspace=workspace,
        entry_id=ENTRY_ID,
        expected_old_digest=old_digest,
        operation_id="review-fix-0002",
    )
    assert replay["replayed"] is True
    assert controller.status(receipt["plan_id"], workspace=workspace)[
        "recorded_entries_ok"
    ]


def test_amend_rejects_changed_identity_cross_batch_and_unrecorded_entry(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(_source(tmp_path), workspace=workspace, batch_size=2)
    first = controller.next_batch(receipt["plan_id"], workspace=workspace)
    write_json_new(
        workspace / "entries" / "manual-fact.json", _entry(receipt["source_id"])
    )
    controller.record(
        receipt["plan_id"],
        _result(receipt["plan_id"], first["batch_id"]),
        workspace=workspace,
    )
    old_digest = sha256_digest(validate_workspace(workspace).entry(ENTRY_ID))

    with pytest.raises(AuthoringError, match="effective binding"):
        controller.amend(
            receipt["plan_id"],
            _revised_entry(receipt["source_id"]),
            workspace=workspace,
            entry_id=ENTRY_ID,
            expected_old_digest="sha256:" + "f" * 64,
            operation_id="review-fix-0007",
        )

    changed_id = _revised_entry(receipt["source_id"])
    changed_id["entry_id"] = "entry_" + "b" * 32
    with pytest.raises(AuthoringError, match="must match --entry-id"):
        controller.amend(
            receipt["plan_id"],
            changed_id,
            workspace=workspace,
            entry_id=ENTRY_ID,
            expected_old_digest=old_digest,
            operation_id="review-fix-0003",
        )
    with pytest.raises(AuthoringError, match="completed batch"):
        controller.amend(
            receipt["plan_id"],
            changed_id,
            workspace=workspace,
            entry_id=changed_id["entry_id"],
            expected_old_digest=sha256_digest(changed_id),
            operation_id="review-fix-0005",
        )

    outside = _revised_entry(receipt["source_id"])
    outside["source_refs"][0]["locator"]["value"] = "3-4"
    with pytest.raises(AuthoringError, match="original batch"):
        controller.amend(
            receipt["plan_id"],
            outside,
            workspace=workspace,
            entry_id=ENTRY_ID,
            expected_old_digest=old_digest,
            operation_id="review-fix-0004",
        )
    assert not list((Path(receipt["plan_path"]).parent / "amendments").iterdir())


def test_legacy_v1_result_is_explicitly_unsealed_then_bridged(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(_source(tmp_path), workspace=workspace, batch_size=2)
    first = controller.next_batch(receipt["plan_id"], workspace=workspace)
    write_json_new(
        workspace / "entries" / "manual-fact.json", _entry(receipt["source_id"])
    )
    request = _result(receipt["plan_id"], first["batch_id"])
    controller.record(receipt["plan_id"], request, workspace=workspace)
    result_path = (
        Path(receipt["plan_path"]).parent / "results" / f"{first['batch_id']}.json"
    )
    legacy = normalize_author_batch_result(request)
    result_path.write_text(json.dumps(legacy), encoding="utf-8")

    status = controller.status(receipt["plan_id"], workspace=workspace)
    assert status["recorded_entries_ok"] is True
    assert status["recorded_entries_digest_bound"] is False
    assert status["legacy_unsealed_entry_count"] == 1
    entry_status = controller.entry_status(
        receipt["plan_id"], ENTRY_ID, workspace=workspace
    )
    assert entry_status["binding_state"] == "legacy_unsealed"
    assert entry_status["effective_entry_digest"] is None
    assert entry_status["current_entry_digest"] == sha256_digest(
        validate_workspace(workspace).entry(ENTRY_ID)
    )
    old_digest = sha256_digest(validate_workspace(workspace).entry(ENTRY_ID))
    amended = controller.amend(
        receipt["plan_id"],
        _revised_entry(receipt["source_id"]),
        workspace=workspace,
        entry_id=ENTRY_ID,
        expected_old_digest=old_digest,
        operation_id="legacy-review-0001",
    )
    stored = load_json_document(
        Path(receipt["plan_path"]).parent
        / "amendments"
        / f"{amended['amendment_id']}.json"
    )
    assert stored["old_digest_basis"] == "operator_asserted_legacy"
    status = controller.status(receipt["plan_id"], workspace=workspace)
    assert status["recorded_entries_digest_bound"] is True
    assert status["legacy_bridged_entry_count"] == 1
    assert status["legacy_unsealed_entry_count"] == 0
    entry_status = controller.entry_status(
        receipt["plan_id"], ENTRY_ID, workspace=workspace
    )
    assert entry_status["binding_state"] == "legacy_bridged"
    assert entry_status["latest_amendment_id"] == amended["amendment_id"]
    assert (
        entry_status["current_entry_digest"] == entry_status["effective_entry_digest"]
    )


def test_closed_result_and_plan_semantics() -> None:
    with pytest.raises(AuthoringContractError, match="input_tokens"):
        normalize_author_batch_result(
            {
                "contract_version": "agiwiki.author-batch-result.v1",
                "plan_id": "authorplan_" + "a" * 32,
                "batch_id": "authorbatch_" + "b" * 32,
                "outcome": "skipped",
                "measurement_source": "unavailable",
                "input_tokens": 1,
                "output_tokens": 0,
                "entry_ids": [],
            }
        )


def test_cli_plan_status_and_help_are_available(tmp_path: Path, capsys) -> None:
    workspace = _workspace(tmp_path)
    source = _source(tmp_path)
    assert (
        main(
            [
                "author",
                "plan",
                str(source),
                "--workspace",
                str(workspace),
                "--batch-size",
                "2",
            ]
        )
        == 0
    )
    plan_receipt = json.loads(capsys.readouterr().out)
    assert (
        main(
            [
                "author",
                "status",
                plan_receipt["plan_id"],
                "--workspace",
                str(workspace),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["batch_count"] == 2


def test_cli_amend_is_available(tmp_path: Path, capsys) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(_source(tmp_path), workspace=workspace, batch_size=2)
    first = controller.next_batch(receipt["plan_id"], workspace=workspace)
    write_json_new(
        workspace / "entries" / "manual-fact.json", _entry(receipt["source_id"])
    )
    controller.record(
        receipt["plan_id"],
        _result(receipt["plan_id"], first["batch_id"]),
        workspace=workspace,
    )
    old_digest = sha256_digest(validate_workspace(workspace).entry(ENTRY_ID))
    staged = tmp_path / "revised-entry.json"
    write_json_new(staged, _revised_entry(receipt["source_id"]))

    assert (
        main(
            [
                "author",
                "entry-status",
                receipt["plan_id"],
                "--workspace",
                str(workspace),
                "--entry-id",
                ENTRY_ID,
            ]
        )
        == 0
    )
    entry_status = json.loads(capsys.readouterr().out)
    assert entry_status["binding_state"] == "sealed"
    assert entry_status["current_entry_digest"] == old_digest
    assert set(entry_status) == {
        "contract_version",
        "plan_id",
        "entry_id",
        "batch_id",
        "current_entry_digest",
        "effective_entry_digest",
        "binding_state",
        "latest_amendment_id",
        "amendment_count",
    }

    assert (
        main(
            [
                "author",
                "amend",
                receipt["plan_id"],
                "--workspace",
                str(workspace),
                "--entry-id",
                ENTRY_ID,
                "--input",
                str(staged),
                "--expect-old-digest",
                old_digest,
                "--operation-id",
                "cli-review-0001",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["entry_id"] == ENTRY_ID


def test_build_preflight_reports_manual_complete_and_drifted_workspaces(
    tmp_path: Path,
) -> None:
    manual_parent = tmp_path / "manual"
    manual_parent.mkdir()
    manual = _workspace(manual_parent)
    controller = AuthoringController()
    assert controller.build_preflight(workspace=manual) == {
        "contract_version": "agiwiki.authoring-build-preflight.v1",
        "ready": True,
        "plan_count": 0,
        "plans": [],
        "blockers": [],
        "legacy_unsealed_entry_count": 0,
        "semantic_review": "NOT_CHECKED",
    }

    root = tmp_path / "planned"
    root.mkdir()
    workspace = _workspace(root)
    source = _source(root)
    receipt = controller.plan(source, workspace=workspace, batch_size=2)
    first = controller.next_batch(receipt["plan_id"], workspace=workspace)
    write_json_new(
        workspace / "entries" / "manual-fact.json", _entry(receipt["source_id"])
    )
    controller.record(
        receipt["plan_id"],
        _result(receipt["plan_id"], first["batch_id"]),
        workspace=workspace,
    )
    incomplete = controller.build_preflight(workspace=workspace)
    assert incomplete["ready"] is False
    assert incomplete["blockers"] == [
        {"plan_id": receipt["plan_id"], "code": "INCOMPLETE_BATCHES"}
    ]

    second = controller.next_batch(receipt["plan_id"], workspace=workspace)
    controller.record(
        receipt["plan_id"],
        _result(receipt["plan_id"], second["batch_id"], skipped=True),
        workspace=workspace,
    )
    complete = controller.build_preflight(workspace=workspace)
    assert complete["ready"] is True
    assert complete["plans"][0]["ready"] is True
    assert complete["semantic_review"] == "NOT_CHECKED"

    source.write_text("source changed after the plan\n", encoding="utf-8")
    drifted = controller.build_preflight(workspace=workspace)
    assert drifted["ready"] is False
    assert drifted["blockers"] == [
        {"plan_id": receipt["plan_id"], "code": "SOURCE_CHANGED"}
    ]


def test_cli_pack_build_blocks_incomplete_authoring_unless_explicitly_overridden(
    tmp_path: Path, capsys
) -> None:
    workspace = _workspace(tmp_path)
    controller = AuthoringController()
    receipt = controller.plan(_source(tmp_path), workspace=workspace, batch_size=2)
    write_json_new(
        workspace / "entries" / "manual-fact.json", _entry(receipt["source_id"])
    )
    destination = tmp_path / "blocked.memory-pack"

    assert main(["pack", "build", str(workspace), str(destination)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "ValueError"
    assert "authoring preflight blocked" in error["message"]
    assert not destination.exists()

    assert (
        main(
            [
                "pack",
                "build",
                str(workspace),
                str(destination),
                "--allow-incomplete-authoring",
            ]
        )
        == 0
    )
    built = json.loads(capsys.readouterr().out)
    assert built["authoring_preflight"]["ready"] is False
    assert built["incomplete_authoring_override"] is True
    assert destination.is_dir()

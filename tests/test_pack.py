from __future__ import annotations

import json
from pathlib import Path

import pytest

from agiwiki.codec import JSONDocumentError, canonical_json, load_json_document, sha256_digest
from agiwiki.pack import PackError, build_pack, get_entry, verify_pack


WORKSPACE_ID = "ws_" + "1" * 32
SOURCE_ID = "src_" + "2" * 32
ENTRY_ID = "entry_" + "3" * 32


def workspace(*, version: str = "1.0.0") -> dict:
    return {
        "contract_version": "agiwiki.workspace.v1",
        "workspace_id": WORKSPACE_ID,
        "slug": "zemax-notes",
        "version": version,
        "title": "Zemax Notes",
        "description": "Personal verified notes",
        "default_locale": "zh-CN",
    }


def source(*, source_id: str = SOURCE_ID, title: str = "Zemax Manual") -> dict:
    return {
        "contract_version": "agiwiki.source.v1",
        "source_id": source_id,
        "kind": "manual",
        "title": title,
        "edition": "2026.1",
        "content_digest": "sha256:" + "4" * 64,
        "canonical_uri": "https://example.test/zemax/manual",
        "language": "en",
    }


def entry(
    *,
    title: str = "设置非序列光源",
    entry_id: str = ENTRY_ID,
    source_id: str = SOURCE_ID,
) -> dict:
    return {
        "contract_version": "agiwiki.entry.v1",
        "entry_id": entry_id,
        "kind": "procedure",
        "title": title,
        "summary": "创建光源并验证设置。",
        "content": {
            "goal": "建立一个非序列光源。",
            "prerequisites": ["打开非序列模式。"],
            "steps": [
                {
                    "step_id": "step_create",
                    "action": "插入 Source Rectangle。",
                    "expected_result": "对象列表出现光源。",
                    "verification": "检查对象类型和功率。",
                    "failure_guidance": ["确认当前系统模式。"],
                    "warnings": ["不要覆盖已有对象。"],
                }
            ],
            "verification": ["运行追迹并检查探测器。"],
            "failure_guidance": ["恢复备份后重试。"],
            "warnings": ["修改前保存工程。"],
        },
        "keywords": ["zemax", "光源"],
        "applies_to": ["zemax:2026.1"],
        "relations": [],
        "source_refs": [
            {
                "source_id": source_id,
                "locator": {"type": "section", "value": "4.2"},
                "support_level": "direct",
            }
        ],
    }


def test_codec_is_deterministic_and_loader_rejects_unsafe_json(tmp_path: Path) -> None:
    left = {"z": "中文", "a": [1, True, None]}
    right = {"a": [1, True, None], "z": "中文"}
    assert canonical_json(left) == canonical_json(right)
    assert sha256_digest(left) == sha256_digest(right)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a":1,"a":2}', encoding="utf-8")
    with pytest.raises(JSONDocumentError, match="duplicate"):
        load_json_document(duplicate)

    non_finite = tmp_path / "nan.json"
    non_finite.write_text('{"a":NaN}', encoding="utf-8")
    with pytest.raises(JSONDocumentError, match="non-finite"):
        load_json_document(non_finite)

    linked = tmp_path / "linked.json"
    linked.symlink_to(duplicate)
    with pytest.raises(JSONDocumentError, match="symlink"):
        load_json_document(linked)

    real_directory = tmp_path / "real"
    real_directory.mkdir()
    nested = real_directory / "document.json"
    nested.write_text("{}", encoding="utf-8")
    linked_directory = tmp_path / "linked-directory"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(JSONDocumentError, match="symlink"):
        load_json_document(linked_directory / "document.json")


def test_pack_is_portable_deterministic_json_closed_set(tmp_path: Path) -> None:
    first = tmp_path / "first-pack"
    second = tmp_path / "second-pack"
    other_source_id = "src_" + "5" * 32
    other_entry_id = "entry_" + "6" * 32
    sources = [source(), source(source_id=other_source_id, title="Release Notes")]
    entries = [
        entry(),
        entry(
            title="检查新版设置",
            entry_id=other_entry_id,
            source_id=other_source_id,
        ),
    ]
    first_receipt = build_pack(workspace(), sources, entries, first)
    second_receipt = build_pack(
        workspace(), list(reversed(sources)), list(reversed(entries)), second
    )

    assert first_receipt["pack_id"] == second_receipt["pack_id"]
    assert first_receipt["manifest_digest"] == second_receipt["manifest_digest"]
    assert first_receipt["replayed"] is False
    manifest = verify_pack(first)
    assert manifest["workspace_id"] == WORKSPACE_ID
    assert manifest["entry_count"] == 2
    assert manifest["source_count"] == 2
    assert {item.relative_to(first).as_posix() for item in first.rglob("*")} == {
        "pack.json",
        "sources.json",
        "entries",
        *(item["path"] for item in manifest["entry_refs"]),
    }
    assert not list(first.rglob("*.db"))
    assert (first / "pack.json").read_bytes() == (second / "pack.json").read_bytes()

    envelope = get_entry(first, ENTRY_ID)
    assert envelope["entry"]["title"] == "设置非序列光源"
    assert envelope["entry_version_id"].startswith("entryv_")
    assert envelope["entry_digest"] == sha256_digest(envelope["entry"])


def test_pack_exact_replay_and_no_clobber_conflict(tmp_path: Path) -> None:
    target = tmp_path / "pack"
    original = build_pack(workspace(), [source()], [entry()], target)
    replay = build_pack(workspace(), [source()], [entry()], target)
    assert replay["pack_id"] == original["pack_id"]
    assert replay["replayed"] is True

    with pytest.raises(FileExistsError):
        build_pack(workspace(version="2.0.0"), [source()], [entry()], target)
    assert verify_pack(target)["pack_id"] == original["pack_id"]


@pytest.mark.parametrize("attack", ["tamper", "extra", "symlink"])
def test_verify_rejects_tamper_extra_files_and_symlinks(
    tmp_path: Path, attack: str
) -> None:
    target = tmp_path / "pack"
    build_pack(workspace(), [source()], [entry()], target)
    manifest = verify_pack(target)
    entry_path = target / manifest["entry_refs"][0]["path"]
    if attack == "tamper":
        document = json.loads(entry_path.read_text(encoding="utf-8"))
        document["entry"]["title"] = "被修改"
        entry_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    elif attack == "extra":
        (target / "extra.json").write_text("{}", encoding="utf-8")
    else:
        (target / "entries" / "alias.json").symlink_to(entry_path)
    with pytest.raises((PackError, JSONDocumentError)):
        verify_pack(target)


def test_invalid_build_leaves_no_pack_and_missing_source_is_rejected(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pack"
    broken = entry()
    broken["source_refs"][0]["source_id"] = "src_" + "9" * 32
    with pytest.raises(PackError, match="absent"):
        build_pack(workspace(), [source()], [broken], target)
    assert not target.exists()
    assert not list(tmp_path.glob(".pack.*"))


def test_manifest_duplicate_key_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "pack"
    build_pack(workspace(), [source()], [entry()], target)
    manifest_path = target / "pack.json"
    text = manifest_path.read_text(encoding="utf-8").rstrip()
    manifest_path.write_text(text[:-1] + ',"pack_id":"pack_' + "0" * 32 + '"}', encoding="utf-8")
    with pytest.raises(PackError, match="manifest"):
        verify_pack(target)

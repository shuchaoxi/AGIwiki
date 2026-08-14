from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import pytest

import agiwiki.index as index_module
from agiwiki.index import (
    IndexError,
    TOKENIZER_FALLBACK,
    build_index,
    ensure_index,
    find_memory,
    rebuild_index,
    verify_index,
)
from agiwiki.pack import PackError, build_pack, verify_pack
from test_pack import ENTRY_ID, entry, source, workspace


def test_index_is_external_rebuildable_and_searches_pack(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    cache = tmp_path / "cache" / "search.sqlite"
    build_pack(workspace(), [source()], [entry()], pack)

    receipt = build_index(pack, cache)
    assert receipt["replayed"] is False
    assert cache.is_file()
    assert cache.parent != pack
    assert not list(pack.rglob("*.sqlite"))
    assert verify_index(pack, cache)["pack_id"] == verify_pack(pack)["pack_id"]

    result = find_memory(pack, cache, "非序列光源")
    assert result["count"] == 1
    assert result["results"][0]["entry_id"] == ENTRY_ID
    assert "query" not in result

    short = find_memory(pack, cache, "光")
    assert short["count"] == 1

    natural = find_memory(pack, cache, "请问如何创建并检查非序列矩形光源的结果")
    assert natural["count"] == 1
    assert natural["results"][0]["entry_id"] == ENTRY_ID

    english = find_memory(pack, cache, "how to verify a zemax source safely")
    assert english["count"] == 1
    assert english["results"][0]["entry_id"] == ENTRY_ID


def test_natural_language_fallback_ranks_the_specific_entry_first(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    cache = tmp_path / "search.sqlite"
    report = entry(
        entry_id="entry_" + "7" * 32,
        title="导出 Zemax 分析报告",
    )
    report["summary"] = "把已经完成的分析结果导出为独立报告，并重新打开文件检查内容完整性。"
    report["content"]["goal"] = "将当前分析结果写入一个新报告文件，同时保留原始工程和已有输出。"
    report["content"]["steps"][0]["action"] = "选择报告导出功能，并指定一个尚不存在的新文件作为目标。"
    report["content"]["steps"][0]["expected_result"] = "目标位置出现新的报告文件，原始工程文件保持不变。"
    report["content"]["steps"][0]["verification"] = "重新打开导出的报告，并核对标题、分析参数和结果段落。"
    report["keywords"] = ["zemax", "分析报告", "导出"]
    build_pack(workspace(), [source()], [entry(), report], pack)
    build_index(pack, cache)

    result = find_memory(pack, cache, "请问如何创建并检查非序列矩形光源的结果")

    assert result["count"] == 2
    assert result["results"][0]["entry_id"] == ENTRY_ID


def test_index_exact_replay_and_conflicting_pack_does_not_clobber(
    tmp_path: Path,
) -> None:
    first = tmp_path / "pack-one"
    second = tmp_path / "pack-two"
    cache = tmp_path / "search.sqlite"
    build_pack(workspace(), [source()], [entry()], first)
    build_pack(workspace(version="2.0.0"), [source()], [entry(title="新版光源")], second)
    original = build_index(first, cache)
    assert build_index(first, cache)["replayed"] is True

    with pytest.raises(IndexError, match="does not match"):
        build_index(second, cache)
    assert verify_index(first, cache)["pack_id"] == original["pack_id"]

    rebuilt = rebuild_index(second, cache)
    assert rebuilt["pack_id"] == verify_pack(second)["pack_id"]
    assert find_memory(second, cache, "新版光源")["count"] == 1


def test_ensure_index_replaces_tampered_or_stale_cache(tmp_path: Path) -> None:
    first = tmp_path / "pack-one"
    second = tmp_path / "pack-two"
    cache = tmp_path / "search.sqlite"
    build_pack(workspace(), [source()], [entry()], first)
    build_pack(workspace(version="2"), [source()], [entry(title="更新后的流程")], second)
    build_index(first, cache)

    metadata = ensure_index(second, cache)
    assert metadata["pack_id"] == verify_pack(second)["pack_id"]

    connection = sqlite3.connect(cache)
    try:
        connection.execute("DELETE FROM entry_fts")
        connection.commit()
    finally:
        connection.close()
    restored = ensure_index(second, cache)
    assert restored["entry_count"] == 1
    assert find_memory(second, cache, "更新后的流程")["count"] == 1


def test_trigram_unavailable_falls_back_to_unicode61(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = tmp_path / "pack"
    cache = tmp_path / "search.sqlite"
    build_pack(workspace(), [source()], [entry()], pack)
    original = index_module._populate_index

    def unavailable(target, manifest, entries, *, tokenizer):
        if tokenizer == index_module.TOKENIZER_TRIGRAM:
            raise sqlite3.OperationalError("no such tokenizer: trigram")
        return original(target, manifest, entries, tokenizer=tokenizer)

    monkeypatch.setattr(index_module, "_populate_index", unavailable)
    receipt = build_index(pack, cache)
    assert receipt["tokenizer"] == TOKENIZER_FALLBACK
    assert find_memory(pack, cache, "zemax")["count"] == 1


def test_pack_tamper_blocks_index_reads(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    cache = tmp_path / "search.sqlite"
    build_pack(workspace(), [source()], [entry()], pack)
    build_index(pack, cache)
    manifest = verify_pack(pack)
    target = pack / manifest["entry_refs"][0]["path"]
    target.write_text("{}", encoding="utf-8")
    with pytest.raises((PackError, ValueError)):
        find_memory(pack, cache, "zemax")


def test_index_symlink_is_never_followed(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    real = tmp_path / "real.sqlite"
    linked = tmp_path / "linked.sqlite"
    build_pack(workspace(), [source()], [entry()], pack)
    build_index(pack, real)
    linked.symlink_to(real)
    with pytest.raises(IndexError, match="symlink"):
        verify_index(pack, linked)


def test_concurrent_first_read_reuses_the_verified_winner(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    cache = tmp_path / "search.sqlite"
    build_pack(workspace(), [source()], [entry()], pack)

    def read_once(_: int) -> str:
        return ensure_index(pack, cache)["pack_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read_once, range(8)))
    assert len(set(results)) == 1
    assert results[0] == verify_pack(pack)["pack_id"]

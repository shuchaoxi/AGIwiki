from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "integrations" / "deepseek-harness"


def _load_closed_json(path: Path) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def test_deepseek_harness_overlay_is_read_only_and_portable() -> None:
    overlay = (INTEGRATION / "agiwiki.cordis.yml").read_text(encoding="utf-8")

    required = (
        "name: '@deepseek-ai/dsh-mcp-client'",
        "serverName: agiwiki",
        "transport: stdio",
        "command: agiwiki-mcp",
        "args: []",
        "failOnStartupError: true",
    )
    assert all(value in overlay for value in required)
    assert overlay.count("@deepseek-ai/dsh-mcp-client") == 1
    assert not any(
        forbidden in overlay
        for forbidden in ("streamable-http", "headers:", "Authorization", "token:")
    )
    assert "/home/" not in overlay
    assert "C:\\" not in overlay


def test_deepseek_harness_compatibility_receipt_is_honest_and_pinned() -> None:
    receipt = _load_closed_json(INTEGRATION / "COMPATIBILITY.json")

    assert set(receipt) == {
        "contract_version",
        "integration_id",
        "status",
        "verified_at",
        "upstream",
        "agiwiki",
        "verification",
        "limitations",
    }
    assert receipt["status"] == "experimental"
    upstream = receipt["upstream"]
    assert isinstance(upstream, dict)
    assert re.fullmatch(r"[0-9a-f]{40}", str(upstream["commit"]))
    assert upstream["bridge_package"] == "@deepseek-ai/dsh-mcp-client"
    verification = receipt["verification"]
    assert isinstance(verification, dict)
    assert verification["deepseek_harness_runtime_round_trip"] == "NOT_RUN"
    agiwiki = receipt["agiwiki"]
    assert isinstance(agiwiki, dict)
    assert agiwiki["write_tools_exposed"] is False
    assert agiwiki["tool_names"] == [
        "mcp__agiwiki__find_memory",
        "mcp__agiwiki__get_memory",
    ]
    assert agiwiki["resource_names_not_bridged"] == ["agiwiki://catalog"]


def test_deepseek_harness_public_docs_are_english_and_state_limits() -> None:
    documentation = (INTEGRATION / "README.md").read_text(encoding="utf-8")
    combined = documentation + (INTEGRATION / "COMPATIBILITY.json").read_text(
        encoding="utf-8"
    )

    assert re.search(r"[\u3400-\u9fff]", combined) is None
    assert "developer preview" in documentation
    assert "MCP resource `agiwiki://catalog` is not available" in documentation
    assert "does not imply\nendorsement" in documentation
    assert "mcp__agiwiki__find_memory" in documentation
    assert "mcp__agiwiki__get_memory" in documentation

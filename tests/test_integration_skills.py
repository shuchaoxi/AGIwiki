from __future__ import annotations

import json
from pathlib import Path

import pytest

from agiwiki.cli import main
from agiwiki.integration import IntegrationError, locate_skill


@pytest.mark.parametrize(
    ("capability", "skill_name", "required"),
    [
        ("read", "agiwiki-memory", {"SKILL.md", "agents/openai.yaml"}),
        (
            "author",
            "agiwiki-author-memory",
            {
                "SKILL.md",
                "agents/openai.yaml",
                "references/authoring-contract.md",
            },
        ),
        (
            "review",
            "agiwiki-critical-review",
            {
                "SKILL.md",
                "agents/openai.yaml",
                "references/review-contract.md",
            },
        ),
    ],
)
def test_locate_complete_skill_is_read_only(
    capability: str,
    skill_name: str,
    required: set[str],
) -> None:
    result = locate_skill(capability)

    assert result["contract_version"] == "agiwiki.skill-path.v1"
    assert result["skill_name"] == skill_name
    assert result["status"] == "AVAILABLE"
    assert result["available"] is True
    assert result["missing_files"] == []
    assert set(result["required_files"]) == required
    assert result["side_effects"] == {
        "files_written": False,
        "agent_configuration_modified": False,
    }
    directory = Path(result["path"])
    assert Path(result["entrypoint"]) == directory / "SKILL.md"
    assert all((directory / relative).is_file() for relative in required)


@pytest.mark.parametrize(
    ("capability", "reference"),
    [
        ("author", "authoring-contract.md"),
        ("review", "review-contract.md"),
    ],
)
def test_skill_path_cli_returns_machine_readable_location(
    capsys, capability: str, reference: str
) -> None:
    assert main(["integration", "skill-path", "--capability", capability]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["capability"] == capability
    assert result["available"] is True
    assert Path(result["entrypoint"]).is_file()
    assert (Path(result["path"]) / "references" / reference).is_file()


def test_locate_skill_rejects_unknown_capability() -> None:
    with pytest.raises(IntegrationError, match="read, author, or review"):
        locate_skill("write")

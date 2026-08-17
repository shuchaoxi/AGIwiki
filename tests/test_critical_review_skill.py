from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "agiwiki-critical-review"


def test_critical_review_skill_freezes_evidence_and_falsification_contract() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    contract = (SKILL_ROOT / "references" / "review-contract.md").read_text(
        encoding="utf-8"
    )

    assert "name: agiwiki-critical-review" in skill
    assert "without flattering the user" in skill
    assert "Cheapest falsification test" in contract
    assert "exact_duplicate" in contract
    assert "near_duplicate" in contract
    assert "NOT_CHECKED" in skill
    assert "This Skill has no write authority" in skill
    for verdict in (
        "SUPPORTED",
        "PLAUSIBLE",
        "SPECULATIVE",
        "BLOCKED",
        "NOT_ENOUGH_INFORMATION",
    ):
        assert verdict in contract


def test_critical_review_skill_is_public_english_and_bounded() -> None:
    files = (
        SKILL_ROOT / "SKILL.md",
        SKILL_ROOT / "agents" / "openai.yaml",
        SKILL_ROOT / "references" / "review-contract.md",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)

    assert not re.search(r"[\u3400-\u9fff]", combined)
    assert len(files[0].read_text(encoding="utf-8").splitlines()) < 120

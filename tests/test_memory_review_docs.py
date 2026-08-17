from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_periodic_memory_review_is_proposal_only_until_automation_gates_pass() -> None:
    review = (ROOT / "docs" / "memory-review-loop.md").read_text(encoding="utf-8")
    normalized = " ".join(review.split())

    assert "daily" in review
    assert "weekly" in review
    assert "read-only proposal" in normalized
    assert "human confirmation is mandatory" in review
    assert "request-bound operation idempotency" in review
    assert "anti-resurrection" in review
    assert "Critical Review Skill" in review
    assert "Do not force it onto emotional support" in normalized
    assert "If model-assisted weekly review does not improve" in normalized


def test_review_policy_is_not_represented_as_factual_memory() -> None:
    strategy = (ROOT / "docs" / "memory-strategy.md").read_text(encoding="utf-8")
    security = (ROOT / "docs" / "security-model.md").read_text(encoding="utf-8")

    assert "versioned opt-in Skill" in strategy
    assert "not in every factual or profile record" in strategy
    assert "opt-in Skill policy, not a durable user fact" in " ".join(security.split())

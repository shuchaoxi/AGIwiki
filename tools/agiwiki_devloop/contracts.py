"""Typed, JSON-like contracts for the bounded development graph."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


Decision = Literal[
    "IN_PROGRESS",
    "READY_FOR_HUMAN",
    "HUMAN_REVIEW",
    "BLOCKED",
]


class VerificationReceipt(TypedDict):
    command_digest: str
    argv_count: int
    returncode: int | None
    timed_out: bool
    truncated: bool
    output_digest: str
    output_bytes: int


class Finding(TypedDict, total=False):
    severity: Literal["P0", "P1", "P2", "P3"]
    message: str
    file: str
    line: int


class HistoryEntry(TypedDict, total=False):
    sequence: int
    round: int
    stage: str
    outcome: str
    reasons: list[str]
    agent_calls: int
    diff_digest: str


class DevLoopState(TypedDict):
    run_id: str
    objective: str
    repo_root: str
    base_commit: str
    allowed_paths: list[str]
    expected_dirty_paths: list[str]
    acceptance_commands: list[list[str]]
    command_timeout_seconds: float

    baseline_snapshot: dict[str, str]
    baseline_diff_digest: str
    plan: dict[str, Any]

    round: int
    max_rounds: int
    agent_calls: int
    max_agent_calls: int

    changed_files: list[str]
    previous_diff_digest: str | None
    diff_digest: str | None
    verification_receipts: list[VerificationReceipt]
    review_findings: list[Finding]
    review_decision: str | None

    decision: Decision
    reasons: list[str]
    history: list[HistoryEntry]
    next_node: Literal[
        "planner",
        "implementer",
        "verify",
        "reviewer",
        "decide",
        "end",
    ]


__all__ = [
    "Decision",
    "DevLoopState",
    "Finding",
    "HistoryEntry",
    "VerificationReceipt",
]

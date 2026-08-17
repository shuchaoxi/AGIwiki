"""A bounded LangGraph loop for multi-Agent AGIWiki development.

The graph coordinates injected callbacks.  It does not invoke an LLM, create a
worktree, commit, push, merge, publish, or deploy.
"""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .adapters import AgentSet
from .contracts import DevLoopState, Finding, VerificationReceipt
from .gates import (
    GateError,
    changed_since_snapshot,
    dirty_paths,
    git_root,
    head_commit,
    normalize_allow_pattern,
    normalize_repo_path,
    outside_allowlist,
    run_acceptance_commands,
    snapshot,
    worktree_diff_digest,
)


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PLAN_STATUSES = frozenset({"READY", "NEEDS_INPUT", "BLOCKED"})
_IMPLEMENTATION_STATUSES = frozenset({"IMPLEMENTED", "BLOCKED"})
_REVIEW_DECISIONS = frozenset({"APPROVE", "REVISE", "HUMAN_REVIEW"})
_SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})


class DevLoopConfigurationError(ValueError):
    """The loop could not start with the supplied deterministic limits."""


class DevLoop:
    """Compile and run a fail-closed development loop with three Agent roles."""

    def __init__(self, agents: AgentSet) -> None:
        self._agents = agents
        self._compiled = self._compile()

    def _compile(self) -> Any:
        try:
            from langgraph.graph import END, START, StateGraph
        except ModuleNotFoundError as exc:  # pragma: no cover - environment contract
            raise RuntimeError(
                "AGIWiki devloop requires the workspace LangGraph runner"
            ) from exc

        graph = StateGraph(DevLoopState)
        graph.add_node("preflight", self._preflight)
        graph.add_node("planner", self._planner)
        graph.add_node("implementer", self._implementer)
        graph.add_node("verify", self._verify)
        graph.add_node("reviewer", self._reviewer)
        graph.add_node("decide", self._decide)
        graph.add_edge(START, "preflight")
        graph.add_conditional_edges(
            "preflight", self._edge, {"planner": "planner", "end": END}
        )
        graph.add_conditional_edges(
            "planner", self._edge, {"implementer": "implementer", "end": END}
        )
        graph.add_conditional_edges(
            "implementer", self._edge, {"verify": "verify", "end": END}
        )
        graph.add_conditional_edges(
            "verify", self._edge, {"reviewer": "reviewer", "end": END}
        )
        graph.add_conditional_edges(
            "reviewer", self._edge, {"decide": "decide", "end": END}
        )
        graph.add_conditional_edges(
            "decide", self._edge, {"implementer": "implementer", "end": END}
        )
        return graph.compile()

    @staticmethod
    def _edge(state: DevLoopState) -> str:
        return state["next_node"]

    def invoke(
        self,
        *,
        run_id: str,
        objective: str,
        repo_root: str | Path,
        allowed_paths: Sequence[str],
        acceptance_commands: Sequence[Sequence[str]],
        expected_dirty_paths: Sequence[str] = (),
        max_rounds: int = 3,
        max_agent_calls: int = 7,
        command_timeout_seconds: float = 600.0,
    ) -> DevLoopState:
        """Run until a bounded terminal recommendation is reached.

        ``expected_dirty_paths`` declares pre-existing user changes.  They are
        accepted by preflight but fingerprinted, so an Agent changing one still
        needs that path to be inside ``allowed_paths``.
        """

        initial = _initial_state(
            run_id=run_id,
            objective=objective,
            repo_root=repo_root,
            allowed_paths=allowed_paths,
            acceptance_commands=acceptance_commands,
            expected_dirty_paths=expected_dirty_paths,
            max_rounds=max_rounds,
            max_agent_calls=max_agent_calls,
            command_timeout_seconds=command_timeout_seconds,
        )
        recursion_limit = max(20, max_rounds * 5 + 8)
        try:
            result = self._compiled.invoke(
                initial, config={"recursion_limit": recursion_limit}
            )
        except Exception:
            failed = deepcopy(initial)
            failed.update(
                decision="HUMAN_REVIEW",
                reasons=["GRAPH_RUNTIME_EXCEPTION"],
                next_node="end",
                history=_append_history(
                    failed,
                    stage="graph",
                    outcome="HUMAN_REVIEW",
                    reasons=["GRAPH_RUNTIME_EXCEPTION"],
                ),
            )
            return DevLoopState(**failed)
        return DevLoopState(**result)

    def _preflight(self, state: DevLoopState) -> dict[str, Any]:
        try:
            repo = git_root(Path(state["repo_root"]))
            actual_dirty = dirty_paths(repo)
            unexpected = sorted(set(actual_dirty) - set(state["expected_dirty_paths"]))
            if unexpected:
                return _terminal_update(
                    state,
                    stage="preflight",
                    decision="HUMAN_REVIEW",
                    reasons=["UNEXPECTED_DIRTY_PATHS"],
                )
            baseline = snapshot(repo, actual_dirty)
            digest = worktree_diff_digest(repo)
            commit = head_commit(repo)
        except (GateError, OSError):
            return _terminal_update(
                state,
                stage="preflight",
                decision="HUMAN_REVIEW",
                reasons=["PREFLIGHT_FAILED"],
            )
        return {
            "base_commit": commit,
            "baseline_snapshot": baseline,
            "baseline_diff_digest": digest,
            "diff_digest": digest,
            "next_node": "planner",
            "history": _append_history(
                state, stage="preflight", outcome="PASS", reasons=[]
            ),
        }

    def _planner(self, state: DevLoopState) -> dict[str, Any]:
        if state["agent_calls"] >= state["max_agent_calls"]:
            return _terminal_update(
                state,
                stage="planner",
                decision="HUMAN_REVIEW",
                reasons=["AGENT_CALL_BUDGET_EXHAUSTED"],
            )
        before = _repository_gate(state, expected_digest=state["diff_digest"])
        if before["reasons"]:
            return _repository_terminal(state, "planner", before)
        request = _agent_request(state, role="planner")
        receipt: dict[str, Any] | None = None
        agent_reasons: list[str] = []
        try:
            receipt = _normalize_plan(self._agents.planner(request))
        except Exception:
            agent_reasons.append("AGENT_HANDLER_OR_RECEIPT_FAILED")
        calls = state["agent_calls"] + 1
        after = _repository_gate(state)
        if (
            after["diff_digest"] is not None
            and after["diff_digest"] != before["diff_digest"]
        ):
            after["reasons"].append("READ_ONLY_AGENT_MUTATED_GIT_WORKTREE")
        gate_reasons = _merge_reasons(after["reasons"])
        if agent_reasons or gate_reasons:
            return _repository_terminal(
                state,
                "planner",
                after,
                reasons=_merge_reasons(agent_reasons, gate_reasons),
                agent_calls=calls,
            )
        assert receipt is not None
        if receipt["status"] != "READY":
            reason = (
                "PLANNER_NEEDS_INPUT"
                if receipt["status"] == "NEEDS_INPUT"
                else "PLANNER_BLOCKED"
            )
            return _terminal_update(
                state,
                stage="planner",
                decision="HUMAN_REVIEW",
                reasons=[reason],
                agent_calls=calls,
            )
        return {
            "plan": receipt,
            "agent_calls": calls,
            "next_node": "implementer",
            "history": _append_history(
                state,
                stage="planner",
                outcome="READY",
                reasons=[],
                agent_calls=calls,
            ),
        }

    def _implementer(self, state: DevLoopState) -> dict[str, Any]:
        if state["agent_calls"] >= state["max_agent_calls"]:
            return _terminal_update(
                state,
                stage="implementer",
                decision="HUMAN_REVIEW",
                reasons=["AGENT_CALL_BUDGET_EXHAUSTED"],
            )
        before = _repository_gate(state, expected_digest=state["diff_digest"])
        if before["reasons"]:
            return _repository_terminal(state, "implementer", before)
        request = _agent_request(state, role="implementer")
        receipt: dict[str, Any] | None = None
        agent_reasons: list[str] = []
        try:
            receipt = _normalize_implementation(self._agents.implementer(request))
        except Exception:
            agent_reasons.append("AGENT_HANDLER_OR_RECEIPT_FAILED")
        calls = state["agent_calls"] + 1
        after = _repository_gate(state)
        gate_reasons = _merge_reasons(after["reasons"])
        if agent_reasons or gate_reasons:
            return _repository_terminal(
                state,
                "implementer",
                after,
                reasons=_merge_reasons(agent_reasons, gate_reasons),
                agent_calls=calls,
                previous_diff_digest=before["diff_digest"],
            )
        assert receipt is not None
        if receipt["status"] == "BLOCKED":
            return _repository_terminal(
                state,
                "implementer",
                after,
                reasons=["IMPLEMENTER_BLOCKED"],
                agent_calls=calls,
                decision="BLOCKED",
                previous_diff_digest=before["diff_digest"],
            )
        return {
            "changed_files": after["changed_files"],
            "previous_diff_digest": before["diff_digest"],
            "diff_digest": after["diff_digest"],
            "agent_calls": calls,
            "next_node": "verify",
            "history": _append_history(
                state,
                stage="implementer",
                outcome="IMPLEMENTED",
                reasons=[],
                agent_calls=calls,
                diff_digest=after["diff_digest"],
            ),
        }

    def _verify(self, state: DevLoopState) -> dict[str, Any]:
        expected_digest = state["diff_digest"]
        before = _repository_gate(state, expected_digest=expected_digest)
        if before["reasons"]:
            return _repository_terminal(state, "verify", before)

        receipts: list[VerificationReceipt] = []
        for command in state["acceptance_commands"]:
            try:
                receipts.extend(
                    run_acceptance_commands(
                        Path(state["repo_root"]),
                        [command],
                        timeout_seconds=state["command_timeout_seconds"],
                    )
                )
            except (GateError, OSError):
                receipts.append(_failed_verification_receipt())
            after_command = _repository_gate(state, expected_digest=expected_digest)
            if after_command["reasons"]:
                reasons = list(after_command["reasons"])
                if after_command["diff_digest"] != expected_digest:
                    reasons.append("VERIFICATION_MUTATED_GIT_WORKTREE")
                return _repository_terminal(
                    state,
                    "verify",
                    after_command,
                    reasons=_merge_reasons(reasons),
                    verification_receipts=receipts,
                )
        passed = bool(receipts) and all(
            item["returncode"] == 0 and not item["timed_out"] for item in receipts
        )
        reasons = [] if passed else ["VERIFICATION_FAILED"]
        return {
            "verification_receipts": receipts,
            "next_node": "reviewer",
            "history": _append_history(
                state,
                stage="verify",
                outcome="PASS" if passed else "FAIL",
                reasons=reasons,
            ),
        }

    def _reviewer(self, state: DevLoopState) -> dict[str, Any]:
        if state["agent_calls"] >= state["max_agent_calls"]:
            return _terminal_update(
                state,
                stage="reviewer",
                decision="HUMAN_REVIEW",
                reasons=["AGENT_CALL_BUDGET_EXHAUSTED"],
            )
        before = _repository_gate(state, expected_digest=state["diff_digest"])
        if before["reasons"]:
            return _repository_terminal(state, "reviewer", before)
        request = _agent_request(state, role="reviewer")
        receipt: dict[str, Any] | None = None
        agent_reasons: list[str] = []
        try:
            receipt = _normalize_review(self._agents.reviewer(request))
        except Exception:
            agent_reasons.append("AGENT_HANDLER_OR_RECEIPT_FAILED")
        calls = state["agent_calls"] + 1
        after = _repository_gate(state)
        if (
            after["diff_digest"] is not None
            and after["diff_digest"] != before["diff_digest"]
        ):
            after["reasons"].append("READ_ONLY_AGENT_MUTATED_GIT_WORKTREE")
        gate_reasons = _merge_reasons(after["reasons"])
        if agent_reasons or gate_reasons:
            return _repository_terminal(
                state,
                "reviewer",
                after,
                reasons=_merge_reasons(agent_reasons, gate_reasons),
                agent_calls=calls,
            )
        assert receipt is not None
        return {
            "review_decision": receipt["decision"],
            "review_findings": receipt["findings"],
            "agent_calls": calls,
            "next_node": "decide",
            "history": _append_history(
                state,
                stage="reviewer",
                outcome=receipt["decision"],
                reasons=[],
                agent_calls=calls,
            ),
        }

    def _decide(self, state: DevLoopState) -> dict[str, Any]:
        verification_passed = bool(state["verification_receipts"]) and all(
            item["returncode"] == 0 and not item["timed_out"]
            for item in state["verification_receipts"]
        )
        serious = any(
            item.get("severity") in {"P0", "P1"} for item in state["review_findings"]
        )
        if state["review_decision"] == "HUMAN_REVIEW":
            return _terminal_update(
                state,
                stage="decide",
                decision="HUMAN_REVIEW",
                reasons=["REVIEWER_REQUESTED_HUMAN"],
            )

        needs_revision: list[str] = []
        if not verification_passed:
            needs_revision.append("VERIFICATION_FAILED")
        if serious or state["review_decision"] == "REVISE":
            needs_revision.append("REVIEW_REQUIRES_REVISION")
        if not state["changed_files"]:
            needs_revision.append("NO_EFFECTIVE_CHANGE")

        if needs_revision:
            if (
                state["previous_diff_digest"] is not None
                and state["diff_digest"] == state["previous_diff_digest"]
            ):
                return _terminal_update(
                    state,
                    stage="decide",
                    decision="HUMAN_REVIEW",
                    reasons=[*needs_revision, "NO_PROGRESS"],
                )
            if state["round"] >= state["max_rounds"]:
                return _terminal_update(
                    state,
                    stage="decide",
                    decision="HUMAN_REVIEW",
                    reasons=[*needs_revision, "MAX_ROUNDS_EXHAUSTED"],
                )
            if state["agent_calls"] + 2 > state["max_agent_calls"]:
                return _terminal_update(
                    state,
                    stage="decide",
                    decision="HUMAN_REVIEW",
                    reasons=[*needs_revision, "AGENT_CALL_BUDGET_EXHAUSTED"],
                )
            return {
                "round": state["round"] + 1,
                "decision": "IN_PROGRESS",
                "reasons": _merge_reasons(state["reasons"], needs_revision),
                "next_node": "implementer",
                "history": _append_history(
                    state,
                    stage="decide",
                    outcome="REVISE",
                    reasons=needs_revision,
                ),
            }

        if state["review_decision"] != "APPROVE":
            return _terminal_update(
                state,
                stage="decide",
                decision="HUMAN_REVIEW",
                reasons=["REVIEW_DECISION_INVALID"],
            )
        return _terminal_update(
            state,
            stage="decide",
            decision="READY_FOR_HUMAN",
            reasons=[],
        )


def _initial_state(
    *,
    run_id: str,
    objective: str,
    repo_root: str | Path,
    allowed_paths: Sequence[str],
    acceptance_commands: Sequence[Sequence[str]],
    expected_dirty_paths: Sequence[str],
    max_rounds: int,
    max_agent_calls: int,
    command_timeout_seconds: float,
) -> DevLoopState:
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise DevLoopConfigurationError("run_id is invalid")
    if not isinstance(objective, str) or not objective.strip():
        raise DevLoopConfigurationError("objective must be non-empty text")
    if type(max_rounds) is not int or not 1 <= max_rounds <= 20:
        raise DevLoopConfigurationError("max_rounds must be between 1 and 20")
    if type(max_agent_calls) is not int or not 3 <= max_agent_calls <= 100:
        raise DevLoopConfigurationError("max_agent_calls must be between 3 and 100")
    if isinstance(command_timeout_seconds, bool) or not isinstance(
        command_timeout_seconds, (int, float)
    ):
        raise DevLoopConfigurationError("command timeout must be numeric")
    timeout = float(command_timeout_seconds)
    if not math.isfinite(timeout) or not 0 < timeout <= 3_600:
        raise DevLoopConfigurationError("command timeout must be in (0, 3600]")
    try:
        allowed = [normalize_allow_pattern(item) for item in allowed_paths]
        expected = [normalize_repo_path(item) for item in expected_dirty_paths]
    except GateError as exc:
        raise DevLoopConfigurationError(str(exc)) from exc
    if not allowed:
        raise DevLoopConfigurationError("allowed_paths cannot be empty")
    commands: list[list[str]] = []
    for raw in acceptance_commands:
        if isinstance(raw, (str, bytes)) or not raw:
            raise DevLoopConfigurationError(
                "acceptance commands must be non-empty argv lists"
            )
        command = list(raw)
        if any(
            not isinstance(item, str) or not item or "\x00" in item for item in command
        ):
            raise DevLoopConfigurationError("acceptance command arguments are invalid")
        commands.append(command)
    if not commands:
        raise DevLoopConfigurationError("at least one acceptance command is required")
    root = Path(repo_root).resolve()
    return {
        "run_id": run_id,
        "objective": objective.strip(),
        "repo_root": str(root),
        "base_commit": "",
        "allowed_paths": allowed,
        "expected_dirty_paths": sorted(set(expected)),
        "acceptance_commands": commands,
        "command_timeout_seconds": timeout,
        "baseline_snapshot": {},
        "baseline_diff_digest": "",
        "plan": {},
        "round": 1,
        "max_rounds": max_rounds,
        "agent_calls": 0,
        "max_agent_calls": max_agent_calls,
        "changed_files": [],
        "previous_diff_digest": None,
        "diff_digest": None,
        "verification_receipts": [],
        "review_findings": [],
        "review_decision": None,
        "decision": "IN_PROGRESS",
        "reasons": [],
        "history": [],
        "next_node": "planner",
    }


def _agent_request(state: DevLoopState, *, role: str) -> dict[str, Any]:
    common: dict[str, Any] = {
        "role": role,
        "run_id": state["run_id"],
        "objective": state["objective"],
        "repo_root": state["repo_root"],
        "base_commit": state["base_commit"],
        "allowed_paths": list(state["allowed_paths"]),
        "expected_dirty_paths": list(state["expected_dirty_paths"]),
        "acceptance_commands": deepcopy(state["acceptance_commands"]),
        "round": state["round"],
        "max_rounds": state["max_rounds"],
    }
    if role != "planner":
        common["plan"] = deepcopy(state["plan"])
        common["changed_files"] = list(state["changed_files"])
        common["prior_reasons"] = list(state["reasons"])
    if role == "reviewer":
        common["verification_receipts"] = deepcopy(state["verification_receipts"])
    return common


def _normalize_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _mapping(value)
    if set(record) != {"status", "summary", "steps", "risks"}:
        raise ValueError("planner receipt fields are invalid")
    status = _choice(record["status"], _PLAN_STATUSES)
    return {
        "status": status,
        "summary": _text(record["summary"]),
        "steps": _text_list(record["steps"]),
        "risks": _text_list(record["risks"]),
    }


def _normalize_implementation(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _mapping(value)
    if set(record) != {"status", "summary", "blockers"}:
        raise ValueError("implementation receipt fields are invalid")
    return {
        "status": _choice(record["status"], _IMPLEMENTATION_STATUSES),
        "summary": _text(record["summary"]),
        "blockers": _text_list(record["blockers"]),
    }


def _normalize_review(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _mapping(value)
    if set(record) != {"decision", "findings"}:
        raise ValueError("review receipt fields are invalid")
    raw_findings = record["findings"]
    if not isinstance(raw_findings, list):
        raise ValueError("findings must be a list")
    findings: list[Finding] = []
    for raw in raw_findings:
        item = _mapping(raw)
        if not set(item).issubset({"severity", "message", "file", "line"}):
            raise ValueError("finding fields are invalid")
        if set(item) < {"severity", "message"}:
            raise ValueError("finding requires severity and message")
        finding: Finding = {
            "severity": _choice(item["severity"], _SEVERITIES),
            "message": _text(item["message"]),
        }
        if "file" in item:
            finding["file"] = normalize_repo_path(_text(item["file"]))
        if "line" in item:
            line = item["line"]
            if type(line) is not int or line < 1:
                raise ValueError("finding line must be a positive integer")
            finding["line"] = line
        findings.append(finding)
    return {
        "decision": _choice(record["decision"], _REVIEW_DECISIONS),
        "findings": findings,
    }


def _repository_gate(
    state: DevLoopState,
    *,
    expected_digest: str | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    changed: list[str] = []
    digest: str | None = None
    try:
        repo = Path(state["repo_root"])
        commit = head_commit(repo)
        if commit != state["base_commit"]:
            reasons.append("BASE_COMMIT_CHANGED")
        changed, _current = changed_since_snapshot(repo, state["baseline_snapshot"])
        outside = outside_allowlist(changed, state["allowed_paths"])
        if outside:
            reasons.append("WRITE_OUTSIDE_ALLOWLIST")
        digest = worktree_diff_digest(repo)
        if expected_digest is not None and digest != expected_digest:
            reasons.append("GIT_WORKTREE_CHANGED_OUTSIDE_STAGE")
    except (GateError, OSError):
        reasons.append("REPOSITORY_GATE_FAILED")
    return {
        "changed_files": changed,
        "diff_digest": digest,
        "reasons": _merge_reasons(reasons),
    }


def _repository_terminal(
    state: DevLoopState,
    stage: str,
    evidence: Mapping[str, Any],
    *,
    reasons: list[str] | None = None,
    agent_calls: int | None = None,
    decision: str = "HUMAN_REVIEW",
    previous_diff_digest: str | None = None,
    verification_receipts: list[VerificationReceipt] | None = None,
) -> dict[str, Any]:
    terminal_reasons = (
        list(evidence.get("reasons", [])) if reasons is None else list(reasons)
    )
    update = _terminal_update(
        state,
        stage=stage,
        decision=decision,
        reasons=terminal_reasons,
        agent_calls=agent_calls,
    )
    update["changed_files"] = list(evidence.get("changed_files", []))
    update["diff_digest"] = evidence.get("diff_digest")
    if previous_diff_digest is not None:
        update["previous_diff_digest"] = previous_diff_digest
    if verification_receipts is not None:
        update["verification_receipts"] = list(verification_receipts)
    return update


def _terminal_update(
    state: DevLoopState,
    *,
    stage: str,
    decision: str,
    reasons: list[str],
    agent_calls: int | None = None,
) -> dict[str, Any]:
    calls = state["agent_calls"] if agent_calls is None else agent_calls
    merged_reasons = (
        []
        if decision == "READY_FOR_HUMAN"
        else _merge_reasons(state["reasons"], reasons)
    )
    return {
        "decision": decision,
        "reasons": merged_reasons,
        "agent_calls": calls,
        "next_node": "end",
        "history": _append_history(
            state,
            stage=stage,
            outcome=decision,
            reasons=reasons,
            agent_calls=calls,
        ),
    }


def _append_history(
    state: DevLoopState,
    *,
    stage: str,
    outcome: str,
    reasons: list[str],
    agent_calls: int | None = None,
    diff_digest: str | None = None,
) -> list[dict[str, Any]]:
    entry: dict[str, Any] = {
        "sequence": len(state["history"]) + 1,
        "round": state["round"],
        "stage": stage,
        "outcome": outcome,
        "reasons": list(reasons),
        "agent_calls": state["agent_calls"] if agent_calls is None else agent_calls,
    }
    if diff_digest is not None:
        entry["diff_digest"] = diff_digest
    return [*deepcopy(state["history"]), entry]


def _merge_reasons(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for item in group:
            if item not in merged:
                merged.append(item)
    return merged


def _failed_verification_receipt() -> VerificationReceipt:
    return {
        "command_digest": "sha256:" + "0" * 64,
        "argv_count": 0,
        "returncode": None,
        "timed_out": False,
        "truncated": False,
        "output_digest": "sha256:" + "0" * 64,
        "output_bytes": 0,
    }


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("Agent receipt must be an object")
    return deepcopy(dict(value))


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("receipt text must be non-empty")
    return value.strip()


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("receipt field must be a list")
    return [_text(item) for item in value]


def _choice(value: object, choices: frozenset[str]) -> str:
    text = _text(value).upper()
    if text not in choices:
        raise ValueError("receipt choice is unsupported")
    return text


__all__ = ["DevLoop", "DevLoopConfigurationError"]

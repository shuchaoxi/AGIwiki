from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import os
import subprocess
import sys
import time

import pytest

from agiwiki_devloop import AgentSet, DevLoop, DevLoopConfigurationError
from agiwiki_devloop.gates import (
    MAX_VERIFICATION_OUTPUT_BYTES,
    run_acceptance_commands,
)


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "devloop@example.invalid")
    _git(root, "config", "user.name", "Dev Loop Test")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "base.txt")
    _git(root, "commit", "-qm", "base")
    return root


def _planner(_request):
    return {
        "status": "READY",
        "summary": "Make the bounded test change.",
        "steps": ["Edit the allowed file", "Run verification"],
        "risks": ["Do not edit files outside the allowlist"],
    }


def _reviewer(_request):
    return {"decision": "APPROVE", "findings": []}


def _loop(implementer: Callable, reviewer: Callable = _reviewer) -> DevLoop:
    return DevLoop(
        AgentSet(planner=_planner, implementer=implementer, reviewer=reviewer)
    )


def _content_check(path: str, expected: str) -> list[str]:
    script = (
        "from pathlib import Path; "
        f"raise SystemExit(0 if Path({path!r}).read_text() == {expected!r} else 1)"
    )
    return [sys.executable, "-c", script]


def test_success_reaches_ready_for_human_without_committing(repo: Path) -> None:
    def implement(request):
        Path(request["repo_root"], "allowed.txt").write_text("done\n", encoding="utf-8")
        return {"status": "IMPLEMENTED", "summary": "Wrote file", "blockers": []}

    state = _loop(implement).invoke(
        run_id="success",
        objective="Create the allowed file",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[_content_check("allowed.txt", "done\n")],
    )

    assert state["decision"] == "READY_FOR_HUMAN"
    assert state["changed_files"] == ["allowed.txt"]
    assert state["agent_calls"] == 3
    assert [item["stage"] for item in state["history"]] == [
        "preflight",
        "planner",
        "implementer",
        "verify",
        "reviewer",
        "decide",
    ]
    assert (
        subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "base"
    )


def test_failed_verification_loops_to_a_second_implementation(repo: Path) -> None:
    calls = 0

    def implement(request):
        nonlocal calls
        calls += 1
        value = "wrong\n" if calls == 1 else "correct\n"
        Path(request["repo_root"], "allowed.txt").write_text(value, encoding="utf-8")
        return {"status": "IMPLEMENTED", "summary": "Updated file", "blockers": []}

    state = _loop(implement).invoke(
        run_id="revision",
        objective="Create correct content",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[_content_check("allowed.txt", "correct\n")],
    )

    assert state["decision"] == "READY_FOR_HUMAN"
    assert state["reasons"] == []
    assert state["round"] == 2
    assert calls == 2
    assert [item["outcome"] for item in state["history"]].count("REVISE") == 1


def test_expected_dirty_path_is_accepted_and_fingerprinted(repo: Path) -> None:
    (repo / "base.txt").write_text("user change\n", encoding="utf-8")

    def implement(request):
        Path(request["repo_root"], "allowed.txt").write_text("done\n", encoding="utf-8")
        return {"status": "IMPLEMENTED", "summary": "Wrote file", "blockers": []}

    state = _loop(implement).invoke(
        run_id="dirty-accepted",
        objective="Preserve user change",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        expected_dirty_paths=["base.txt"],
        acceptance_commands=[_content_check("allowed.txt", "done\n")],
    )

    assert state["decision"] == "READY_FOR_HUMAN"
    assert state["changed_files"] == ["allowed.txt"]
    assert (repo / "base.txt").read_text(encoding="utf-8") == "user change\n"


def test_expected_dirty_file_mode_change_still_requires_allowlist(repo: Path) -> None:
    _git(repo, "config", "core.filemode", "false")
    path = repo / "base.txt"
    path.write_text("user change\n", encoding="utf-8")

    def implement(request):
        os.chmod(path, 0o755)
        Path(request["repo_root"], "allowed.txt").write_text("done\n", encoding="utf-8")
        return {"status": "IMPLEMENTED", "summary": "Wrote file", "blockers": []}

    state = _loop(implement).invoke(
        run_id="dirty-mode-change",
        objective="Do not mutate the forbidden dirty file mode",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        expected_dirty_paths=["base.txt"],
        acceptance_commands=[[sys.executable, "-c", "raise SystemExit(0)"]],
    )

    assert state["decision"] == "HUMAN_REVIEW"
    assert state["reasons"] == ["WRITE_OUTSIDE_ALLOWLIST"]
    assert state["changed_files"] == ["allowed.txt", "base.txt"]


def test_unexpected_dirty_path_stops_before_any_agent(repo: Path) -> None:
    (repo / "base.txt").write_text("undeclared\n", encoding="utf-8")
    calls = []

    def forbidden(_request):
        calls.append(True)
        raise AssertionError("Agent must not run")

    state = DevLoop(
        AgentSet(planner=forbidden, implementer=forbidden, reviewer=forbidden)
    ).invoke(
        run_id="dirty-rejected",
        objective="Do nothing",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[[sys.executable, "-c", "raise SystemExit(0)"]],
    )

    assert state["decision"] == "HUMAN_REVIEW"
    assert state["reasons"] == ["UNEXPECTED_DIRTY_PATHS"]
    assert calls == []


def test_write_outside_allowlist_fails_closed(repo: Path) -> None:
    def implement(request):
        Path(request["repo_root"], "forbidden.txt").write_text(
            "bad\n", encoding="utf-8"
        )
        return {"status": "IMPLEMENTED", "summary": "Wrong file", "blockers": []}

    state = _loop(implement).invoke(
        run_id="scope",
        objective="Stay in scope",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[[sys.executable, "-c", "raise SystemExit(0)"]],
    )

    assert state["decision"] == "HUMAN_REVIEW"
    assert state["reasons"] == ["WRITE_OUTSIDE_ALLOWLIST"]
    assert state["changed_files"] == ["forbidden.txt"]


def test_read_only_agent_mutation_is_detected(repo: Path) -> None:
    def mutating_planner(request):
        Path(request["repo_root"], "allowed.txt").write_text("bad\n", encoding="utf-8")
        return _planner(request)

    state = DevLoop(
        AgentSet(
            planner=mutating_planner, implementer=lambda _: {}, reviewer=lambda _: {}
        )
    ).invoke(
        run_id="read-only",
        objective="Planner must not write",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[[sys.executable, "-c", "raise SystemExit(0)"]],
    )

    assert state["decision"] == "HUMAN_REVIEW"
    assert state["reasons"] == ["READ_ONLY_AGENT_MUTATED_GIT_WORKTREE"]


def test_repeated_no_progress_stops_the_loop(repo: Path) -> None:
    def no_change(_request):
        return {"status": "IMPLEMENTED", "summary": "No change", "blockers": []}

    state = _loop(no_change).invoke(
        run_id="no-progress",
        objective="A change is required",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[[sys.executable, "-c", "raise SystemExit(0)"]],
    )

    assert state["decision"] == "HUMAN_REVIEW"
    assert "NO_EFFECTIVE_CHANGE" in state["reasons"]
    assert "NO_PROGRESS" in state["reasons"]
    assert state["round"] == 1


def test_agent_exception_does_not_copy_secret_message(repo: Path) -> None:
    def broken(_request):
        raise RuntimeError("secret-token-must-not-enter-state")

    state = DevLoop(
        AgentSet(planner=broken, implementer=lambda _: {}, reviewer=lambda _: {})
    ).invoke(
        run_id="exception",
        objective="Fail closed",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[[sys.executable, "-c", "raise SystemExit(0)"]],
    )

    assert state["decision"] == "HUMAN_REVIEW"
    assert state["reasons"] == ["AGENT_HANDLER_OR_RECEIPT_FAILED"]
    assert "secret-token" not in repr(state)


def test_invalid_planner_receipt_still_runs_the_post_mutation_gate(
    repo: Path,
) -> None:
    def invalid_and_mutating(request):
        Path(request["repo_root"], "forbidden.txt").write_text(
            "bad\n", encoding="utf-8"
        )
        return {"status": "READY"}

    state = DevLoop(
        AgentSet(
            planner=invalid_and_mutating,
            implementer=lambda _: {},
            reviewer=lambda _: {},
        )
    ).invoke(
        run_id="invalid-planner",
        objective="Fail after checking the worktree",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[[sys.executable, "-c", "raise SystemExit(0)"]],
    )

    assert state["decision"] == "HUMAN_REVIEW"
    assert state["changed_files"] == ["forbidden.txt"]
    assert state["reasons"] == [
        "AGENT_HANDLER_OR_RECEIPT_FAILED",
        "WRITE_OUTSIDE_ALLOWLIST",
        "READ_ONLY_AGENT_MUTATED_GIT_WORKTREE",
    ]


def test_implementer_exception_still_reports_allowed_mutation(repo: Path) -> None:
    def mutate_then_raise(request):
        Path(request["repo_root"], "allowed.txt").write_text(
            "partial\n", encoding="utf-8"
        )
        raise RuntimeError("private implementation failure")

    state = _loop(mutate_then_raise).invoke(
        run_id="implementation-exception",
        objective="Inspect partial writes",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[[sys.executable, "-c", "raise SystemExit(0)"]],
    )

    assert state["decision"] == "HUMAN_REVIEW"
    assert state["changed_files"] == ["allowed.txt"]
    assert state["reasons"] == ["AGENT_HANDLER_OR_RECEIPT_FAILED"]
    assert "private implementation failure" not in repr(state)


def test_agent_commit_changes_head_and_stops_immediately(repo: Path) -> None:
    def committing_implementer(request):
        root = Path(request["repo_root"])
        (root / "allowed.txt").write_text("committed\n", encoding="utf-8")
        _git(root, "add", "allowed.txt")
        _git(root, "commit", "-qm", "forbidden agent commit")
        return {"status": "IMPLEMENTED", "summary": "Committed", "blockers": []}

    state = _loop(committing_implementer).invoke(
        run_id="commit-drift",
        objective="Agents must not commit",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[[sys.executable, "-c", "raise SystemExit(0)"]],
    )

    assert state["decision"] == "HUMAN_REVIEW"
    assert "BASE_COMMIT_CHANGED" in state["reasons"]


def test_verification_may_not_change_even_an_allowed_file(repo: Path) -> None:
    def implement(request):
        Path(request["repo_root"], "allowed.txt").write_text(
            "before verify\n", encoding="utf-8"
        )
        return {"status": "IMPLEMENTED", "summary": "Wrote file", "blockers": []}

    mutate = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('allowed.txt').write_text('from verify\\n')",
    ]
    state = _loop(implement).invoke(
        run_id="verify-mutation",
        objective="Verification must be observational",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[mutate],
    )

    assert state["decision"] == "HUMAN_REVIEW"
    assert "GIT_WORKTREE_CHANGED_OUTSIDE_STAGE" in state["reasons"]
    assert "VERIFICATION_MUTATED_GIT_WORKTREE" in state["reasons"]


def test_verification_commit_is_detected_as_base_drift(repo: Path) -> None:
    def implement(request):
        Path(request["repo_root"], "allowed.txt").write_text(
            "ready\n", encoding="utf-8"
        )
        return {"status": "IMPLEMENTED", "summary": "Wrote file", "blockers": []}

    commit_script = (
        "import subprocess; "
        "subprocess.run(['git','add','allowed.txt'],check=True); "
        "subprocess.run(['git','commit','-qm','verification commit'],check=True)"
    )
    state = _loop(implement).invoke(
        run_id="verify-commit",
        objective="Verification cannot commit",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[[sys.executable, "-c", commit_script]],
    )

    assert state["decision"] == "HUMAN_REVIEW"
    assert "BASE_COMMIT_CHANGED" in state["reasons"]


def test_invalid_reviewer_receipt_still_checks_read_only_boundary(repo: Path) -> None:
    def implement(request):
        Path(request["repo_root"], "allowed.txt").write_text(
            "implemented\n", encoding="utf-8"
        )
        return {"status": "IMPLEMENTED", "summary": "Wrote file", "blockers": []}

    def invalid_reviewer(request):
        Path(request["repo_root"], "allowed.txt").write_text(
            "reviewer changed it\n", encoding="utf-8"
        )
        return {"decision": "APPROVE"}

    state = _loop(implement, reviewer=invalid_reviewer).invoke(
        run_id="invalid-reviewer",
        objective="Reviewer is read-only",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[[sys.executable, "-c", "raise SystemExit(0)"]],
    )

    assert state["decision"] == "HUMAN_REVIEW"
    assert state["reasons"] == [
        "AGENT_HANDLER_OR_RECEIPT_FAILED",
        "READ_ONLY_AGENT_MUTATED_GIT_WORKTREE",
    ]


def test_verification_output_is_reduced_to_digest_and_size(repo: Path) -> None:
    marker = "verification-secret-must-not-enter-state"

    def implement(request):
        Path(request["repo_root"], "allowed.txt").write_text("done\n", encoding="utf-8")
        return {"status": "IMPLEMENTED", "summary": "Wrote file", "blockers": []}

    state = _loop(implement).invoke(
        run_id="private-verification",
        objective="Do not retain verification output",
        repo_root=repo,
        allowed_paths=["allowed.txt"],
        acceptance_commands=[
            [
                sys.executable,
                "-c",
                "print('verification-' + 'secret-must-not-enter-state'); "
                "raise SystemExit(1)",
            ]
        ],
        max_rounds=1,
    )

    receipt = state["verification_receipts"][0]
    assert set(receipt) == {
        "command_digest",
        "argv_count",
        "returncode",
        "timed_out",
        "truncated",
        "output_digest",
        "output_bytes",
    }
    assert receipt["command_digest"].startswith("sha256:")
    assert receipt["argv_count"] == 3
    assert receipt["truncated"] is False
    assert receipt["output_bytes"] >= len(marker)
    assert marker not in repr(receipt)
    assert "secret-must-not-enter-state" not in repr(receipt)


def test_verification_output_is_killed_at_hard_limit(repo: Path) -> None:
    command = [
        sys.executable,
        "-c",
        f"import os; os.write(1, b'x' * ({MAX_VERIFICATION_OUTPUT_BYTES} + 1))",
    ]

    receipt = run_acceptance_commands(repo, [command], timeout_seconds=5.0)[0]

    assert receipt["truncated"] is True
    assert receipt["timed_out"] is False
    assert receipt["output_bytes"] == MAX_VERIFICATION_OUTPUT_BYTES
    assert "import os" not in repr(receipt)


def test_timeout_kills_verifier_process_group(repo: Path) -> None:
    escaped = repo / "escaped.txt"
    grandchild = (
        "import time; from pathlib import Path; time.sleep(0.4); "
        f"Path({str(escaped)!r}).write_text('escaped', encoding='utf-8')"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
        "time.sleep(10)"
    )

    receipt = run_acceptance_commands(
        repo, [[sys.executable, "-c", parent]], timeout_seconds=0.1
    )[0]
    time.sleep(0.6)

    assert receipt["timed_out"] is True
    assert receipt["returncode"] is None
    assert escaped.exists() is False


@pytest.mark.parametrize("value", [0, 21, True, "3"])
def test_invalid_round_limit_is_rejected(repo: Path, value) -> None:
    with pytest.raises(DevLoopConfigurationError):
        _loop(lambda _: {}).invoke(
            run_id="invalid",
            objective="Reject invalid limit",
            repo_root=repo,
            allowed_paths=["allowed.txt"],
            acceptance_commands=[[sys.executable, "-c", "raise SystemExit(0)"]],
            max_rounds=value,
        )


@pytest.mark.parametrize(
    "pattern",
    ["src/*.py", "tests/test_?.py", "docs/[ab].md", "src/**/nested"],
)
def test_allowlist_rejects_every_glob_except_directory_recursive(
    repo: Path, pattern: str
) -> None:
    with pytest.raises(
        DevLoopConfigurationError,
        match="only exact paths and directory/\\*\\*",
    ):
        _loop(lambda _: {}).invoke(
            run_id="invalid-allowlist",
            objective="Reject ambiguous patterns",
            repo_root=repo,
            allowed_paths=[pattern],
            acceptance_commands=[[sys.executable, "-c", "raise SystemExit(0)"]],
        )

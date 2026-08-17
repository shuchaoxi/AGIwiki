# Bounded LangGraph development loop

Status: repository development tool, not part of the AGIWiki runtime.

AGIWiki includes an optional bounded LangGraph workflow for software iteration. It coordinates
three callbacks supplied by the caller. It does not choose or invoke Codex, Claude, the OpenAI API,
or another model, and it is excluded from `src/agiwiki`, the runtime wheel, CLI, and MCP.

```text
preflight → planner → implementer → verify → reviewer → decide
                         ▲                              │
                         └──────── bounded revise ─────┘
```

## Permission boundary

- Planner and Reviewer are read-only; the graph compares Git-visible state before and after each
  callback.
- Implementer is the only writer. Its final Git-visible changes must match caller-supplied exact
  paths or `directory/**` rules. Other glob forms are rejected.
- `HEAD` must remain equal to the captured `base_commit` before and after every agent callback and
  verification command.
- Verification uses argv arrays with `shell=False`. A command that changes Git-visible state stops
  the run.
- Verification receipts retain only a command digest, argument count, exit status, timeout or
  truncation flag, output digest, and byte count. They do not retain argv or output text.
- Each command runs in a separate process session. Timeout or output above 1 MiB terminates the
  process group.
- The graph never creates a worktree, commits, merges, pushes, publishes, or deploys.
- `READY_FOR_HUMAN` still requires a maintainer to inspect and accept the diff.

Run the graph in an isolated Git worktree whenever possible. If a pre-existing dirty worktree is
unavoidable, list every expected dirty path. Preflight snapshots those paths; later changes still
require write authorization.

## Callback receipts

Planner:

```json
{
  "status": "READY",
  "summary": "Implement one bounded feature",
  "steps": ["Add domain logic", "Add tests"],
  "risks": ["Preserve the existing Schema version"]
}
```

Implementer:

```json
{
  "status": "IMPLEMENTED",
  "summary": "Implemented the feature and tests",
  "blockers": []
}
```

Reviewer:

```json
{
  "decision": "APPROVE",
  "findings": []
}
```

Reviewer findings can use severities `P0` through `P3`, a message, and optional repository-relative
file and positive line number. P0 and P1 findings trigger a bounded revision.

## Inject callbacks

```python
import sys

from agiwiki_devloop import AgentSet, DevLoop

agents = AgentSet(
    planner=my_read_only_planner,
    implementer=my_single_writer,
    reviewer=my_read_only_reviewer,
)

state = DevLoop(agents).invoke(
    run_id="feature-001",
    objective="Implement one approved local feature",
    repo_root="/isolated/AGIwiki-worktree",
    allowed_paths=["src/agiwiki/feature/**", "tests/test_feature.py"],
    expected_dirty_paths=[],
    acceptance_commands=[
        [sys.executable, "-m", "pytest", "-q"],
        [sys.executable, "-m", "ruff", "check", "."],
    ],
    max_rounds=3,
    max_agent_calls=7,
)
```

An external-provider Adapter should enforce separate sessions, structured output, read-only
sandboxes for Planner and Reviewer, and a workspace-write sandbox for Implementer. It must not
bypass approvals or isolation.

## Git gates are not an operating-system sandbox

The graph cannot prove that a process did not write:

- ignored files;
- `.git/` state other than final `HEAD`;
- paths outside the repository;
- network services, credential stores, or other processes.

Use an isolated worktree and an OS-level sandbox with narrowly scoped temporary writes.
`READY_FOR_HUMAN` means only that Git-visible gates, declared verification commands, and the
structured review passed. It is not a security certification.

## Stop conditions

The graph returns `READY_FOR_HUMAN` only when:

- there is a valid change from the baseline;
- every verification command passes;
- Reviewer approves with no P0 or P1;
- `HEAD` never changes;
- Git-visible writes remain inside the allowlist;
- verification leaves no new Git-visible changes.

It fails closed to `HUMAN_REVIEW` or `BLOCKED` for an undeclared initial change, `HEAD` drift,
read-only-role writes, out-of-scope Implementer writes, invalid receipts, callback failures, a
stalled revision, exhausted budgets, or an explicit blocker. Recommended defaults are three rounds,
seven agent calls, and no more than ten minutes per verification command.

## Test

```bash
./.venv/bin/python -m pip install -e '.[dev,devloop]'
env PYTHONPATH=tools ./.venv/bin/python -m pytest -q tools/agiwiki_devloop/tests
```

The tests use temporary Git repositories and injected fake agents. They do not call a network or a
real model.

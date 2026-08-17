"""Injected Agent ports for the repository-side development loop.

The loop never selects a provider and never launches an external Agent.  A
caller must deliberately inject three independent callbacks.  Sandboxing and
provider credentials therefore stay under the caller's control.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class PlannerAgent(Protocol):
    """Read-only role that turns an objective into a small implementation plan."""

    def __call__(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ImplementerAgent(Protocol):
    """The policy-designated writer for Git-visible worktree changes.

    The caller must still enforce an operating-system sandbox.  The graph
    cannot observe ignored files, Git internals, or writes outside the repo.
    """

    def __call__(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ReviewerAgent(Protocol):
    """Read-only role that reviews the resulting diff and verification receipts."""

    def __call__(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class AgentSet:
    """Three deliberately separate Agent roles.

    The callbacks may use any provider or a deterministic test double.  Their
    returned objects are treated as untrusted and normalized by the graph.
    """

    planner: PlannerAgent
    implementer: ImplementerAgent
    reviewer: ReviewerAgent


__all__ = [
    "AgentSet",
    "ImplementerAgent",
    "PlannerAgent",
    "ReviewerAgent",
]

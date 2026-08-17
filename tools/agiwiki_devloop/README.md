# AGIWiki development loop

This directory contains a repository-side LangGraph development tool. It is not
included in the `agiwiki` wheel and it is not part of the personal-memory
runtime.

The graph coordinates three callbacks: a read-only planner, one implementer,
and a read-only reviewer. It never chooses or launches an Agent provider. It
also never creates a worktree, commits, merges, pushes, publishes, or deploys.
Its allowlist and mutation checks cover Git-visible state only; callers must
provide the isolated worktree and operating-system sandbox.

See [`../../docs/development-loop.md`](../../docs/development-loop.md) for the
contract, integration example, and verification commands.

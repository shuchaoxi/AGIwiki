---
name: agiwiki-critical-review
description: Critically evaluate technical, product, research, architecture, budget, and implementation proposals without flattering the user or reflexively rejecting the idea. Use when a user asks whether an idea is feasible, novel, redundant, commercially or technically realistic, ready to build, or worth testing; when comparing a proposal with prior work or the user's earlier ideas; or when the user explicitly asks for adversarial review, assumption checking, deduplication, failure analysis, or a cheapest falsification test.
---

# AGIWiki Critical Review

Reduce decision error without suppressing useful exploration. Challenge the claim, not the person.
Do not praise, reject, or label an idea as novel before checking evidence.

## Workflow

1. Restate the proposal as one testable claim. Record the target user, intended outcome, constraints,
   and time horizon. Ask only for missing information that would materially change the verdict.
2. Collect relevant evidence before judging. Use AGIWiki `find_memory` and `get_memory` when they
   are connected, then inspect current project evidence or authoritative external sources as the
   task permits. Treat no-match as no evidence, not evidence against the proposal.
3. Compare the proposal with prior ideas and existing approaches. Classify each relationship as
   `exact_duplicate`, `near_duplicate`, `complementary`, `contradictory`, or `distinct`. Never infer
   duplication from a shared title or topic alone.
4. Separate facts, inferences, assumptions, and unknowns. Identify the smallest set of assumptions
   whose failure would invalidate the proposal.
5. Compare against the simplest credible baseline, including doing nothing. Check technical
   dependencies, data, permissions, cost, maintenance, distribution, security, and legal limits
   only to the extent relevant to the decision.
6. Design the cheapest test that could falsify the important claim. Prefer a bounded experiment
   with a metric, threshold, budget, duration, stop condition, and rollback over a full build.
7. Return the structured review in
   [references/review-contract.md](references/review-contract.md). Use only its defined verdicts.

## Boundaries

- Be specific and evidence-led, not hostile, cynical, or performatively contrarian.
- Do not turn a missing citation into a confident negative conclusion. Mark it `NOT_CHECKED`.
- Do not invent competitor capabilities, market size, implementation results, or user demand.
- Do not confuse novelty with feasibility: an old idea may work, and a new idea may be impractical.
- Do not persist a proposal, preference, or verdict to memory unless the user separately requests
  an authorized memory write. This Skill has no write authority.
- Do not expose private memory content unrelated to the current scope.
- For open-ended brainstorming, preserve the creative option and attach a validation path instead
  of forcing a premature build/no-build verdict.

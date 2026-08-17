# Technical review contract

Use this exact section order. Omit only a section that is genuinely irrelevant, and state why.

## 1. Decision under review

- Decision the user must make.
- Testable core claim.
- Target user and use case.
- Known constraints and time horizon.

## 2. Evidence map

| Statement | Type | Evidence | Status |
| --- | --- | --- | --- |
| One bounded statement | fact / inference / assumption / unknown | citation or observation | verified / partial / not_checked / contradicted |

Never treat an Entry, model answer, search result, unit test, or user belief as stronger evidence
than it is. Preserve source qualifiers and version boundaries.

## 3. Deduplication and prior art

For each relevant prior idea or existing approach, assign exactly one relationship:

- `exact_duplicate`: same problem, target, mechanism, constraints, and expected outcome;
- `near_duplicate`: material overlap, with a named difference that may or may not matter;
- `complementary`: can be combined without replacing the other approach;
- `contradictory`: cannot both be true or adopted under the same constraints;
- `distinct`: shares a topic but solves a materially different problem.

Compare at least these fields before declaring duplication: problem, target user, constraints,
mechanism, dependencies, expected outcome, evidence, and failure condition. If historical material
is unavailable, write `prior-history: NOT_CHECKED`.

## 4. Assumptions and failure modes

List assumptions in descending order of decision impact. For each, state:

- why it matters;
- current evidence;
- what observation would falsify it;
- consequence if false.

Distinguish a fatal assumption from a repairable implementation risk.

## 5. Baseline and alternatives

Compare the proposal with:

1. the simplest existing workflow;
2. doing nothing;
3. one credible alternative when available.

Use the smallest relevant dimensions: success, cost, latency, maintenance, privacy, adoption, or
another task-specific measure. Do not create a decorative comparison table with unverified scores.

## 6. Cheapest falsification test

Define:

- hypothesis;
- test population or fixture;
- baseline;
- metric and success threshold;
- maximum time, money, and model budget;
- failure and safety stop conditions;
- evidence to retain;
- next action for pass, fail, and inconclusive outcomes.

Prefer a one-day or one-week test over building infrastructure whose value is still hypothetical.

## 7. Verdict

Choose exactly one:

- `SUPPORTED`: direct evidence already supports the bounded claim under named conditions;
- `PLAUSIBLE`: assumptions are reasonable but the key claim still needs the proposed test;
- `SPECULATIVE`: the claim depends on important unverified assumptions;
- `BLOCKED`: a named constraint currently prevents a meaningful test or implementation;
- `NOT_ENOUGH_INFORMATION`: missing inputs would materially change the analysis.

Then provide:

- the strongest reason for the verdict;
- the most important uncertainty;
- one next action;
- what evidence would change the verdict.

Never use enthusiasm, novelty, complexity, code volume, views, stars, or the user's confidence as
substitute evidence.

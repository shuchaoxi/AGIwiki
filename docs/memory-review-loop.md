# Periodic memory review loop

Status: product contract for a future scheduler and writer. The current release provides the
read-only Critical Review Skill, content-free persisted proposals, append-only human decision
receipts, and explicit Adaptive Memory CLI, but does not schedule or apply review actions
automatically.

## Why review memory periodically

Long-lived memory decays even when its storage is healthy. Preferences change, task episodes lose
value, similar records accumulate, and a plausible technical idea can be mistaken for an
established fact. A review loop should reduce that drift without turning an Agent into an
unattended editor of the user's identity or knowledge.

AGIWiki keeps three responsibilities separate:

```text
source-grounded Artifact Memory     explicit profile / episode memory
              |                                  |
              +------------ read-only -----------+
                                   |
                         periodic review proposal
                                   |
                         human accept / reject
                                   |
                   explicit correct / forget operation
```

A prompt or behavioral policy is not factual memory. The opt-in
[`agiwiki-critical-review`](../skills/agiwiki-critical-review/SKILL.md) Skill controls how an Agent
reviews technical proposals. It is versioned as software and is not written into every profile
record or Memory Pack.

## Recommended cadence

Cadence is policy, not a hard-coded timer:

| Cadence | Work | Model required | May mutate memory |
| --- | --- | --- | --- |
| on every read | enforce scope, validity, expiry, digest, and Pack integrity | no | no |
| daily | report expired records, superseded heads, broken Packs, and due source checks | no | no |
| weekly | propose merges, corrections, retention changes, and technical-claim challenges | optional | no |
| monthly or before release | recheck important Source editions, Pack replacements, and unresolved conflicts | optional | no |

The implemented scheduler boundary is `adaptive review-due`. An external timer may call it with
one exact scope and `daily` or `weekly`; the command returns a closed content-free status and never
creates a proposal. Exit code zero means the status was computed, so automation must inspect
`due` and `recommended_actions`. Never place a capability file in an unattended timer command.

Daily model calls are usually wasteful for a personal ledger with few changes. Run a weekly
model-assisted review only when there are new or changed candidates. A user may choose another
cadence, but a missed schedule must never block normal reads.

## Review protocol

One review run follows this order:

1. Freeze the exact scope, review policy version, ledger or Pack digests, and cutoff time.
2. Select only records changed or due since the prior accepted review. Never widen user, agent,
   run, or workspace scope implicitly.
3. Run deterministic checks first: expiry, supersession, duplicate IDs, invalid digests, broken
   Packs, and Source edition drift.
4. If enabled, ask an Agent for proposals. A proposal must name its evidence, affected stable IDs,
   old digests, proposed replacement, confidence, and reason. It cannot claim that a model opinion
   is a fact.
5. Apply the Critical Review Skill to technical ideas and architecture claims. Check prior work,
   assumptions, the simplest baseline, failure modes, and a bounded falsification test.
6. Present a diff-like review to the user. Separate `keep`, `correct`, `merge`, `expire`, `forget`,
   `quarantine`, and `not_enough_information` decisions.
7. Apply only explicitly accepted operations through the canonical controller. Rejected and
   deferred proposals do not change memory.
8. Record a content-minimized receipt that binds the input snapshot, decision, operation IDs, and
   resulting digests without copying raw private memory into logs.

The model that writes a proposal should not silently approve its own proposal. For sensitive or
durable profile changes, human confirmation is mandatory.

## Technical-claim policy

Technical users benefit from skepticism, but automatic contrarian behavior is also harmful. The
reviewer must:

- challenge the claim rather than the person;
- separate facts, inferences, assumptions, and unknowns;
- compare with prior ideas using exact relationship labels rather than keyword overlap;
- compare with a credible baseline, including doing nothing;
- prefer the cheapest falsification test over a large implementation;
- preserve useful speculation as a hypothesis instead of promoting or deleting it as fact;
- mark unavailable evidence `NOT_CHECKED` rather than inventing a competitor, result, or market.

Use the Critical Review Skill for technical, product, research, and architecture decisions. Do not
force it onto emotional support, routine drafting, or casual conversation.

## Scheduling boundary

An external scheduler such as a user's Agent runtime, systemd timer, Windows Task Scheduler, or
Hermes scheduled task may eventually start a review. The scheduled action must remain a read-only
proposal until the user approves changes. AGIWiki does not need a resident daemon to define this
contract.

Hermes documents bounded persistent memory with optional approval for memory writes and a separate
scheduled-task facility. AGIWiki adopts the useful separation—bounded memory, scheduled review,
and approval—but does not copy Hermes's runtime or silently edit its files:

- [Hermes persistent memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)
- [Hermes scheduled tasks](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)

## Automation gates

The local ledger now implements request-bound operation idempotency for remember, correct, and
forget. This is necessary but not sufficient for unattended review.

Do not ship an unattended writer until all of these remaining gates are implemented and tested:

- capability delivery is isolated from the proposing Agent; schema v5 defines a separate manual
  `review_apply` permission, but current local enrollment still trusts the OS owner;
- dry-run output that cannot contain hidden mutations;
- anti-resurrection tests for expired, superseded, forgotten, and quarantined content;
- crash recovery and concurrent-review tests;
- prompt-injection tests against memories and source excerpts;
- bounded Token, time, and item budgets plus a kill switch;
- a review receipt that can be independently recomputed;
- an explicit rollback route for corrections, while preserving confirmed forget semantics.

Until those gates pass, users can enroll separate local propose/approve/apply capabilities, run
`adaptive review-plan`, persist it with `review-create`, and record a complete non-applying
decision with `review-decide`. A separately confirmed `review-apply` may atomically apply only a
still-current expiry deletion or exact-duplicate reduction. It refuses stale candidates and
content-generating corrections; those still require `adaptive correct` with explicit new content.
An external scheduler may invoke only `review-due` to generate reminders. It cannot create,
approve, or apply a review.

## Evaluation

Measure the review loop against no review and deterministic checks only:

- obsolete-memory recall rate;
- incorrect merge and incorrect deletion rate;
- duplicate reduction without semantic loss;
- technical claims promoted without sufficient evidence;
- user acceptance, rejection, and correction rates;
- cross-scope leakage;
- time, Token, and model cost per accepted useful change;
- remembered content that the user later says should never have been stored.

If model-assisted weekly review does not improve these outcomes over a deterministic report, keep
the deterministic report and remove the model step.

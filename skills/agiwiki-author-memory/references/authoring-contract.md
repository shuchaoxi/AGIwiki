# Authoring contract

AGIWiki uses closed JSON objects. Unknown fields fail validation. Treat the packaged JSON
Schemas and `agiwiki workspace validate` as authoritative.

## Workspace layout

```text
TARGET/
├── agiwiki.json
├── sources/
│   └── one-edition.json
└── entries/
    └── one-memory.json
```

`agiwiki workspace init` creates the manifest and the two empty directories. An empty
Workspace is intentionally incomplete until it has at least one Source and one Entry.

For a large source, `.agiwiki-author/` stores an immutable local plan and append-only
claims/results/amendments. This directory may contain a private local Source path and never enters a
Memory Pack. Use `agiwiki author status PLAN_ID --workspace TARGET` to inspect progress and
`agiwiki author next PLAN_ID --workspace TARGET` to resume the exact outstanding batch.
When the selected edition has a stable public address, pass its credential-free URL through
`author plan --canonical-uri URL`; v2 binds it into both the plan and Source. Legacy v1 plans
remain readable and imply a null canonical URI.

## Source

Required fields:

```json
{
  "contract_version": "agiwiki.source.v1",
  "source_id": "src_<32 lowercase hex>",
  "kind": "pdf|web|manual|code|note|other",
  "title": "Source title",
  "edition": "version/date/commit or null",
  "content_digest": "sha256:<64 lowercase hex>",
  "canonical_uri": "https://credential-free.example/page or null",
  "language": "zh-CN or null"
}
```

`content_digest` identifies the exact bytes or stable export used for authoring. When a
web page has no stable export, record the digest of the saved snapshot the agent actually
read. Never put a local path in these fields.

A prior summary or generated memory may be recorded as `kind: note`, but it is secondary
evidence. Its digest proves which note was read, not that the note's upstream claim is true.
Only register and cite the upstream edition when it was actually available for inspection.

## Entry envelope

Every Entry contains:

```json
{
  "contract_version": "agiwiki.entry.v1",
  "entry_id": "entry_<32 lowercase hex>",
  "kind": "fact|concept|procedure|troubleshooting",
  "title": "Short searchable title",
  "summary": "A self-contained retrieval summary that states the subject, condition, and conclusion.",
  "content": {},
  "keywords": ["specific term", "alternate term"],
  "applies_to": [],
  "relations": [],
  "source_refs": [
    {
      "source_id": "src_<32 lowercase hex>",
      "locator": {"type": "page", "value": "7"},
      "support_level": "direct"
    }
  ]
}
```

Allowed locator types are `page`, `section`, `url_fragment`, `line_range`,
`json_pointer`, `media_time`, and `note`. Allowed relations are `parent_of`, `child_of`,
`related_to`, `prerequisite_for`, `supersedes`, and `contradicts`.

For an Authoring Controller batch, `line_range` and `page` values use an exact positive decimal
position (`"42"`) or inclusive range (`"17-19"`). At least one reference to the planned Source
must fall wholly inside the claimed batch. Broad section names and ranges crossing a batch
boundary remain valid general Entry locators, but cannot be used to claim that planned batch.

## Kind-selection gate

Choose a kind from the reusable question that the cited material can answer, not from the topic's
importance and not from a desired distribution:

- `fact` answers which bounded rule, value, compatibility condition, or constraint applies. Keep
  every necessary qualifier. A normative requirement is normally a fact unless the source
  supplies a sequence that a caller can execute.
- `concept` answers what something is, how it works, or how it differs from nearby ideas. It needs
  a source-supported definition or model; it is not a catch-all for a collection of rules.
- `procedure` answers how to perform an action or follow a decision process and verify an
  observable result. The source must support the ordered actions or decisions, prerequisites,
  expected results, and verification. Declarative conditions, precedence rules, or descriptions
  of system behavior are not procedures by themselves.
- `troubleshooting` answers how to respond to a named observable failure. The source must support
  the symptom, at least one diagnostic signal or branch, a matching fix, and verification.

Every required content field must be supported by the cited locator or be a conservative
transformation entailed by it. Do not invent actions, expected results, checks, fixes, warnings,
or escalation to complete a richer shape. `indirect` identifies a supported transformation; it
does not authorize unsupported operational advice. If a candidate cannot pass this gate, select a
fitting `fact` or `concept` shape, or leave it in the Source/RAG layer. A batch or corpus may have
zero Entries of any kind. Never enforce or optimize for a kind quota.

## Content by kind

### Fact

```json
{
  "statement": "In product version 1.2, the named setting defaults to the documented value.",
  "qualifiers": [{"name": "version", "value": "1.2"}]
}
```

### Concept

```json
{
  "definition": "A reusable definition that distinguishes this concept from nearby terms.",
  "details": ["State one source-supported property that matters during later use."],
  "examples": [],
  "misconceptions": []
}
```

### Procedure

```json
{
  "goal": "Produce an observable result without modifying unrelated user data.",
  "prerequisites": [],
  "steps": [{
    "step_id": "step_first",
    "action": "Perform one bounded action using the exact documented option.",
    "expected_result": "The named output appears and the previous input remains available.",
    "verification": "Read the output again and compare the documented fields.",
    "failure_guidance": [],
    "warnings": []
  }],
  "verification": ["Repeat the documented read-only check and confirm the expected state."],
  "failure_guidance": [],
  "warnings": []
}
```

### Troubleshooting

```json
{
  "symptoms": ["The documented command fails with the named observable error."],
  "prerequisites": [],
  "diagnostic_steps": [{
    "step_id": "diag_first",
    "check": "Run the documented read-only diagnostic and record only the necessary fields.",
    "expected_signal": "The result distinguishes the documented failure states.",
    "branches": [{
      "when": "The documented failure signal is present",
      "guidance": "Apply only the matching bounded fix and then verify again."
    }],
    "warnings": []
  }],
  "fixes": [{
    "fix_id": "fix_first",
    "action": "Apply the smallest source-supported change to a disposable copy.",
    "verification": "Repeat the original failing operation and check the expected result.",
    "failure_guidance": [],
    "warnings": []
  }],
  "escalation": [],
  "warnings": []
}
```

## Selection rules

Keep an Entry only when it is likely to be reused, supplies a precise citation, resolves a
concept, captures a version-sensitive operation, or prevents an expensive failure. Leave
ordinary prose in the original source/RAG layer. Prefer several independently supportable
Entries over one broad summary that mixes facts and inference.

Before intentionally releasing a changed collection, update the Workspace `version` to a
meaningful new value. Keep existing `source_id` and `entry_id` values stable for the same
logical Source and Entry; changed canonical content automatically receives new immutable
digests, `entry_version_id`, and `pack_id` values.

## Planned batch result

The `agiwiki.author-batch-result.v1` shape submitted to `author record` names the exact Entry IDs.
After Workspace validation, the Controller stores `agiwiki.author-batch-result.v2`, replacing that
list with sorted `entry_bindings` containing each ID and its canonical normalized Entry digest.
The stored result is a control receipt, not an Entry. A completed result needs at least one binding;
a skipped result must have none. `measurement_source`
is `provider`, `agent`, or `unavailable`. Only the last value forces both Token counts to zero.
The controller rejects results without a prior claim, results for another plan, duplicate Entry
attribution, missing Workspace Entries, or Entries that do not cite a locator inside the claimed
Source batch. The `result_seed` returned by `author next` supplies the three identity fields; it
does not imply success or provide Token measurements.

## Recorded Entry amendment

A recorded Entry is content-bound and must not be edited directly. `author amend` accepts one
complete staged Entry plus an explicit expected old digest and idempotency operation ID. The
replacement must preserve `entry_id`, remain owned by the original completed batch, pass full
Workspace structure and quality checks, and retain at least one exact locator inside that batch.
The caller cannot supply a replacement batch ID, add an Entry, or delete one.

The append-only `agiwiki.author-amendment.v1` receipt binds `base_result_digest`, `entry_id`,
sequence, predecessor amendment, old/new Entry digests, and an `old_digest_basis`. Its basis is
`recorded_result` for the first v2 transition, `prior_amendment` later, or
`operator_asserted_legacy` for a first transition from a v1 result. The last form is a present-time
operator assertion and is not evidence of the Entry bytes at the earlier v1 record event.

The receipt is persisted before atomic Workspace replacement. A retry with the same operation and
replacement completes an interrupted old→new transition or reports an already-applied replay;
different content under the same operation fails closed.

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
  "summary": "Self-contained retrieval summary",
  "content": {},
  "keywords": [],
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

## Content by kind

### Fact

```json
{"statement": "One bounded assertion.", "qualifiers": [{"name": "version", "value": "1.2"}]}
```

### Concept

```json
{
  "definition": "Reusable definition.",
  "details": [],
  "examples": [],
  "misconceptions": []
}
```

### Procedure

```json
{
  "goal": "Observable goal.",
  "prerequisites": [],
  "steps": [{
    "step_id": "step_first",
    "action": "One action.",
    "expected_result": "Observable result.",
    "verification": "How to check it.",
    "failure_guidance": [],
    "warnings": []
  }],
  "verification": ["End-to-end success check."],
  "failure_guidance": [],
  "warnings": []
}
```

### Troubleshooting

```json
{
  "symptoms": ["Observable symptom."],
  "prerequisites": [],
  "diagnostic_steps": [{
    "step_id": "diag_first",
    "check": "Read-only diagnostic.",
    "expected_signal": "Expected evidence.",
    "branches": [{"when": "Signal condition", "guidance": "Next action"}],
    "warnings": []
  }],
  "fixes": [{
    "fix_id": "fix_first",
    "action": "Bounded fix.",
    "verification": "How to prove the fix worked.",
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

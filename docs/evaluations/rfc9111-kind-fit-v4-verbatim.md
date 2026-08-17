# RFC 9111 prompt-v4 verbatim kind-fit regression

Date: 2026-08-16
Status: PASS for this seven-case internal regression

This regression checks whether the `agiwiki-author-memory.v4` instructions select a defensible
Entry kind, preserve required source qualifiers, and skip material that is not independently
useful. It is not a whole-document quality result and does not test retrieval or answer quality.

## Method

Seven RFC 9111 fragments were extracted as byte-exact concatenations of frozen inclusive line
ranges. Before authoring, a separate source-auditor role checked the saved source digest, line
ranges, fragment digests, byte equality, and PDF page mapping. The candidate count was zero when
the inputs and a private kind key were frozen.

The author role received the fragments and prompt-v4 contract but not the private key. It emitted
six Entries and skipped one fragment. The author process was interrupted after writing the
candidate artifact. That artifact was preserved byte-for-byte; the orchestrator subsequently
checked JSON syntax and ran the packaged `normalize_entry` contract over all six emitted Entries.
Only then was the private key opened for source comparison. No candidate content was repaired
after the key became available.

The candidate artifact digest was:

```text
sha256:e533cc97fd2e636bd89ea13210bff96ed812209abf5cdab0434a66b0d47dff14
```

The review artifact digest was:

```text
sha256:738e17d3f6dbc7809b5e0d90388ececf572c5db9f558ce83cb1d05ce18625c20
```

## Result

| Metric | Result |
| --- | ---: |
| Cases | 7 |
| Correct emit/skip decisions | 7/7 |
| Accepted kinds for emitted Entries | 6/6 |
| Forbidden kinds | 0 |
| Correct skips | 1/1 |
| Unsupported required fields | 0 |
| Support-level mismatches | 0 |

The emitted distribution was one Fact, two Concepts, three Procedures, and zero Troubleshooting
Entries. Kind balance was not a target. The isolated index fragment was correctly skipped. The
three Procedures used `indirect` support, while directly stated definitions and rules used
`direct` support.

## Interpretation boundary

This result closes a defect in an earlier study whose fragments were compressed paraphrases rather
than the claimed whitespace-normalized source. It shows that prompt v4 passed the tested kind,
skip, required-field, and support-level gates when given byte-faithful evidence.

It does **not** establish:

- general factual accuracy;
- whole-book authoring quality;
- behavior across model providers;
- independent human validation;
- superiority over RAG or another memory system.

The source auditor, author, and reviewer were role-separated Agents in a shared environment. The
private key was withheld by process and file permissions, not by a separate security principal.

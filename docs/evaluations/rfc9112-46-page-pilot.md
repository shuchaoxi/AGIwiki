# RFC 9112 46-page authoring pilot

Date: 2026-08-15

This is a bounded product-quality pilot, not a benchmark claim. It tests whether the current
Authoring Controller can turn one real 20–100 page document into useful Artifact Memory without
adding a cloud service, knowledge graph, or more MCP tools.

## Source and rights boundary

- Source: [RFC 9112: HTTP/1.1](https://www.rfc-editor.org/info/rfc9112/), Internet Standard,
  June 2022.
- The RFC Editor currently records that RFC 9112 was updated by RFC 9931. The Pack therefore
  identifies the exact June 2022 edition rather than claiming to be an automatically current HTTP
  reference.
- PDF length: 46 pages; PDF SHA-256:
  `b260bba790c2da55a7e0795f356fcd9b70743686c55250f0ef1cc993c4d1abac`.
- PDF size: 649,012 bytes; mechanically extracted text: 117,122 bytes.
- RFC availability does not make every RFC unrestricted public-domain material. The
  [RFC Editor usage guidance](https://www.rfc-editor.org/series/rfc-use/) and the document's IETF
  Trust notice govern reuse. The repository does not redistribute this pilot's PDF or extracted
  text; the pilot Entries are concise original paraphrases with page locators.

## Frozen plan

| Item | Result |
|---|---:|
| Plan | `authorplan_4cfc22be0d1bebb747dab40b8cd4afe4` |
| Unit | PDF page |
| Pages | 46 |
| Batch size | 8 pages |
| Batches | 6 completed / 6 planned |
| Estimated input budget | 41,400 Tokens |
| Configured budget | 50,000 Tokens |
| Recorded provider Tokens | unavailable |
| Entries | 12 |

`measurement_source=unavailable` is intentional: this run did not receive a trustworthy provider
Token receipt, so the controller recorded zero measured Tokens rather than inventing usage.

Every Entry cites a numeric PDF page or page range inside its claimed batch. After all six results,
`author status` reported `source_ok=true`, `recorded_entries_ok=true`, and 10,000 basis points of
progress.

## Resulting memory

| Kind | Count | Purpose in this pilot |
|---|---:|---|
| `concept` | 5 | Message structure, target forms, framing conflict, security boundary, migration changes |
| `procedure` | 3 | Body-length precedence, persistent reuse, graceful connection close |
| `troubleshooting` | 4 | Octet parsing, invalid whitespace, incomplete messages, desynchronization |

The 12 editable Entry JSON files total 30,213 bytes, averaging about 2.5 KB each. The complete
closed Memory Pack is 36,054 bytes. That is about 31% of the extracted text size and 5.6% of the
PDF size, while preserving prerequisites, warnings, verification, failure guidance, and page
locators for operational Entries.

The reviewed Pack is:

- `pack_id=pack_ab574d1f20f1b58fa256a76b6abed25c`
- `manifest_digest=sha256:5fe79f0e38c7b953ff570ddc6275dc06594419dcd247363fee9109c515e1e93b`
- 12 Entries and one Source; build, verify, install, activate, find, and exact get all passed.

Five concept Entries use `support_level=direct`. The seven procedures/troubleshooting Entries use
`indirect` because they combine source-grounded protocol rules with generated test, failure, and
operational guidance. Calling those whole transformed Entries “direct quotations” or wholly direct
support would overstate provenance.

## Retrieval task bank

Twelve in-scope questions were mapped in advance to one expected Entry, including:

- parsing HTTP as octets rather than Unicode;
- the four request-target forms;
- Transfer-Encoding and Content-Length conflicts;
- message-body-length precedence;
- incomplete-message detection;
- persistent connection reuse and graceful close;
- response splitting and request smuggling.

All 12 expected Entries ranked first (`12/12` top-1). Five deliberately out-of-scope questions
covered HTTP/2 HPACK, SameSite cookies, CORS, WebSocket masking, and cache freshness.

The first run exposed a precision defect: all five out-of-scope questions returned an unrelated
HTTP/1.1 Entry because fallback search accepted one generic term. The implementation was changed
so a fallback candidate must cover a majority of the informative query terms. After the change:

- in-scope top-1: `12/12`;
- out-of-scope correct no-match: `5/5`;
- Pack identity and Entry content were unchanged by the search-only fix.

These numbers are regression evidence for this small corpus, not a general retrieval-accuracy
claim. The questions were written by the same author who prepared the Entries and were not a
blinded external set.

The exact 17-case task bank is stored in
[`rfc9112-retrieval-task-bank.json`](rfc9112-retrieval-task-bank.json). When the exact reviewed
Pack is available locally, replay it without copying the Pack into this repository:

```bash
PYTHONPATH=src ./.venv/bin/python tools/evaluate_retrieval.py \
  /path/to/rfc9112-reviewed.memory-pack \
  docs/evaluations/rfc9112-retrieval-task-bank.json
```

The report binds both the task-bank digest and Pack manifest digest and emits case IDs rather than
raw queries. This makes the local regression repeatable; it does not turn the author-written task
bank into an independent or blinded benchmark.

## Deterministic Fragment baseline

A second tool now replays the same questions against the mechanically extracted PDF text, split at
its 46 form-feed page boundaries. The evidence map is frozen in
[`rfc9112-fragment-evidence.json`](rfc9112-fragment-evidence.json). The baseline is deliberately
described as lexical page retrieval, not as a strong semantic RAG system and not as answer-quality
evidence.

```bash
pdftotext -layout /path/to/rfc9112.pdf /tmp/rfc9112.txt
PYTHONPATH=src ./.venv/bin/python tools/evaluate_fragment_retrieval.py \
  /tmp/rfc9112.txt \
  docs/evaluations/rfc9112-retrieval-task-bank.json \
  docs/evaluations/rfc9112-fragment-evidence.json --top-k 5
```

On Poppler `pdftotext` 24.02.0, bound to the recorded extracted-text digest, the baseline achieved:

- evidence-page recall within top 5: `11/12`;
- out-of-scope no-match: `4/5`;
- median retrieved page payload across answerable questions: 14,006 characters.

The Pack retrieval result remained `12/12` top-1 and `5/5` no-match, with a median exact Entry
payload of 2,754.5 characters across answerable questions. That payload is about 5.1 times smaller
than the lexical five-page
baseline, but it is not a measured provider Token saving: tool envelopes, prompts, answer
generation, authoring cost, and the stronger semantic/embedding baseline are not included. The
task bank was also written after the Entries by the same author, so this is a diagnostic result,
not a fair causal product comparison.

## Accuracy review

The author re-read the cited PDF pages after generation and checked the following dimensions:

1. the central claim is present in the cited range;
2. RFC qualifiers and precedence ordering are preserved;
3. a concept is not presented as executable procedure without prerequisites;
4. generated operational advice is not mislabeled as directly quoted material;
5. procedures and troubleshooting records retain verification, warnings, and failure boundaries;
6. unrelated HTTP topics produce no-match rather than a synthetic answer.

All 12 Entries passed this author self-review. This is not independent subject-matter review. In
particular, security-sensitive HTTP implementations should still use the exact current standard,
errata, and implementation tests rather than treating this pilot Pack as a conformance oracle.

## Product findings

The pilot supports the product thesis in a narrow form: a bounded operational Pack can reduce a
46-page document to a small, reusable, exact-addressed memory while keeping enough structure for
an Agent to act cautiously. It does not show that every book should be converted or that this is
better than RAG for one-off factual lookup.

The pilot uncovered a source-navigation gap: its v1 Authoring Plan could not bind the known
official URL, so this frozen pilot Pack retains `canonical_uri=null`. Author-plan v2 was added
afterward with `--canonical-uri`, credential checks, immutable-plan binding, and v1 read
compatibility. The old pilot artifact was not silently rewritten.

Two follow-ups are more valuable than adding new memory kinds:

1. Repeat the task bank with a second person or Agent that did not author the Entries, plus a plain
   chunk-RAG baseline. Measure unsupported-claim rate, citation correctness, task success, latency,
   and real provider Token usage.
2. Run a second real-source plan under v2 so the Pack exposes a verified clickable Source URI from
   the beginning rather than retrofitting it after authoring.

Until those are done, the honest claim is “validated local authoring and retrieval on one real
46-page standards document,” not “proven universal document compression.”

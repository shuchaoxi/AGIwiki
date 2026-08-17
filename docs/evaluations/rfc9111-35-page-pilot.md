# RFC 9111 35-page consumer pilot

Status: internal Agent-only dogfood; not independent human validation.

This pilot used the official [RFC 9111 HTML](https://www.rfc-editor.org/rfc/rfc9111.html)
and [PDF](https://www.rfc-editor.org/rfc/rfc9111.pdf). The 20 prompts and private scoring
key were frozen while the Workspace contained zero Entries. Authoring and answer Agents did
not receive the scoring key.

## Frozen source and output

- PDF: 35 pages, 566,083 bytes,
  `sha256:2663227a94ec8a81892a02f5697fc15c578571cdee96407adb5c60a60236c76f`.
- Author plan: 7 batches of 5 pages; all 7 completed.
- Workspace: 34 Entries and 1 Source.
- Entry size: average 2,275.5 characters; range 1,491–3,152 characters; average summary
  291.4 characters.
- Kind distribution: 33 concept, 1 fact, 0 procedure, 0 troubleshooting.
- Pack: `pack_5019ff7a899289a181da62ca2526d278`, 101,098 bytes, verified.

The Pack is not smaller than the 89,268-byte extracted text. Its intended economy is narrower
query-time context and reusable structure, not whole-book byte compression.

## Source review

An Agent that did not author the Entries checked every Entry against its bounded source pages.
The initial result was 30 PASS, 4 REVISE, and 0 REJECT. All four revisions used the append-only
`author amend` flow and passed a second review. The defects were normative overstatements that
JSON Schema could not detect.

The run exposed the earlier v1 batch-result content-binding gap. After the controller fix, 26
Entries were sealed by v2 results, one legacy Entry was explicitly bridged by an amendment, and
seven old Entries remain visibly `legacy_unsealed`. They are not represented as digest-bound.

## Frozen-task answer comparison

Two isolated Agents answered the same 20 frozen tasks. One could query only the Memory Pack;
the other could search only the extracted RFC text. Neither had the scoring key. A third Agent
scored the answers against 70 required points and forbidden-overclaim rules.

| Metric | Memory Pack | Source retrieval |
| --- | ---: | ---: |
| PASS / PARTIAL / FAIL | 16 / 4 / 0 | 16 / 4 / 0 |
| Required points | 65/70 (92.9%) | 66/70 (94.3%) |
| Forbidden overclaims | 0 | 0 |
| Correct refusal on the out-of-scope task | 1/1 | 1/1 |
| Citation locator compatible with the key | 19/20 (95%) | 18/20 (90%) |

A separate deterministic diagnostic produced 11/19 exact top-1 Entry identity hits for the
Pack and 15/19 evidence-page hits at top 5 for lexical page retrieval. These are retrieval
diagnostics, not answer accuracy: a non-designated Entry can still support the right answer,
and a related candidate can still lead to a correct refusal.

Median positive candidate context was 2,781 characters for one Pack Entry and 12,120
characters for lexical top-5 pages, a 77.1% reduction. No provider-grade token, latency, or
price measurement was available, so this is not a token-savings claim.

## Decision and limitations

The vertical slice passes: frozen questions, bounded authoring, independent source review,
controlled amendment, immutable Pack, retrieval, answering, and key-based scoring all worked.
The result does not establish superiority over strong semantic RAG or consumer adoption.

Material limitations:

- all participants were Agents, not independent human consumers;
- the maintainer designed and orchestrated the task freeze;
- the outcome reviewer could see arm labels and infer answer origin;
- the source arm used direct lexical navigation rather than a fixed embedding and reranking
  baseline;
- provider token, latency, monetary cost, repeated-session consistency, and two-week retention
  were not measured;
- the 33/1/0/0 kind distribution does not prove that this source should contain every kind, and
  zero troubleshooting Entries may be appropriate; it exposes a `concept` catch-all risk and
  leaves non-concept kind selection unvalidated.

The next evaluation should therefore score kind fitness under prompt set v4 without rewarding a
balanced distribution, then add a fixed strong-RAG baseline, provider usage receipts, and first-use
testing by people who did not build AGIWiki. It should not rewrite this historical Pack to improve
its distribution, or add cloud, graph, or more MCP tools first.

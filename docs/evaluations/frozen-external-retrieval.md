# Frozen external retrieval replay

This repository tool evaluates contexts that were already produced by an external retrieval
system. It does not install an embedding model, build a vector index, call a provider, or add a
RAG runtime to AGIWiki. The contract and evaluator belong to research tooling, not to the
Workspace, Memory Pack, CLI, or MCP product interfaces.

The useful separation is:

```text
external retrieval system                 AGIWiki repository tool
index + retrieve + optionally rerank  ->  validate frozen run + replay metrics
```

This makes the measurement provider-neutral while keeping AGIWiki core CPU-only and
network-free. It reproduces the report from one frozen artifact; it does **not** prove that an
external service actually ran as declared or make that service independently reproducible.

## Inputs and binding

`tools/evaluate_frozen_retrieval.py` accepts four required files and one optional receipt:

1. the exact UTF-8 source-text snapshot used by the experiment;
2. an `agiwiki.retrieval-eval-task-bank.v1` task bank;
3. its `agiwiki.fragment-evidence.v1` page-evidence manifest;
4. an `agiwiki.frozen-retrieval-run.v1` external run;
5. optionally, one `agiwiki.evaluation-usage-receipt.v1` usage receipt.

The run binds the canonical task-bank digest, original source digest, extracted source-text
digest, retrieval configuration digest, and corpus snapshot digest. It must contain every task
exactly once. Each task stores only the query digest, a `match`, `no_match`, or `error` decision,
and the contexts actually delivered to an answerer.

Each delivered context contains:

- a contiguous rank starting at one;
- a bounded context ID and exact text;
- the SHA-256 digest of the text's exact UTF-8 bytes;
- ordered, disjoint source PDF page ranges supplied by the retrieval adapter.

The adapter is responsible for deriving page ranges from indexed corpus metadata, not by asking
the answer model to guess them. The evaluator checks their bounds and uses overlap with the
frozen evidence map. It cannot independently prove that normalized external context text came
from the claimed pages, so locator provenance remains self-attested.

All contract objects are closed. Unknown or duplicate fields, missing cases, changed queries,
bad digests, non-contiguous ranks, excess contexts, oversized inputs, and inconsistent
`no_match`/`error` decisions fail closed.

## Frozen run example

Digests below are illustrative placeholders:

```json
{
  "contract_version": "agiwiki.frozen-retrieval-run.v1",
  "task_bank_id": "manual-eval-01",
  "task_bank_digest": "sha256:<64 lowercase hex characters>",
  "source_digest": "sha256:<64 lowercase hex characters>",
  "source_text_digest": "sha256:<64 lowercase hex characters>",
  "retriever": {
    "system": "local-hybrid-retriever",
    "version": "1.0.0",
    "retrieval_family": "hybrid",
    "embedding_model": "embedding-model-version",
    "reranker_model": "reranker-model-version",
    "chunking_id": "400-token-overlap-40-v1",
    "configuration_digest": "sha256:<64 lowercase hex characters>",
    "corpus_snapshot_digest": "sha256:<64 lowercase hex characters>"
  },
  "declared_top_k": 5,
  "cases": [
    {
      "case_id": "case-001",
      "query_digest": "sha256:<64 lowercase hex characters>",
      "decision": "match",
      "contexts": [
        {
          "rank": 1,
          "context_id": "chunk-0042",
          "text": "The exact context delivered by the external retriever.",
          "text_digest": "sha256:<64 lowercase hex characters>",
          "source_page_ranges": [[7, 8]]
        }
      ]
    }
  ]
}
```

`query_digest` uses AGIWiki's canonical JSON digest of the query string. `text_digest` differs
deliberately: it is SHA-256 over the context's exact UTF-8 bytes, without JSON string quoting.

The run file is bounded to 32 MiB, each context to 512 Ki characters, each case to 2 Mi
characters, each page-range list to 32 ranges, and `declared_top_k` to 20. These are safety and
audit bounds, not recommended retrieval settings.

## Usage receipt

The optional receipt is an aggregate measurement for exactly one retrieval-run digest:

```json
{
  "contract_version": "agiwiki.evaluation-usage-receipt.v1",
  "retrieval_run_digest": "sha256:<64 lowercase hex characters>",
  "scope": "retrieval_only",
  "measurement_source": "provider_reported",
  "request_count": 20,
  "input_tokens": 12345,
  "output_tokens": 0,
  "cached_input_tokens": 0,
  "latency_ms": 8421,
  "cost": {"currency": "USD", "amount_decimal": "0.0123"},
  "provider_receipt_digest": "sha256:<64 lowercase hex characters>"
}
```

Scopes are `retrieval_only`, `answering_only`, and `end_to_end`. They must not be added together
because scopes may overlap. Monetary values are non-negative decimal strings rather than binary
floating-point numbers.

`provider_reported` requires a digest of the retained private provider receipt.
`client_metered` permits that digest to be absent. When measurement is unavailable, use
`measurement_source: "unavailable"` and `null` for every measurement and provider receipt digest.
Zero means a measured zero and must not stand in for missing data. Raw provider output, session
IDs, credentials, and request URLs do not belong in this receipt.

## Replay

From the repository checkout:

```bash
PYTHONPATH=src /path/to/python tools/evaluate_frozen_retrieval.py \
  /path/to/source.txt \
  /path/to/task-bank.json \
  /path/to/evidence.json \
  /path/to/frozen-run.json \
  --usage /path/to/usage.json
```

A valid run exits zero even when recall is low. Exit code 2 means the inputs failed contract or
cross-file validation. This distinction prevents a poor experimental result from being hidden as
an infrastructure failure.

The JSON report includes evidence recall at the declared top-k, first-hit reciprocal rank,
explicit negative no-match count, retrieval error count, and delivered context character counts.
Per-case output contains IDs and measurements only; it does not repeat queries or context text.

## Claim boundary

The report always labels itself:

```text
frozen_external_context_replay_only_not_answer_quality_or_verified_provider_execution
```

It is a retrieval diagnostic, not answer accuracy, task success, provider execution proof, or a
Token/cost saving claim. Character counts are not Tokens.

Calling an arm a **strong RAG baseline** requires a documented experimental qualification outside
the evaluator:

- questions and scoring evidence were frozen before running either arm;
- both arms used the exact same source edition and questions;
- the external arm used a fixed dense or hybrid retriever, exact embedding model identifier,
  chunking/corpus snapshot, and a fixed reranker;
- top-k and configuration were frozen before seeing the scoring key;
- answer-quality comparison used the same answer model, prompt, budget, and isolated sessions;
- provider or client usage was measured rather than inferred;
- failures and missing measurements remained in the denominator.

Without those conditions, describe the artifact as a frozen external retrieval run or semantic
RAG candidate. To compare final answers, prepare a separate answer input containing only each
question and its frozen delivered contexts; never give the answer Agent the task bank's expected
Entry IDs or the private evidence/scoring key.

The current v1 task bank remains Pack-oriented because it includes `pack_id` and
`expected_entry_id`. The external evaluator ignores those identities for scoring but binds the
entire task-bank digest. A future benchmark contract should split neutral questions from
arm-specific Pack and source scoring keys instead of silently changing v1.

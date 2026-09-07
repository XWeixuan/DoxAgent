# W1/W2 Responses Cache Tuning Report

Date: 2026-09-06  
Model: `qwen3.8-flash`  
Reasoning effort: `medium`  
Transport: OpenAI-compatible Responses API with strict JSON Schema

## Outcome

The best production layout keeps the Responses API, disables the
`x-dashscope-session-cache` header, and uses the model's default implicit cache.
Every round serializes reusable business reference data as one deterministic,
read-only prefix before the dynamic Case payload. The transport records a local
SHA-256 `prefix_fingerprint` on every successful turn.

No W1/W2 business Prompt or output schema was changed. `previous_response_id`
remains in local audit lineage but is no longer sent to the provider: W1-R2,
W2-R2, and W1-R3 already receive all required prior-round business context in
their direct payloads, while remote conversation inheritance made the cache
prefix depend on a stochastic prior response.

## Why Session Cache was rejected

The original evaluation had Session Cache enabled. A same-Case control confirmed
that the header worked, but only for exact independent requests:

| Control | Input | Cached | Share |
|---|---:|---:|---:|
| First full Case | 36,241 | 0 | 0.00% |
| Immediate identical full Case | 36,687 | 20,607 | 56.17% |

In the second Case, W1-R1 hit 13,460/13,466 and W2-R1 hit 7,147/7,153, while
W1-R2 remained 0 because its new `previous_response_id` changed the inherited
conversation. Three W2-R1 payload layouts with different messages all remained
at zero while the Session Cache header was enabled:

| Layout | Input | Cached | Share |
|---|---:|---:|---:|
| Current single JSON input | 21,302 | 0 | 0.00% |
| Reference in instructions | 21,419 | 0 | 0.00% |
| Reference and message as separate input blocks | 21,350 | 0 | 0.00% |

An exact-repeat control for the second layout hit 7,125/7,131 on both warm
requests. This isolates the problem to cross-Case prefix reuse rather than a
missing or ineffective header.

## Winning implicit-cache layout

The input is now serialized as:

```text
# Read-Only Business Reference Data
<deterministically serialized Event/Policy business data>

# Current Case Input
<deterministically serialized dynamic Case data>
```

The reference block is data, not an instruction. Audit/version/protocol metadata
is not reintroduced into the model payload.

| Round / experiment | Input | Cached | Share | Warm-call behavior |
|---|---:|---:|---:|---|
| W1-R1, 5 different messages | 68,571 | 55,808 | 81.39% | 12,288 cached each on calls 2-5 |
| W2-R1, 5 different messages | 35,864 | 31,744 | 88.51% | 6,144-7,168 cached each |
| W1-R2, fixed E14 Detail, 3 messages | 8,309 | 5,760 | 69.32% | 2,048 cached on calls 2-3 |
| W2-R2, fixed Policy Detail, 3 messages | 8,072 | 3,328 | 41.23% | 1,664 cached on calls 2-3 |
| W1-R3, same message, 3 calls | 7,134 | 5,120 | 71.77% | 2,048 cached on calls 2-3 |

Warm-only W2-R2 cache share was 63.46%; warm-only W1-R3 share was 86.12%.
All repeated-round prefix fingerprints were identical.

## Final production Case

`MU-W12-018` completed through W1-R1, W1-R2, W2-R1, and W1-R3 with strict
outputs and no retry. It used 25,413 input tokens and returned 10,368 cached
tokens (40.80%):

| Round | Input | Cached | Share |
|---|---:|---:|---:|
| W1-R1 | 13,458 | 6,656 | 49.46% |
| W1-R2 | 2,381 | 1,664 | 69.89% |
| W2-R1 | 7,145 | 0 | 0.00% |
| W1-R3 | 2,429 | 2,048 | 84.31% |

An immediate no-lineage repeat of the same complete Case returned 23,168 cached
tokens from 26,970 input tokens (85.90%).

The one W2-R1 miss is consistent with the provider's documented caveat that
implicit-cache hits are not deterministic. In the immediately repeated W2-R1
batch, warm calls consistently hit 6,144-7,168 tokens.

This cache acceptance is not a new full semantic release approval. In the final
018 runs, the current W1-R3 Prompt/model produced 10-12 atomic candidates versus
the fixture's expected 3-6. An A/B control that restored remote R2 response
inheritance still produced 10, so the issue is independent of the cache layout
and should be handled as a separate Prompt-quality regression.

The Responses usage object did not expose `cache_creation_input_tokens` in any
test; this field is therefore recorded as unavailable rather than inferred.

## Evidence

- `run_session_cache_ab.py`: payload and endpoint AB harness.
- `run_round_cache_acceptance.py`: W1-R2/W2-R2/W1-R3 real-round harness.
- `cache_ab/*.json`: per-request usage, latency, and prefix fingerprints.
- `runs/mu-w1w2-implicit-cache-final-case-20260906`: final full-Case artifact.
- Aliyun Context Cache guide: <https://help.aliyun.com/zh/model-studio/context-cache>
- Aliyun Responses API / Session Cache guide:
  <https://help.aliyun.com/zh/model-studio/compatibility-with-openai-responses-api>

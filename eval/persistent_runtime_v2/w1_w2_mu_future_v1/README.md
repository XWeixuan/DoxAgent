# MU W1/W2 Future Corpus V1

This corpus contains 25 fictional future messages for real-model evaluation of the current Persistent Runtime V2 W1/W2 prompts. The messages are grounded in the pinned MU Event Library and D3 PolicySet listed in `manifest.json`; they are not claims about events that actually occurred.

## Files

- `messages.jsonl`: model-facing message fixtures in chronological order. Only `source_message.ticker`, `source_message.title`, and `source_message.body` belong in the LLM business payload.
- `gold.jsonl`: round-level W1/W2 expectations, semantic scoring notes, expected R3 execution, and diagnostic route expectations.
- `case_matrix.md`: frozen human-readable coverage design.
- `manifest.json`: source hashes, prompt hashes, corpus contract, and expected distribution.
- `validate_dataset.py`: deterministic integrity and coverage checks.

Join `messages.jsonl` and `gold.jsonl` by `case_id`. Do not inject the Gold object, case metadata, source kind, genre, dependency metadata, or manifest data into the model prompt.

## Evaluation profiles

The canonical profile is `w1_w2_round_isolated`: run every case against the pinned Event index/details and PolicySet with a fresh Policy-activation ledger. This is the cleanest prompt-quality comparison and is the profile to use for aggregate W1/W2 metrics.

Two OLD cases require same-day provisional state:

- Run `MU-W12-013` before `MU-W12-024`, then substitute the actual provisional `E#` allocated from 013 wherever the Gold specifies `provisional_from_case`.
- Run `MU-W12-018` before `MU-W12-019`, then substitute the actual provisional `E#` allocated from 018.

The files are already sorted by `published_at` and then `case_id`, so both dependencies are in the correct order.

For `ordered_provisional_replay`, seed those allocated provisional facts but evaluate W1/W2 independently of Policy activation consumption. A literal full-Runtime sequential replay differs for case 019 because case 018 consumes the same Policy activation revision; after that, 019 is intentionally presented a projection with the consumed Policy removed. `manifest.json` records this distinction so the isolated W2 Gold is not incorrectly applied to that end-to-end state.

## Gold interpretation

- `must_include_event_ids` is a recall requirement; extra R1 IDs are acceptable only when they are genuine candidates and the output stays within the five-ID contract.
- `reference_expectation.must_include` identifies the minimal decisive references. `may_include` lists defensible extras, and the W1 R2 contract still caps the final list at three.
- `matched_condition_ids` is evaluated after the Runtime's advisory-attribution sanitizer. Policy order is significant only where explicitly marked.
- `near_miss_policy_ids` records the principal false-positive trap. These IDs must not appear in the final W2 output unless a case explicitly promotes one through R1 low confidence and R2 adjudication.
- R3 candidates are scored semantically. Proposition wording need not match verbatim, but all required atomic facts must be preserved without combining unrelated assertions or adding investment interpretation.
- `diagnostic_route` assumes a fresh activation ledger. It is included to expose W1/W2 integration consequences, not to turn this corpus into a W3-quality evaluation.

## Coverage

The corpus contains 13 NEW and 12 OLD messages; 20 news articles, four company releases, and one government notice. Four cases end with non-empty Policy matches. It covers ordinary hit/no-hit decisions, R1-low to R2-hit, R1-low to R2-no-hit, R2-still-low, W1-low, multi-Policy ordering, Reference View versus full Event-index recall, provisional duplicate detection, and the two R3 execution cases. No fixture is an earnings, quarterly-results, earnings-preview, or financial-guidance story.

All bodies are at least 500 whitespace-delimited English words. Length is a floor, not a target; the set deliberately includes wire-style follow-ups, trade features, a long market roundup, low-relevance aggregation, official releases, and a regulator notice.

## Validation

From the repository root:

```powershell
python .\eval\persistent_runtime_v2\w1_w2_mu_future_v1\validate_dataset.py
```

The validator checks IDs, chronology, minimum length, source distribution, novelty and Policy counts, continuation shapes, provisional dependencies, frozen-input hashes, and artifact hashes.

# MU W1/W2 Real Evaluation — Provider Blocker

## Outcome

The real evaluation is blocked before semantic scoring. One isolated Case
(`MU-W12-001`) was submitted through the production Persistent Runtime V2 W1/W2
path with Bailian `qwen3.8-flash`, reasoning effort `medium`, strict JSON Schema,
and the pinned Event Library and PolicySet. Both parallel first-round calls were
rejected by Bailian, so no W1/W2 model output exists and precision/recall cannot
be computed.

## Verified configuration

- Effective API key equals the user-specified key (the secret is not reproduced here).
- China Responses endpoint: HTTP 400, provider code `Arrearage`.
- China Chat Completions endpoint: HTTP 400, provider code `Arrearage`.
- International Responses endpoint: HTTP 401, provider code `InvalidApiKey`.
- International Chat Completions endpoint: HTTP 401, provider code `invalid_api_key`.

The matching China-endpoint failure across both protocols rules out the W1/W2
prompt, strict-schema contract, Responses adapter, and model routing as causes.
The international checks show that changing region is not a valid workaround.

## Probe evidence

- Runtime Cases attempted: 1
- Runtime Cases reaching terminal `COMPLETED`: 1 (degraded runtime completion)
- Semantically scorable Cases: 0
- Provider turns: 2 (`W1/R1`, `W2/R1`)
- Retry turns: 0; `Arrearage` is correctly classified as non-retryable
- Input/output/reasoning tokens billed: 0/0/0
- Aggregate provider latency: 1,352 ms

See `cases/MU-W12-001.json`, `predictions.jsonl`, `metrics.json`, and
`run_manifest.json` in this directory for machine-readable evidence.

## Metric boundary

All node/round precision, recall, verdict accuracy, confidence accuracy, R2/R3
invocation accuracy, and route accuracy are `null` or have zero scored Cases.
Reporting any of them as 0% would incorrectly attribute provider unavailability
to model semantic quality. No per-node semantic error attribution is possible
until at least one valid strict output is returned.

## Resume command

After the provider accepts this exact key, run a new immutable full-run ID:

```powershell
& .\.venv\Scripts\python.exe eval\persistent_runtime_v2\w1_w2_mu_future_v1\run_real_eval.py --run-id mu-w1w2-real-20260904-01 --max-workers 5
```

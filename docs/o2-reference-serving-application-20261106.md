# Production O2 reference date application — 2026-11-06

The November 1/2 recovery really ran against production Runtime and admitted its
O2/O3 revisions. Its earlier acceptance verified native tasks and candidate
coverage, but did not verify the frontend date projection. That acceptance was
incomplete for frontend visibility.

## Cause and scope

The live API uses `/data/read/active.json`, currently pointing at
`/data/read/serving-v4-20261018/v2.sqlite3`. Its Projector sources production Runtime.
The payload slimming filter in `SourceOutbox.event(serving=True)` omitted
`reference_deliveries`. `FormalProjectors` therefore never received the genuine
O3 reference delivery records required for `delta_day` and `reference_delta`.
The source records are still present and successful. The initial read-side check
found 32 of the original 43 ticker/date groups absent, with one further date
showing an older failed delivery. Other dates after the original recovery were
also affected by the same filter.

Unrelated read-side trade execution dependency gaps were observed but are not
part of this repair. No trade execution, model, candidate status or activation
pointer changes are made here.

## Applied repair

- Admit `reference_deliveries` to the existing derived projection input allowlist.
  Do not add its large snapshots to native Serving mirrors.
- Add a full serving-capture-to-date-projection regression, including NO_CHANGE.
- Build a one-file overlay from the exact running Projector image; its original
  outbox code matched local HEAD. Recreate only `v2-projector` and change only that
  image in the current release override.
- New Projector image:
  `sha256:f04c84b42be36d0768985ddf195f11366523777b44683f194dd6d721723538c9`.
- Deployment/rollback evidence: `/home/ubuntu/o2-reference-serving-20261106`.
- Reapply original successful reference delivery evidence through `FormalProjectors`
  and normal `ReadStore.ingest`, with repair receipt identities and no cursor reset.
  Current production Event activations are preserved, not rolled back to old runs.
- Also reconcile missing successful days beyond the original recovery, using
  first/latest successful captured inputs to retain each day's native before/after
  chain. Failures are recorded per date without blocking other dates/tickers.

## Validation

Seven reference capture, reference API and Projector contention tests passed.
New scripts/tests pass Ruff. Existing broad lint violations in outbox.py were not
reformatted as part of the one-line scope change.

Production replay and API acceptance files are written under
`/data/runtime/o2-reference-serving-20261106`. The API acceptance harness sends
ASGI requests through the deployed API code with the real production read alias,
using a verifier injected only into that isolated diagnostic process. It does not
alter the running API's public authentication or use fabricated business data.
Final replay counts and endpoint verification will be appended after completion.

## Final production acceptance

- All 43 original accepted backfill dates have now been replayed into the active
  production Serving generation. Two transient SQLite lock errors were isolated
  and successfully retried without repeating successful dates.
- Reconciled another **61** missing successful dates affected by the same filter,
  including later maintenance through semantic day 2026-11-04. This reuses
  successful source evidence, not new O2/O3 model runs.
- Production-data API route verification: all **43** original days pass list,
  date detail and (when present) before/after Event detail requests; all **253**
  successful source ticker/date groups pass the date-list endpoint check across
  ten tickers. There are zero outstanding replay errors.
- The endpoint harness ran the deployed API code with the real production alias.
  Its locally injected verifier does not change or bypass authentication on the
  running public server. No browser-login or visual screenshot check is claimed.
- Final proof: `/data/runtime/o2-reference-serving-20261106/acceptance.verified.json`;
  component receipts include original replay, two-date retry, reconciliation,
  original-day API details and all-day API list verification.
- No source candidates, maintenance outcomes or current activation pointers were
  rewritten. Reload the Events page to obtain a fresh view snapshot; existing
  view IDs intentionally preserve their original read sequence.

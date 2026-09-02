# Persistent Runtime W3 real-model acceptance — 2026-08-31

## Scope

- Fixed 12-Case public/synthetic corpus in `corpus_v1.json`.
- Real local Codex Worker and configured provider.
- Model `gpt-5.6-luna`, reasoning effort `max`, strict structured output.
- One ticker (`MU`), one persistent main thread, one Case per turn.
- No production business-state writes and no remote Supabase migration apply.

## Final result

Pilot run token: `abafef4040d1`.

| Gate | Result |
|---|---:|
| Cases completed | 12 / 12 |
| Strict outputs accepted | 12 / 12 |
| Expected semantic outcomes | 12 / 12 |
| Main-thread turns | 12 / 12 |
| Retried Cases | 0 |
| Case-correlation errors | 0 |
| Aggregate model latency | 385,799 ms |

The corpus covers Mode 1 direct LONG, direct SHORT, and NO_TRADE; Mode 2 OLD
revalidation, missed/false Policy repair, false OLD repair, NEW Policy execution,
and OLD + Policy BADCASE semantics.

## Defects found and fixed during acceptance

1. The model could return NEW without a RuntimeFactCandidate because JSON Schema
   cannot express that cross-field invariant. The prompt now states the invariant;
   the local Pydantic validator remains fail-closed.
2. A ticker main thread was being resumed across Case-scoped workspace roots, so the
   resumed Codex process could read the prior Case's `task.json`. Main threads now use
   a stable ticker workspace, while each Case has an immutable
   `cases/<w3_case_id>/` input directory. Fallback threads retain isolated workspaces.
3. W3 output now echoes and validates `w3_case_id`; stale/cross-Case responses fail
   with `w3_case_correlation_mismatch` and enter the persisted retry path.
4. Two synthetic fixtures contradicted the pinned PolicySet/Reference View. They were
   corrected so Mode 1 is genuinely uncovered and the missed-margin-policy case is a
   new 18% guide rather than a repetition of the recorded 20% guide.

## Automated verification

- Changed Python surface: Ruff passed; strict mypy passed for 18 source/script files.
- W3 + Persistent Runtime V2 focused suite: 29 passed.
- Final full pytest: 952 passed, 47 skipped, 19 failed. All 19 failures are existing
  unrelated prompt/baseline fixtures (missing legacy prompt resources/files and one
  CDECR prompt assertion); no failure names a W3 or Persistent Runtime V2 test.
- Full-repository Ruff/mypy are not clean on the pre-existing worktree; changed-file
  checks are clean.

The machine-readable final report is written by the Pilot runner to
`.tmp/persistent-runtime-w3-pilot/report.json`.

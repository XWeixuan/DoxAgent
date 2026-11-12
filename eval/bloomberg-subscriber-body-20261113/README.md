# Bloomberg paid BODY bounded acceptance

Sanitized production receipts, 2026-11-13 Asia/Shanghai. No paid article text or account/session secrets are stored here.

- `final-pipeline-receipt.jsonl`: four real subscription article reads through deployed SharedContentExtractor/Site Access; title/body counts, hashes, precise publisher times and routing evidence.
- `production-receipt.jsonl`: read-only normal background article outcomes, four test publications and zero formal Bus messages/subscriptions. Includes historical pre-fix PARTIAL and release-window connection failure; do not count all rows as new-version attempts.
- `budget-smoke.json`: final running code and one actual BODY under the 35-second outer deadline.

Reproduction uses the existing production containers, authorized identity and worker token files. `accept_pipeline.py` performs four real article requests with 8-second spacing; `budget_smoke.py` performs one. They do not publish or alter subscription configuration. `production_receipt.py` reads native durable records. `check_visibility.py` uses synthetic local DOM only; its `/tmp/bloomberg_document.py` import is a copy of `src/doxagent/site_strategy/bloomberg_document.py`, not a separate extractor service.

Do not run another login, change Profile/network or export cookies to repeat these checks. Use the existing Site Login Maintenance workflow if genuine authentication or human verification is required. Long-term success-rate and challenge-recurrence acceptance remains open.

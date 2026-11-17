# Massive Distribution regex tuning · 2026-11-12

See [report](../../docs/massive-distribution-regex-tuning-report-20261112.md), [results](summary.json), [case index](case-review.md), [verification](verification.json), and [selected configs](selected/README.md).

All candidates and GOLD are immutable. `design.py`, `second.py`, `correction.py` are preserved manual authoring recipes and refuse overwrites. `ops.py` replays the production classifier; validation timing is governed by the SOP. `verify.py` independently replays saved predictions. No production writes.

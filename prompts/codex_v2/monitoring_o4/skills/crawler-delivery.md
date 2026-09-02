# O4_DELIVER

The Configuration Plan is immutable. Work only its NEW_CRAWLER_REQUIRED items and closed candidate
lists; do not research replacement needs or redesign the portfolio. Preflight only for state drift,
identifier collision and retry state. An unrelated collision yields REPLAN_REQUIRED for that item.

For each item, progress independently through candidate selected, working version, implementation,
live probe, fixtures, certification, promotion, source registration, ticker binding and verification.
Persist stage/cycles/failure/status as work progresses. Keep completed stages and continue another
item after any failure.

Bounded engineering cycles:

- NORMAL: primary initial plus one repair; an alternative gets a viability check and at most one
  repair only when near success.
- HIGH: primary initial plus two repairs; alternative initial plus one repair.
- CRITICAL: primary initial plus three repairs and optionally one narrowly focused near-pass repair;
  alternative initial plus two repairs.

COMPLETED requires live probe evidence, all six certification checks PASS, ACTIVE promotion,
SourceDefinition schema alignment, explicit ticker binding and reread verification. Otherwise settle
the item FAILED, REPLAN_REQUIRED, or HUMAN_INTERVENTION_REQUIRED with durable evidence and retained
partial state. Never make another item's completion or Message Bus startup contingent on it.

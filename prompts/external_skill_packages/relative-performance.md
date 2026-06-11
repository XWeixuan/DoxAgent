+++
kind = "external_skill_package"
id = "relative-performance"
name = "Relative Performance"
version = "2026.06.12"
source_project = "schnetzlerjoe/hermes-finance"
source_kind = "hermes_finance"
applicable_agents = ["O4"]
applicable_task_types = ["generate_global_research"]
+++
# Relative Performance

Use this package only when O4 explicitly loads it for market-trace work.

Compare the ticker against the configured benchmarks, sector proxy, and close peers over the same date window. The goal is to decide whether the move appears stock-specific, sector-linked, broad-market-linked, or mixed.

## Analysis Focus

- Calculate or describe relative return versus benchmarks and peers.
- Identify divergences: outperforming while peers decline, underperforming in a rally, or moving in line with the group.
- Treat benchmark and peer evidence as context, not proof of causality.
- If peer or benchmark bars are missing, avoid overstating relative conclusions.

## Output Discipline

Use concise comparative language: outperforming, underperforming, in line, mixed, or inconclusive. Tie every relative-performance judgment to available market-data evidence.

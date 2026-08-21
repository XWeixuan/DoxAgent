# Market Situation Research v1 当前架构

> 日期：2026-08-20  
> 工作流：`codex_market_situation_v1`  
> lane：`market_situation_research`

Market Situation Research 是与 Global Research 完全独立的研究 lane。首版只负责宏观、
大盘、金融环境和目标证券价格状态，不读取或等待 Global Research。

## 首版 DAG

```text
lane-local horizontal collection -> parallel(C2, O4) -> assemble -> publish
```

- C2：当日与长周期宏观、大盘、利率、信用、流动性和汇率环境；
- O4：目标证券价格、多个窗口收益、相对表现、波动、成交、流动性、定位与走势；
- C2/O4 首版互不读取对方报告；
- 不读取 C1/C3/C5/C4、Global bundle 或其失败状态；
- 产物发布为独立 `MarketSituationBundle` 与 handoff，不是 Document 1 章节。

## 兼容边界

历史 `codex_d1_v2` 的 O4-B 保持 `o4_b`；新 O4 不自动继承或改写其历史 thread、artifact
和 checkpoint。更细的盘前/盘中/盘后持续监控编排留待后续版本。

## 权威实现入口

- Prompts：`prompts/agents/c2.md`、`prompts/agents/o4.md`
- Price skill：`prompts/internal_task_skills/ticker_price_tracking.md`
- Asset manifest：`codex_assets/market_situation_v1/bundle_manifest.json`
- Orchestrator：`src/doxagent/workflows/codex_market_situation/orchestrator.py`
- 总体边界：[`global_market_research_lane_rearchitecture_plan_20260820.md`](./global_market_research_lane_rearchitecture_plan_20260820.md)

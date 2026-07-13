# ReAct 节点配置矩阵

本次只调整 Document1/2 中原本使用默认 `max_steps=5` 的非 review 节点。`max_loop` 在当前实现中由同一个 `max_steps` 配置控制。

| Workflow | 节点 | 类型 | 最终 max_steps | 说明 |
|---|---|---|---:|---|
| Document1 | BuildGlobalResearch | generate | 10 | 原默认 5，调整为 10 |
| Document1 | ReviewGlobalResearch | deterministic review | N/A | 不调用 ReAct，未改 |
| Document1 | GenerateGlobalNarrativeReport | generate | 10 | 原默认 5，调整为 10 |
| Document2 | GenerateExpectationConstruction | generate | 10 | 原默认 5，调整为 10 |
| Document2 | ReviewExpectationConstruction | review | 5 | review 保持不变 |
| Document2 | ResolveExpectationConstruction | resolve | 10 | 原默认 5，调整为 10 |
| Document2 | GenerateExpectationDetails | generate | 10 | 原默认 5，调整为 10 |
| Document2 | ReviewExpectationFields | review | 3 | 原节点显式配置，保持不变 |
| Document2 | ResolveObjectionsAndDelegations | repair/resolve | 1 | 当前实际任务显式配置，保持不变；若未来该节点任务显式为 5，统一提升到 10 |
| Document2 | PromoteExpectationToBeliefState | deterministic | N/A | 不调用 ReAct，未改 |
| Document3 | GenerateKnownEvents | generate | 5 | Document3 不改 |
| Document3 | GenerateMonitoringConfig | generate | 5 | Document3 不改 |
| Document3 | ReviewMonitoringConfig | review | 5 | Document3 不改 |
| Document3 | ResolveMonitoringConfig | resolve | 5 | Document3 不改 |
| Document3 | GenerateMonitoringPolicy | generate | 5 | Document3 不改 |
| Document3 | ReviewMonitoringPolicy | review | 5 | Document3 不改 |
| Document3 | ResolveMonitoringPolicy | resolve | 5 | Document3 不改 |
| 其他 workflow | 其他 ReAct 节点 | existing | 原配置 | 不改 |

实现规则：仅当目标节点的有效 `max_steps` 为 5 时改为 10；显式配置为 1、3 或其他值时原样保留。

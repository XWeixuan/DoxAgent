# Message Bus 采集模式与共享分发操作

本模块将采集入口分为 `by_ticker`、`by_search`、`by_distribution`。首批仅将 Reuters Site Search 与 Google News Search RSS 改为 `by_search`，注册但默认关闭工商时报半导体栏目 `ctee_semiconductor`。Benzinga、Yahoo 等现有入口保持原路径。工商时报没有自动订阅任何 ticker。

## 迁移

先在运行 Message Bus 的环境执行：

```bash
python -m doxagent.message_bus_v2.cli migration preview
python -m doxagent.message_bus_v2.cli migration apply --backup-dir /data/bus/backups
```

`preview` 只列模式变更和现有 Reuters/Google binding 的显式旧词，不猜测翻译或截断超过三个的词。`apply` 先用 SQLite 在线备份创建可恢复副本，再幂等迁移两个 source，并创建 terms/distribution 表；不改 stream offset。数据库位置取 `DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH`。旧 binding 在统一词表提交前继续走 `LEGACY_TERMS`，新 search binding 应先完成词表。切换代码版本前须先 drain 新的共享正文与分发任务；不要用旧 Hub 消费 `distribution_article` owner。

## 监测词提交

`docs/examples/monitoring-mu.example.yaml` 只演示格式，不代表经投资判断确认的生产 MU 词表。首次 `expected_revision: 0`，后续以 `terms show` 的 revision 为准。每个 ticker 最多三个 L1 概念，但每个概念必须覆盖当前所有 search/distribution 入口语言；首批为 `en`、`zh-Hant`。L2 规则与 relevant/irrelevant 定义随一次提交统一生效。

```bash
python -m doxagent.message_bus_v2.cli terms validate --file docs/examples/monitoring-mu.example.yaml
python -m doxagent.message_bus_v2.cli terms apply --file docs/examples/monitoring-mu.example.yaml --actor user
python -m doxagent.message_bus_v2.cli terms show --ticker MU
python -m doxagent.message_bus_v2.cli terms history --ticker MU
python -m doxagent.message_bus_v2.cli terms preview --ticker MU
python -m doxagent.message_bus_v2.cli terms test --ticker MU --source ctee_semiconductor --article /tmp/ctee-article.json
```

Reuters 对 L1 各建一个搜索页；Google 用一页 OR 查询。terms revision 改变时 query key 改变；进行中的 CLOSED_SWEEP 固定原计划。L2 的入口语言与英文规则并集用于 distribution，组内 `none` 仅排除本组。`terms apply` 不创建 source binding，不触碰其他 ticker。
`terms test` 仅对提供的一篇 `RawMessageInput` JSON 做本地规则 dry-run，不抓网站、不发布或回放历史。

## 启用首批共享入口

先完成工商时报 Site Access 状态和正文可读性现场确认，然后由操作员选择确实订阅该栏目的 ticker，提交其监测词，再以现有 Message Bus source/binding 管理接口启用 `ctee_semiconductor` 并绑定这些 ticker。入口 URL、语言和 300 秒调度属于 source；binding 仅决定订阅/streaming，不得逐 ticker 改栏目或频率。没有有效订阅时不抓取。

```bash
python -m doxagent.message_bus_v2.cli distribution status --source ctee_semiconductor
python -m doxagent.message_bus_v2.cli distribution decisions --ticker MU --limit 50
```

共享 run 保存原始文章、观察关系、正文版本和每 ticker 的 decision/delivery；真正发布仍经过 ticker 本地准入、去重和 stream。闭市 sweep 保留每 ticker 的 SOURCE_SWEEP task，等待其 distribution delivery 终结，失败会留下 gap/PARTIAL。实时仍用现有 1800 秒精确发布时间准入；DATE/UNKNOWN_FIRST_SEEN 不被改写为伪精确时间。

## Jev 实验轨

OpenRouter 使用当前官方 Decisions API `POST /api/alpha/decisions`，`typesafe/jev-1.13`。仅给实际运行 classifier 的 `v2-message-bus` 配 `OPENROUTER_API_KEY`；本地 `.env` 已忽略，勿在文档、日志、提交或命令回显中输出值。生产 Compose 默认 `DOXAGENT_MESSAGE_BUS_JEV_ENABLED=false`，未注入远端 key 时正则轨照常工作。开启前先对少量人工标注样本核对相关/不相关与成本。每文章×ticker 最多两轮，至多 16 ticker/批、15 秒/请求、60 秒文章总预算。正则或 Jev 任一命中即可投递，双中只发布一次；Jev 不可用且正则未命中按不相关终结，保留诊断原因。

## 回滚与验收

优先关闭 Jev，再视需要关闭 `ctee_semiconductor`；不要删除新增表或已发布消息。完整代码回滚前先 drain `distribution_article` 正文任务及 delivery。验收要分别看入口采集、正文 FULL/降级、L2 命中、Jev 命中/失败、时效拒绝、去重、发布与窗口覆盖；HTTP 200 或搜索固定返回量均不能证明业务覆盖。工商时报栏目达到分页上限报告 PARTIAL，不能伪称完整。生产 Jev 尚须远端 key 和用户选择的正式监测词/订阅才能验收实际增益。

# Bloomberg 正式订阅与分类登记

2026-11-13 北京时间 06:07:59，由用户明确要求将 Bloomberg 从测试源转为正式源。

- 正式 source_id 为 `bloomberg_massive`，源版本 6，delivery_mode=production、enabled=true。
- 已启用十个实际绑定：INTC、MU、BE、COHR、NBIS、CEG、RKLB、SNOW、ONDS、IONQ。前端以这些真实绑定显示订阅标签，历史 test_tickers 已清空。
- 保留 Massive Distribution，使用专用正则，不调用 Decision Model；只有完整正文才能正式发布。Latest HTTP 与 Artemis HTTP 快照继续共享采集，未为十个绑定创建十套浏览器或列表轮询。
- 九个 ticker 的 Massive 规则未改变。IONQ 原无 Massive 规则，将其当前英文 L2 的四组规则复制为独立 massive_l2；普通 L1/L2、definition、X 账号配置逐字段确认未改变。此举沿用当前 IONQ 监测口径，不代表其已经过 Newswire 专用 P/R 调优。
- 分类按 `C:/DoxMetrics/docs/message-source-classification-maintenance.md` 维护：GLOBAL、sector_ids=[]、列表 HTTP、正文 Chrome/CDP，均无 fallback。补充 Latest/Artemis 公共入口，删除“测试”用途说明；不把订阅名单写入分类文件。
- 仅发布 API 角色以更新静态分类；没有重建 Collector、正文服务、Chrome Supervisor 或前端。订阅通过既有 MessageBusV2Service/版本化规则配置流程生效。

生产回读十个绑定均启用、source_version=6；两个共享物理单位分别为 WIRE:latest（30 秒）和 WIRE:realtime（10 秒），各映射十个绑定且轮询成功。转正式后的首批三篇文章已取得 FULL。历史测试文章转为正式发布计数为零，没有把测试台账批量导入 Bus。

分类回归 10 项通过，Ruff E9/F 与 diff 检查通过。配置备份位于生产 Bus 持久卷 `/data/bus/ops/bloomberg-production-20261113/before.private.json`（0600）；公开回执为 `eval/bloomberg-production-activation-20261113/activation.json` 与 `verification.jsonl`。

实际登录的生产前端 `/message-bus/sources?q=Bloomberg` 已刷新核验：位于全局通用分区，显示 Ticker 订阅 10、运行 10、轮询成功、列表 HTTP、正文 Chrome/CDP；十个 ticker 标签齐全。观察时已有 4 条正式发布消息，正文样本为 3/3 完整。此处是启用后的有限样本，不代表长期成功率或发布→发现时延已经通过验收。

历史正文开发验收见 `bloomberg-subscription-body-acceptance-20261113.md`；其中“保持测试隔离”描述的是本次正式授权之前的状态，以本记录为当前订阅状态。

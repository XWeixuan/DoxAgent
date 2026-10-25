# Message Bus 休市临时采集开关

只绕过 Bus 已确认的非交易日持续采集门禁，不修改 MarketCalendar、研究/交易 schedule、语义日或 Runtime 休市流程。日历不可用时不以开关掩盖错误。原来全年采集的 IR、SEC 仍维持原行为；暂停、禁用订阅、查询/频道、周期、checkpoint、退避、身份与预算限制照常生效。

## 操作

在生产 Bus 容器内使用既有运行配置，不需重启：

```bash
docker exec doxagent-v2-v2-message-bus-1 python -m doxagent.message_bus_v2.cli collection status
docker exec doxagent-v2-v2-message-bus-1 python -m doxagent.message_bus_v2.cli collection enable --until '2026-10-26T15:00:00+08:00' --actor user --reason '休市功能验证'
docker exec doxagent-v2-v2-message-bus-1 python -m doxagent.message_bus_v2.cli collection disable --actor user --reason '提前结束验证'
docker exec doxagent-v2-v2-message-bus-1 python -m doxagent.message_bus_v2.cli collection history --limit 20
```

`--ticker MU,BE`、`--source yahoo_finance_news,barrons_ticker_news` 可分别限定 ticker/source；同时填写取交集，省略则动态覆盖所有原本已启用、可采集订阅。未知 ticker/source 拒绝操作，防止拼写错误变成意外全量。`enable` 不带 `--until` 表示手动开启直到关闭；带截止时间必须明确时区且在未来，再次 enable 可调整期限/范围。开关默认关闭，发布不会永久打开。

`state` 为 DISABLED / ACTIVE / EXPIRED，`effective` 是实际是否生效；到期不需要另一个 timer、进程或共享日历写入。操作历史在 Bus `bus_collection_control` 追加记录，保存版本、时间、actor、原因、期限及范围。EXPIRED 从持久截止时点计算，重启不延长有效期。disable 保留所结束会话的范围与原期限，操作记录不会覆盖旧记录。

## 停止边界与衔接

下一次调度读取持久状态。临时轮询还携带可撤销会话，在取得请求许可、HTTP 发送及提交 Site Access 前重新检查版本和期限，避免等待中的旧任务继续发起新请求。替换会话后旧会话不继承新授权。已经发送的 HTTP 或已提交给 Site Access 的访问属于在途请求，可正常返回；一个已提交的浏览器执行内部导航属于该在途执行。后续分页、回退和重试必须再次检查，关闭不是清理浏览器或取消整个持久任务。

结束会话按 `TEMPORARY_COLLECTION_ENDED` 记延期，不增加来源失败计数或触发网站失败退避。控制中止单独传播，不被网站适配器的普通异常回退吞掉。已捕获 page 的收据和 checkpoint 保留，持久接纳 worker 明确脱离临时请求上下文；独立 BODY worker 继续执行既有任务。

开关不修改绑定版本或重建 poll state，不清空 first-seen、Raw/Stream、文章与正文结果，不重置分发游标。下一交易日仍通过原接纳窗口、去重、正文复用和 ARTICLE_ONCE 接管；同文更新原消息及修订，不重投已消费文章。已有休市 sweep 与正常历史覆盖机制保留。它不扩大既有新闻时效窗口，也不承诺网站已移除历史的无限回补。

## 本轮整合与资源恢复

- 核对基线 main/origin/main/远端均为 `575bca85`，无分叉。已提交的 SEC 等开发完整保留。
- 纳入其他会话未提交的 W3 AGENTS/skill 改动：ticker 级 Surprise、传导概率与分量、消息时效独立判断；不改 schema 或已冻结 Case。相关 28 项测试通过。
- 纳入 BE D1/D2/D3 重初始化的 7 个运维脚本及审计文档，仅归档已有工作，不执行脚本、不激活研究候选或改生产配置引用。历史 eval/exports、私有回执和模型工作区保留本地，不将海量运行数据打包为应用代码。
- 服务器实测 8 核、15,602MiB RAM。此前发布 overlay 显式全局浏览器 4 是旧硬件适配，本轮恢复为 8，并在生产 Compose 保留可覆盖的默认 8。BODY 常规 2、无条件借第三槽 false 属于原 8 核公平预算设计，继续保留；局部 Site/source 上限已为自适应 8。已验证身份能力单独保留，不随 CPU 数字扩大；无消息服务低 CPU quota 的情况下沿用相对权重。

部署版本、实际有效容量及简短链路检查补充于本轮发布回执。不开展长时间性能观察，不据基础健康或硬件升配宣称周期/正文成功率目标达成。

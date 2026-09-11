# MU Message Bus P0 正文补全事故归因与代码审查

日期：2026-09-12  
审查范围：事故时线上提交 `6f3792ee`、应急修复、当前提交 `b0cb43b1`，以及 Message Bus V2、Content Enrichment、Runtime V2 和 Read Projector 的相关链路。  
结论状态：本次 P0 主故障链已关闭；审查发现的 P1 正确性/可观测性风险和可直接收敛的 P2 边界缺口，已在同日后续窄修复中处理。  
实施补记：本报告先完成归因与代码审查，随后按第五节方案修改运行代码；实现与验证状态以本报告末尾实施记录和仓库 `changelog` 为准。

## 1. 执行摘要

### 1.1 是否因为把 V1 正文补全硬接入 V2

结论是“部分是，但不能简化为版本号不兼容”。

事故时线上 V2 Message Bus 的 `factory.py` 会在开关打开时创建
`message_bus_v2.content.ArticleContentMaterializer`。这个 bridge 在 V2 poll 协程内直接调用
`doxagent.monitoring.media_enrichment.enrich_media_records()`。后者位于 V1 monitoring 命名空间，
但这里只复用了提取算法和网络访问代码，没有接入 V1 数据库、V1 poller、V1 binding 或 V1 stream。

因此，准确表述是：

- 不是 V1 workflow 和 V2 workflow 同时消费同一消息；
- 是把为旧 monitoring 链路形成的、具有网络波动性的正文提取实现，以同步等待方式嵌入了 V2
  的 poll/identity/dedupe 主路径；
- 真正导致 P0 的是错误的接入顺序和幂等边界，而不是模块名叫 V1。

事故触发链如下：

```text
Finnhub 每 60 秒返回最近三日累计集合
  -> 每一条历史 item 每轮都重新抓目标网页正文
  -> 网页中的实时行情、推荐内容或提取结果发生波动
  -> enrichment 后才计算 content_hash
  -> 旧 record_raw 只比较最新 revision 的 content_hash
  -> 同一 provider payload 被误判成新 revision
  -> bootstrap 后的历史消息 revision 被发布为实时 Standard/Stream
```

这解释了前端出现 9 月 9 日、10 日旧新闻，以及 Raw/Standard/Stream 快速膨胀。

### 1.2 “全部没有进入下游”不是数据库事实

事故清理前数据库实际存在：

- 749 条 Raw；
- 477 条 Standard；
- 477 条 Stream；
- 476 个 Runtime CASE task；
- 131 个 Runtime case；
- 486 个 turn、124 个 effect、104 个 candidate、608 条 admission。

所以消息并非全部停在 Message Bus。前端之所以看起来“没有进入下游”，是另一个可见性问题：

- Dashboard 的 V2 message DTO 没有把 StandardMessage 映射到真实 Runtime V2 task/case，
  `runtime_execution_id` 固定为空；
- Managed V2 scheduler 的详情读取仍访问可能为空的 legacy runtime；
- Read Projector 对 Runtime Case 删除事件的空 payload 不兼容，影响清理后的投影追平。

应急修复已经分别补齐 Runtime V2 映射、legacy runtime 空值处理和 projector 删除事件兼容。

## 2. 事故时线上代码的直接缺陷

### 2.1 P0：在 poll 内逐条等待正文网络抓取

旧 `accept_poll_result()` 对每条 Finnhub item 直接 `await accept_message()`；
`accept_message()` 再 `await ArticleContentMaterializer.materialize()`。一次累计 poll 的所有历史消息因此
串行进入外部网页抓取，poll 完成、PollState 更新和 bootstrap 完成均被正文网络延迟绑架。

这既放大了请求量，也扩大了进程中断、超时和重复执行窗口。

### 2.2 P0：在非确定性 enrichment 后判重

旧链路只有完成网页提取后才计算 `content_hash`。该 hash 包含 title、完整 body、source、最终 URL
和 published_at。网页正文中的实时行情或动态块发生变化，就会改变 hash。

旧 `record_raw()` 只将“同 identity 且与最新 revision 的 content_hash 相同”视为 duplicate，
没有使用原始 provider payload 的 `raw_hash`。因此即使 Finnhub JSON 完全相同，只要网页提取内容变化，
也会创建 revision。

### 2.3 P0 放大器：累计接口与 bootstrap 状态

Finnhub adapter 固定查询最近三天。该行为本身允许存在，但只有在 provider payload 级幂等成立时才安全。
第一次 poll 建立 bootstrap 后，后续累计响应中的相同历史 item 应被跳过；旧实现却让它们再次进入网页抓取，
并把正文波动误判为 bootstrap 后的新修订。

### 2.4 P1：PollState 成功不能证明正文或下游完成

旧实现和当前设计都将 PollState 定义为 provider acquisition 状态。事故时前端只展示 source 成功/失败，
没有展示 enrichment queue backlog、最老任务年龄和 Runtime 映射，因此“provider 成功、正文阻塞、下游不可见”
可以同时存在。这不是 PollState 语义错误，但属于 P0 事故所暴露的可观测性缺口。

## 3. 当前版本已经解决的部分

当前 `b0cb43b1` 已完成以下关键修复：

1. poll 只把输入写入 Message Bus SQLite 中的 durable enrichment queue，不再等待网页抓取；
2. 独立 `v2-content-enrichment` worker 统一处理全部 ticker/source，全局并发上限为 8；
3. 进程生命周期内复用一个 HTTP session 和一个 `DomainFetchController`；
4. 同一 provider payload 在入队前按 ticker/source/identity/raw_hash 跳过；
5. Raw 落库再次按 identity + content_hash 或 identity + raw_hash 判重；
6. enrichment 失败按 body -> summary -> 空串降级，不阻断整批 poll；
7. transient fetch 最多一次 retry，并受 180 秒 deadline 约束；
8. TikHub/Stocktwits 通过 source policy 跳过网页抓取；
9. Dashboard 已映射真实 Runtime V2 task/case 状态；
10. scheduler 和 Read Projector 的事故相关空值错误已修复。

定向测试结果为 17 passed。线上复核时 MU 的 Raw 数量与唯一 identity 数量一致，最大 revision 为 1；
最新检查为 162 Raw / 162 identity / max revision 1、2 Standard、2 Stream、2 Runtime case、queue 0，
三个 source 的 poll state 都是 succeeded 且无 last_error。当前没有再次出现假 revision 膨胀。

因此可以判定：本次 P0 的主故障链已经解决，当前线上不需要再次紧急停机。

## 4. 当前代码仍然存在的风险

### 4.1 P1：active queue 会吞掉同一 item 的真实修订（已复现）

当前 `intake_key` 只包含 binding_id、source_item_key、URL 和 published_at，不包含 raw_hash。
如果同一 external ID 在旧 enrichment job 尚未完成时收到新的 provider payload，
`enqueue_enrichment_job()` 会命中同一个 key，但只刷新 last_seen_at/updated_at，不更新 message，也不新增 job。

本次审查的零网络反例结果：

```text
first_created=True
second_created=False
queue_len=1
queued_payload=旧 payload
```

这不会重现本次“假 revision 洪水”，但可能丢失真实公告修订。60 秒轮询通常会在 job 完成后再次看到修订，
但这依赖 provider 后续仍返回该 item，不能作为正确性保证。

### 4.2 P1：worker 吞掉未处理异常，可能再次形成“看似运行、实际不下游”

`ContentEnrichmentHub.run_once()` 使用 `asyncio.gather(..., return_exceptions=True)`，但没有读取、记录或上报
返回的异常。提取异常已在 item 内转换为 fallback；因此逃出 `_process()` 的异常通常是 finalization、数据库、
schema 或代码错误，属于应该让 worker 失败并由容器重启/报警的基础设施错误。

当前行为会让进程继续显示 Up，job 保持 RUNNING，等待 lease 到期后再被静默领取。永久错误可无限循环，
PollState 仍显示 succeeded。这个缺口与本次事故的“前端看不到真实阻塞”高度相关。

### 4.3 P1：finalization 没有实现文档承诺的同事务语义

方案要求 Raw finalization 与 queue delete 位于同一 SQLite transaction。当前实现实际上是：

```text
accept_message() 的多个 transaction
  -> record Raw
  -> 标记 PROCESSING
  -> Standard/Stream finalization
  -> 单独 delete queue row
```

崩溃后 `retry_pending_raw()` 和 raw_hash dedupe 能避免大多数重复发布，但恢复依赖 Message Bus scheduler
另一个进程继续运行。若在 Raw 写入后、Standard 创建前崩溃，enrichment worker 重试会看到 duplicate 并删除
queue job，Raw 的补完要等待 scheduler 的 pending recovery。这是可恢复窗口，不是当前 P0，但实际代码与设计契约不一致。

### 4.4 P2：Finnhub redirect 阶段未进入共享域名 limiter，429 不会 retry（已复现）

`SharedContentExtractor` 把 controller 传给 direct/reader fetch，但 `_resolve_fetch_url()` 在 controller 外直接请求
Finnhub redirect，最多五跳。该阶段既没有 Finnhub 域名 pacing，也没有生成带 status/transient_hint 的 FetchAttempt。

反例显示仅有 `reason=http_429`、没有 attempt/status 的结果被 `_retryable()` 判为 False。
这会降低 Finnhub 正文补全成功率，但 fallback 与 raw_hash 防线会阻止其升级为本次 P0。

### 4.5 P2：无 title/body/summary 的消息仍会发布（已复现）

实施方案要求这类 item 只保存 Raw，并标记 `content_unavailable`，不得创建 StandardMessage。
当前 `_standardize()` 无该门禁。反例得到 `empty_standard_count=1`、`empty_stream_offset=1`。

### 4.6 P2：旧同步 bridge 仍留在代码树中

当前 factory 已不再引用 `ArticleContentMaterializer`，但 `message_bus_v2/content.py`、
`ContentMaterializer` Protocol 和构造参数仍存在。它们是死代码，却提供了一条可以误把同步 enrichment
重新注入 V2 poll 的入口。

同时，新 `SharedContentExtractor` 仍从 `monitoring.media_enrichment` 导入公开类型及两个私有 `_default_*`
函数。当前没有混用 V1 数据面，但模块边界仍是 V2 反向依赖 V1 命名空间，后续改旧 monitoring 时存在回归风险。

## 5. 建议修复方案

原则：不引入 Kafka、Redis、分布式锁或新工作流；直接修正现有 SQLite queue 和 worker。

### 第一组：发布前必须完成的 P1 修复

1. **让 queue 幂等键区分真实 provider revision**
   - 计算一次 `provider_raw_hash`；
   - `intake_key` 改为 binding_id + identity_key + provider_raw_hash；
   - EnrichmentJob 显式保存 provider_raw_hash，避免重复计算和语义漂移；
   - 测试同 external ID、不同 raw payload 在同时 active 时两条都被最终处理；同 raw payload 仍只有一条。

2. **删除 silent exception**
   - 去掉 `return_exceptions=True`，或逐项检查并重新抛出；
   - extractor 的网络/内容失败继续 item-local fallback；
   - finalization、SQLite、schema 等逃逸异常直接让 worker 退出，由 `restart: unless-stopped` 恢复；
   - 日志必须包含 job_id、ticker、source_id，不记录正文和凭据。

3. **让 enrichment worker 自己闭合 Raw 恢复**
   - `accept_message()` 命中 duplicate 时，如果既有 Raw 仍为 PENDING/PROCESSING，立即幂等完成 Standard/Stream；
   - 不再把恢复完全委托给 scheduler；
   - 增加 Raw 已写、Standard 未写和 Standard 已写、queue 未删两个 crash-point 测试。

### 第二组：同一次修复中顺手完成的窄修正

4. **把 Finnhub redirect 纳入共享 controller**
   - `_resolve_fetch_url()` 接收同一个 DomainFetchController；
   - 每一跳都走 Finnhub 域名 pacing；
   - redirect 429/5xx 生成结构化 FetchAttempt，使既有一次性 retry 规则生效。

5. **落实 content_unavailable 门禁**
   - Raw 必须保留；
   - 最终 title 和 fallback body 同时为空时，将 Raw 标记 COMPLETED/content_unavailable；
   - 不创建 Standard/Stream，不阻断同批其他消息。

6. **拆除旧同步回归入口**
   - 删除 `message_bus_v2/content.py`；
   - 删除 `ContentMaterializer`、`PassthroughContentMaterializer` 和 service 构造注入口；
   - 当前 Hub 是 V2 正文补全的唯一执行路径。

不建议在这次修复中搬迁整个 1400 行 `monitoring.media_enrichment.py`。先消除死 bridge 和私有函数依赖，
后续再把通用 extraction core 独立成中性模块；现在整体搬迁会扩大风险且对本次正确性没有额外收益。

## 6. 最小验收集

代码测试只需要覆盖以下六组：

1. 同 provider payload 重复 100 次：一次 enrichment、一个 Raw、零 revision；
2. 同 external ID、两个不同 raw payload 同时 active：两者不互相吞掉；
3. enrichment 正文每次包含不同实时行情：相同 raw payload 不产生 revision；
4. finalization 两个 crash point：重启后恰好一个 Standard/Stream；
5. Finnhub redirect 429：只 retry 一次，超过 deadline fallback；
6. 空 title/body/summary：只保留 Raw，不发布 Standard/Stream。

线上小流量验收观察至少三个 poll 周期：

- `raw_count == distinct(source_id, identity_key)`（没有真实修订时）；
- `max_revision == 1`（没有真实修订时）；
- enrichment queue 能回到 0，且 oldest age 不持续增长；
- Stream 与 Runtime case 均能形成对应状态；
- PollState、enrichment backlog 和 Runtime 状态分别展示，不再互相代替。

## 7. 最终判断

- **本次 P0 是否由 V1 硬接 V2 导致：**旧 V1 extraction library 被硬嵌入 V2 poll 是重要原因，但根因是
  integration order 和 dedupe contract 错误，不是 V1/V2 数据库或消费者混跑。
- **本地后续修复是否解决事故：**解决了本次假 revision 洪水、poll 阻塞和 Runtime 可见性问题；线上数据已证明
  相同 Finnhub payload 不再反复形成 revision。
- **当前是否仍有风险：**有，但尚无正在发生的 P0。active revision 被吞、silent worker exception 和跨事务恢复
  是应尽快修复的 P1；redirect retry、空内容发布和旧 bridge 是同轮可以直接清掉的 P2。
- **建议动作：**按第五节一次完成窄修复，然后做六组定向测试和三个线上 poll 周期验收；无需重新设计 Message Bus。

## 8. 2026-09-12 实施记录

第五节的六项窄修复均已实现：active queue 以 provider raw hash 区分真实 revision；worker 的基础设施异常会显式退出并由容器恢复；重复 intake 可直接结算中断遗留 Raw；Finnhub redirect 纳入共享限流和瞬态重试；空内容只保留 Raw；旧同步 materializer bridge 已删除。事故链相关 22 项聚焦测试与变更文件 Ruff 检查通过，未执行无关全量测试。

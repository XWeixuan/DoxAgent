# 全局消息源故障与端到端延迟逐源修复（2026-11-16）

## 范围与判断原则

按 Yahoo → SEC → 最严重延迟来源逐项处理。Reuters、Bloomberg 不在本轮修复范围。不修改交易时段、轮询周期、新稿准入、相关性阈值或 recheck 开关。每项保留自然运行回执，HTTP 成功、浏览器成功和正文成功分别验证。

初始页面同口径快照 `eval/global_sources_repair_20261116/catalog-initial.json`：异常全局源是 Yahoo、SEC、Bloomberg。Investing 端到端均值约691秒，Finnhub313秒、Google News288秒、Barron's279秒；这些数字暂不能直接解释为实时服务耗时。页面在24小时不足5篇时回看历史最近5篇，须进一步对齐原始消息、正文队列、分发及投影时间。

## 1. Yahoo（已确认根因与执行项）

历史 Nov10 的依赖停服、Nov11 的共享 HTTP 许可问题不是当前主因。当前浏览器补充列表持续成功，RSS HEAD 连续失败；新 HTTP Session 同样失败，排除单纯旧连接池失活。

部署迁移遗漏：服务器 Compose 把 Yahoo 专用 HTTP 路径留在旧 Bus 角色，实际采集已迁到 Collector。Collector 有效 `yahoo_http_routes=null`，回落至通用代理；该路径访问 Yahoo RSS 的 TLS 握手失败。对同一 MU RSS 进行受控对比：通用路径约5秒 SSLError(35)，直连200/18条、US路径200/18条；原先专用 DE/TR 两路径也均200/18条。浏览器身份没有坏，不能用清 Profile 或重启 Supervisor 修复此问题。

内部放大：Yahoo 的8秒网络预算还包含共享许可和节奏等待，坏路径占住唯一 RSS 网络槽后，其他 ticker 未发请求就超时。

执行：

1. 在 Collector 的正式 Compose 配置补回原有 DE/TR 专用路径；标准宿主机发布 manifest 同步写入该有效配置，防止仅源码正确、运行配置仍错误。
2. Yahoo 复用现有 `joint_permits/network_timeout/network_wait`，排队和节奏等待不消耗网络预算；调用方的总任务截止、限流熔断和许可数量均保留。不添加后台线程或新恢复状态机。
3. 网络与取消等待回归后只发布 Collector；核对有效配置，观察全部订阅 ticker 的自然 RSS HEAD 和 browser supplement，不以一次 canary 作为恢复结论。

发布与初验：`c010a2445`，合并同期 main 后以 `2d2f28bff` 发布 Collector。UTC13:45:55启动，已确认有效路由是DE/TR。全部10 ticker 的 RSS、GraphQL、浏览器补充均出现自然成功；源码18项 Yahoo 回归通过，外围55项通过。一项原有 LiveDeliveryWorker 测试 fixture 缺少 `binding_id` 失败，非此次 Yahoo 改动导致，未据此改业务代码。

## 2. SEC EDGAR

生产共享 `/data/bus/sec-http/rate.json` 大小为0，最后修改时间UTC2026-11-13 15:09:36，与最后成功时间一致。HEAD 约8毫秒即报JSONDecodeError，网络请求数组为空。当前报错是持久化本地状态损坏，不是SEC返回非法JSON；空文件形成时是否为磁盘满或进程中断，现有证据不足以唯一归因。

原实现持有独立 `rate.lock`，但JSON使用 `write_text` 先截断原文件，异常后无恢复，导致全部 ticker 及BODY共用通道永久受影响。修复沿用原锁与两条2rps通道，临时文件完整写入、flush/fsync后原子替换；失败保留旧文件并收尾临时文件。读取发现损坏时原子重置为一次60秒共享cooldown，随后自然恢复，避免丢失未知旧限流后立即发请求。合法cooldown、发现/BODY独立节奏及SEC正文筛选均保留。

先发布 Collector（验证自然发现），同一HTTP类被BODY使用，因此确认后同步发布 Enrichment，防止旧BODY进程继续使用非原子写法破坏共享文件。无需删除业务库、回放旧文章或重启Chrome。

## 后续逐源记录

高延迟先拆 Investing 的真实逐篇时间线，再评估是否有跨源共因。不凭均值把历史等待删除或把周末耗时强制归零。

## 3. Investing 高端到端耗时：历史与真实本地积压并存

页面50条延时样本按发现日拆分：11/13共13条均值1699.83秒；11/16共18条均值620.88秒；其他19条来自11/10–15。原因是各ticker独立执行“不足5条回看历史5条”的既定口径，再由全局卡片汇总，不是统一的当前运行窗口。

但不能全归咎于口径：今天07:00扫入的MU文章，BODY在07:02–03已完成，直到07:19才正式发布；INTC也从07:02完成等到07:20。耗时主要在BODY后的本地分发。周五IonQ坏例另含storage_unavailable/site_budget_deferred累计等待两小时、以及BODY完成后16–19分钟本地等待。10:50之后的8条今日样本则为1.77–9.32秒，不能把页面691秒解释为当前每次抓取都耗时691秒。

### 可复现的共用根因

`published_candidates` 声称做索引精确查询，但生产EXPLAIN实际由 `logical_message_versions` 的ticker前缀开始，遍历历史版本，最后才过滤本次少量URL别名。未命中在生产耗时85.5–131.5毫秒、约146,000条SQLite指令；此查询既在准备阶段又在最终写事务内执行，会把恢复/扫入批次的负载放大为业务积压。历史越多，代价越高，正是“修复后短期正常，积累后再次变慢”的内部机制之一。不能凭现有历史回执把全部16分钟唯一归因为这一条SQL，但这项低效已直接证实。

同一生产库只读A/B：别名精确查找放到外层后约0.4毫秒、低于1000条指令，结果同为未命中。修复固定CROSS JOIN顺序并选用既有版本主键/正文指纹索引，不建新索引、不改去重、ARTICLE_ONCE、ticker隔离、发布原子性或消息时效规则。新增万条历史和过期统计回归，覆盖未命中、命中、未发布版本不算命中及正文指纹回退；运行旧去重业务回归。

SQLite执行顺序依据：[官方查询优化说明](https://sqlite.org/optoverview.html#manual_control_of_query_plans_using_cross_join)。此处使用范围限于已证明误选计划的热点，不对全库运行ANALYZE或添加定时维护。

只发布使用这些去重入口的Bus、Collector、Enrichment。验收分别记录实际查询计划/指令量、自然文章分阶段时间、PENDING年龄；旧延时样本不重写，未发生自然新稿则不宣称该源新的总体均值已达标。

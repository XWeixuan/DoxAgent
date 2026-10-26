# 消息管线业务增量与 I/O 优化：实施及生产实测

日期：2026-10-27（北京时间）。对应方案：`message-pipeline-business-delta-io-and-latency-optimization-plan-20261027.md`。

## 1. 实施范围

- 列表进入持久化前计算业务输入指纹。相同未完成输入只有一个 leader；后续物理请求保留独立回执并关联处理结果。已判定旧输入在写 payload 前过滤，只保存新输入和实质变化。指纹保留来源/绑定/词表/接纳版本、日期依据及未知业务字段，仅去掉明确的请求追踪噪声；未完成、不完整分页及 CLOSED_SWEEP 不被误当作完成。
- per-unit checkpoint 分表写入，单个 unit 更新不再展开/重写同绑定其他 unit。Owner 保留完整 checkpoint；CDC 只传健康、计数、必要端点摘要及正文任务小字段，正文内容仍由 Owner 完整保存。正文对象按内容复用，变化的时间戳包装不再复制正文。
- 修复 L2 续作旧 token 重放：只领取未完成交付、重新移交所有权、心跳续租，已终结交付不重复判定。READY 分发独立补槽；正向 L2 结果立即发布，其他 ticker 独立收尾；去掉 L1 已确定正向后的默认实验模型调用。判定、现行配置核对、发布、交付回执合并为一个有 fence 的业务事务。
- BODY 上报后台收尾、清理有界低频执行，容量告警合并为一笔事务并节流；保留新正文优先和原 CRAWLER/BODY 容量，不扩大浏览器全局页数。
- 修正陈旧 due 的当前时隙归属，历史缺失周期仍计入分母；已有 on_page 回执后不再追加无物理响应的空 terminal capture。保留 Cache-Control/Age 等响应证据。
- 修复 generic publisher 在 scoped runtime 下原站导航误拒绝：验证公网 URL 后，只允许原 hostname 及 www 规范变体；无关域和私网仍拦截。保留既有身份、出口、风险恢复和付费权限。
- Site command 快照按真实 profile/catalog revision 缓存；idle 扫描批量取 scope，公开 publisher 空闲生命周期缩短至 180s；不重启 Chrome Supervisor。Reuters 单独、显式配置源/Site 上限 2 和 1.5s 请求节奏，沿用已验证同身份容量 2，不全局放大。
- 缺失语言采用来源语言，显式原语言优先；兼容旧 FULL 正文 key 复用。短付费预览不可凭字数判 FULL，长完整正文的普通订阅页脚不一概拒绝。
- Message Bus UI 独立读取状态/指标/来源列表；当日计数一次分组查询替代每源请求；重采集诊断按需打开。固定水位和 MVCC 仍保留。

业务约束：保留 ARTICLE_ONCE、实质更新修订、原始正文、checkpoint、失败/延期/在途证据和真实必需频道。不关闭 WAL/fsync，不直接改业务数据库配置，不批量清空历史队列。

## 2. 整合其他本地 main 工作

一并纳入已存在的 W3 鲁棒性 A—D 改动（cleanup、准入取消、SSE、dispatcher 恢复、ACK 收尾、原身份退役），107 项关联回归通过。未运行其历史重跑或生产批量清理。其他会话的报告、Reuters 验收脚本及 changelog 保留。原始多 MB 诊断证据不作为应用镜像输入提交。

## 3. 本地验证

- 核心采集/接纳/分发/正文/存储：111 项通过。
- Site Access/身份并发/BODY/恢复/保留：81 项通过。
- Read/API/CDC/快照/协议加新增量回归：50 项通过。发现并修复 SQLite JSON 函数改变 CDC `data_json` TEXT 形态的问题，真实投影测试复验通过。
- 配置 CAS 及 Reuters 显式容量：2 项通过。
- W3：107 项通过。
- 前端：生产 build/typecheck 通过；Vitest 67 项通过；Playwright 4 项通过，1262/1559 宽度核验。
- 语义测试包括 100 次相同采集仅一个业务输入/正文任务、重启后回执收尾、变化输入只保存增量、日期/词表/接纳失效、旧 token fencing、已完成续作释放、正文对象复用和 scoped generic 公网边界。

## 4. 发布及监测记录

待统一提交发布后填写。必要正式组件：Site Access、Message Bus、Content Enrichment、Projector、API、Web、Codex Worker。使用 `scripts/ops/storage_release.py` 的完整 commit 构建和正式 Compose 更新；保留生产卷、配置与 Chrome Supervisor 实例。发布窗口单列，不计作稳态性能。

生产证据目录：`eval/pipeline-business-delta-20261027/`；发布前快照时间 2026-10-26 20:34 UTC，可用内存约 1.45 GiB，load 18.55，I/O/memory PSI 明显。此次监测位于美东盘后，不能替代盘中高峰稳定性验证。

## 5. 验收口径

以全部应执行周期为分母，逐来源/必需 unit 保留失败、延期、未执行和窗口末在途，分别给出请求启动、响应刷新覆盖与连续间隔。修正 due 归属不算执行性能收益；有 Age 的响应单列源端缓存年龄。新文章按首次列表响应到 Bus CONSUMABLE 审计统计，失败/未发布也保留；页面可见和下游领取另有含义。FULL/SHORT_FULL 标签率与真实正文完整性抽检分开，少样本不宣称稳定 >80%。

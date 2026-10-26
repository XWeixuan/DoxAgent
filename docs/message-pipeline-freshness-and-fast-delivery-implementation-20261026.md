# 有效采集周期与快速交付：实现、发布与实测

依据 `message-pipeline-freshness-and-fast-delivery-optimization-plan-20261026.md` 执行。当前为开发记录，发布版本、正式窗口和实测结果将在实际完成后填写；未开启本轮临时采集开关。

## 实现范围

1. **采集与慢候选工作解耦**：Google RSS 返回基础列表，不再等整批 wrapper 还原；只有经接纳的候选进入已有 BODY job 前置解析。四个解析执行槽、同链接 single-flight、SQLite 持久映射；验证出版商域，再复用兼容且未到复查期的 FULL。
2. **周期证明**：物理请求 ID、角色、请求/响应时点和缓存头；共享消费者引用原请求，due 纳入 Site cache key。辅助请求不计 listing，迟到任务归原 due，超期返回单列 late recovery。多 RSS URL 独立 FEED 单元，频道和 query 初始相位独立。
3. **日常接纳**：完整输入保留，持久原数组 offset 顺序让新增/变更优先；64MiB 有界解码缓存避免每片展开全文；跨周期只复用业务处理收据，保留每轮实际网络请求，复查到期、输入/版本变化不能跳过。
4. **获取成本**：Barron’s 两个公共 JSON 频道各自获取、批准 US5 出口、持久会话、四个 HTTP 槽；失败一次浏览器回退，失败 HTTP 短冷却。Yahoo/InvestorsHub/Investing 借用现有身份 origin anchor，独立 fetch 读取，不运行每轮整页广告渲染；合同或拒绝仍走原导航恢复。Investing 原始响应与当前原生 DOM 竞速，消除第二次无界 page.content。
5. **写入与占槽**：StorageLane 内 BEGIN 等待100ms，未取得写锁时释放 lane 重新排队；启动/DDL 仍用原等待预算，操作已提交后不重放。记录 BEGIN/事务/commit 分段。捕获进度首/末更新，CDC 忽略 position-only 更新；原始审计和业务投影保留。
6. **快速正文与分发**：两个实际浏览器正文执行槽；无槽时在已有 job 持久续跑，保留同一预算并从浏览器阶段继续。L2 按上游16项上限分批覆盖最多20 ticker；持久 L2 continuation 与 regex 快分发票据分开。新增消息提交后重新读取完整 Stream/member/body，审计 `CONSUMABLE`；这证明 Bus 可读，不宣称实际消费者已经 claim。
7. **源级修复**：代理 client/curl/stdlib/SDK 传输证明；坏 RSS 保存响应哈希与解析位置；Calcalist 每轮刷新 HEAD，tail 独立；IBKR SDK 历史请求时间方向、周期对账、短全局锁、源槽外文章下载、回调时点和即时交接；Worker 安全异常帧及 trace ID 关联。

保留 8核16G 基线、浏览器总8/BODY2初始预算，以及 profile、出口绑定、挑战维护独占。并发按请求单元/成本计算，不固定9 ticker，不为20 ticker建设新身份。

## 验证记录（持续更新）

- 新增首批10个专项通过：47条Google列表不解析、20个解析消费者单飞及重启映射、930旧项加1新项位于头/中/尾、辅助请求不填未来周期、SQLite争锁让出、L2 16+4、浏览器等待释放、完整提交后可读。
- 扩充至15个专项通过，包含未来收据复用/复查边界、L2持久恢复、Barron’s两个频道与浏览器回退、Yahoo待核时条目保留。
- 发布前按测试名去重的最新结果：454项通过、1项跳过、0项尚未修复的失败；原失败与修复后的重跑XML均保留，汇总见 `test-summary.json`。新增专项已扩充至23项，包含SDK实际周期请求/慢ticker隔离/开关到期、publisher别名复用与调度tick的query-plan复用/失效。
- 追加生产单次探针：TrendForce Press批准直连HTTP 200、5条新闻合同通过、404ms，作为轻量主路径，保留原浏览器回退。Google旧默认源串行配置有专门迁移；query plan按真实词表revision和source version失效。
- 同工作区科技/量子/The Fly开发已先形成 `5a0b2c4c`、`e2b308bc`，本次发布包含这些提交；相应源、BODY、预览不发布与配置测试已联动执行。

## 发布与实测

待填写实际 commit、组件版本、迁移/身份核验、临时开关 revision/有效期。部署前整合并验证同工作区其它已完成改动，保留未完成工作。浏览器 Supervisor 无需重启。

验收以全部应执行周期及逐篇真实新文章为分母，保留失败、延期、未证明、窗口末在途和长尾。Reuters真实挑战未恢复时保留在总分母；证据补链不等于访问性能提升。没有足够真实新文章时，不宣称≤10秒或正文稳定80%已经达标。

证据目录：`eval/pipeline-freshness-implementation-20261026/`。

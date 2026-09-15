# 消息总线三层去重与更新判定：本地修复交付

日期：2026-09-15。仅实施本地代码修复；生产 DB 只读取样，未部署、未清理或重放历史。

## 实施

- 共用规则在 `message_bus_v2/deduplication.py`；adapter 只提供字段、质量与身份能力声明。
- 增量增加 `message_observations`、`message_identity_aliases`、`logical_message_versions`。原始 payload/hash 留痕；完全相同采集观察聚合 first/last seen 与次数。ID、文章 URL、验证别名并列匹配，冲突或多候选不强行合并。未知 URL 能力保守降级；不删除有身份含义的 query（包括 `ref`）。
- 补全任务按业务字段 fingerprint + 来源身份 + 30 分钟复查窗口幂等，而不是全 payload。发布时间、正文、摘要、可信版本字段变化仍进入处理；排除 thumbnail/transport 等噪声。已消费凭据使用该来源自己的观察输入，避免跨源合并后反复补全。未完成发布不能被当作消费凭据。
- 正式正文 fingerprint 不包含 URL/source/时间。只接受通过质量检查且具有辨识度的完整正文，辅以上下文冲突检查；不对摘要、相同标题、近似正文实施通用硬去重。不新增模型或相似度闸门。
- 所有逻辑版本预约、来源关联及重复判断在现有 `BEGIN IMMEDIATE` 事务中；重复复用 canonical Raw。现有 Standard/Stream 原子发布与 Raw 恢复路径保证并发/重试只发布一次。
- 正文/摘要/空内容分开保存在 `metadata.content_evidence`，原始 URL 独立保留；展示正文继续 fallback。空 tick、摘要消失、URL/发布时间技术变化不制造业务版本。相同正文的质量升级更新证据索引，不额外发布。
- 摘要补正文有新增信息、真实标题/正文变化或可信出版商修订，产生关联业务版本；`content_updated` 流项携带逻辑 ID、业务版本与前一 Raw，Runtime envelope 保留关联并走现有消费链路，不仅更新 DB。摘要级明确修订保留此前正文并明确标为旧上下文，不把旧文当成当前修订。
- 旧 Raw 按既有身份/版本惰性索引，不全量改写历史；旧消息 ID/hash/Runtime 关联保留。合法 closed sweep 对未发布 Raw 的接管路径保留。

## 三个来源

### Reuters

真实 DOM 提取停止在单篇卡片，发现多个不同文章链接立即停止扩张。摘要只读取明确 description/summary 或卡片段落；纯日期/相对时间不作为摘要。URL 日期优先，卡片日期冲突留痕；相对时间保存估计时间，不冒充精确发布时间。搜索列表/导航正文被拒绝，401 保留失败状态。

### IBKR

支持历史接口 `%Y-%m-%d %H:%M:%S.%f`。Gateway 原有实时/历史两条路径均调用 article API 取得内容，保留该机制；失败的空 tick 在总线复用已有正文。通用 `providers.php` 标为 generic，不作文章去重键，也不发起网页补全。

不建立未经验证的 `$` 后缀全局映射。不同 Dow Jones 频道 ID 在共用完整正文匹配确认后关联同一逻辑消息；同后缀不同内容继续独立处理。原生正文不因 URL 补全失败而失去完整正文资格。

### Yahoo

NCP/Search/proxy 的 UUID 声明为文章身份；原始 payload、URL、摘要缺失状态均保留。路径切换不清空旧摘要或制造正文降级版本；独立出版/身份冲突继续处理。Publisher/重定向验证结果可提供 URL 别名。

Reader 请求统一为 `https://r.jina.ai/` 加原始 `http(s)://...`，不再产生 `http://https://...`。正文识别同时拒绝导航列表，HTTP 成功与长度不单独作为有效正文依据。

## 验证

- 定向验收覆盖并列 ID/URL、不同 ID 同稿、ID 复用不同稿、ticker 边界、完整正文跨源匹配、同标题不同稿、直播更新、正文降级、摘要补正文、可信短修订、来源输入凭据、并发和失败恢复。
- 真实 Chromium 本地 DOM 测试验证相邻 Reuters 卡片、没有摘要的卡片及相对时间；没有发起网站抓取。
- 原有正文队列、三个新闻 adapter、原始 URL/补全身份、历史版本及 Reader fallback 回归检查通过。最终完整定向运行 47 项通过；未知 URL 能力降级调整后另做核心定向复验。
- 44 条真实生产 Raw 只读取样，在临时本地 SQLite 中回放，不访问正文网络、不触发生产 Runtime。结果：Reuters 12 条中抑制 9 条、保留 1 次标题变化；IBKR 12 条同标题样本抑制 10 条、保留 2 个实际内容版本；Yahoo 20 条保留 12 个首次消息及 6 次正文补充，抑制 2 条重复。
- 回放不代表当前线上抓取成功率，也不代表所有相同标题都重复。历史重复仍然保留。

## 上线边界

本轮没有 git push、远端 build/restart 或历史清理。上线后需小范围观察消息发布增量、来源关联、正文质量和 Runtime 更新关联；真实正文网络可用性尚未重新验收。没有修改 O1 编排或任何 skill。

## 2026-09-15 部署与附加检查

- 代码提交 `7b9de5eb` 已推送；新加坡远端使用两份既有 Compose 配置 fast-forward 拉取，重新构建后端共享镜像及前端镜像、重建默认服务。API、前端与 Codex worker 健康检查通过。
- 附加检查确认调度器在旧镜像、部署前已连续出现 Docker `oom` / `exitCode=137`，512 MiB 上限阻止每日维护推进；交易日 2026-09-14 的每日任务在 2026-09-15 06:00 UTC 创建后仍 Pending。保持 CPU、并发和编排不变，将调度器内存与含 swap 总上限同时有界提高至 1536 MiB，待复查。
- 部署前 IB Gateway 已通过只读 API 握手、账户可用及服务时钟检查。Reuters 搜索 HTTP 401 从 2026-09-14 15:56 UTC 起已存在，本次发布不宣称解决外部访问失败。
- 未清理历史数据，未纳入其他对话尚未提交的 O3 prompt/skill 改动。
- 临时 1536 MiB 调度器仍 OOM，每日任务没有推进；撤回该无效配置，保留原部署边界。此项检查未通过，不能宣称 O2/O3 正常；记录为 `FRONTEND_INTEGRATION_ISSUES.md` 的 BE-15，需单独后端修复。
- 重启后 Finnhub、Benzinga、IBKR、Yahoo 连续轮询成功，TikHub 10 分钟周期也成功；Reuters 仍为既有 HTTP 401。新增去重表已创建，但观察期间没有通过 freshness 的新增稿件，因此没有生产去重/内容更新样本，不能以零新增证明线上去重效果。
- 重启后 Gateway 再次通过实际只读 API 登录/账户/时钟检查；未修改 Gateway 配置或重启 Gateway，也未提交订单。

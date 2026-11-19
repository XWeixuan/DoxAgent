# Reuters 正常恢复、终态一致性与三身份均摊

## 冻结边界

- 随附正文准入方案的实际规则是每 ticker 仅第一个 L1、无 TAIL、QUERY 仍 60 秒。生产已是 10 QUERY / 0 TAIL；本轮不再改频率或重建业务队列。
- 现有 Main 为 `reuters-backup2`，Backup1 为 `reuters-backup`；新 Backup2 使用 `reuters-active-3`，独立空 Profile、正式 Chrome 153、相同固定浏览器环境。
- 第三身份用于当前健康服务的正常容量分摊，不用于替换受阻身份以绕过验证；遇到验证沿现有流程暂停该身份。不复制 Cookie、账号或已通过验证的浏览器存储，不额外购买打码任务、不关闭原页、不重建 Supervisor。

## 1. 已确认修复先发布

1. `GovernanceRepository.save_recovery` 的 AUTO→HUMAN/人工原因分支必须排除 `phase=RESOLVED`。清理回写不得重新创建 ChallengeTask。保留失败中的人工升级和独立原页所有权。
2. 有界 WireEvidence 正确合并先到的 ExtraInfo（包括没有 DD Cookie 的情况），区分缺少证据与明确未携带；请求结束清理暂存。识别 memory/disk/prefetch cache，保留已有六请求/八暂存上限。
3. 仅发布 Site Access，验证正式镜像 SHA、原 Chrome instance、Profile 和 Supervisor 未变。旧历史任务不批量删除或伪造完成时间。

## 2. 非付费恢复的证据和实验边界

历史成功不是单变量实验：新上下文、Device Check、Cookie 转移、fetch 和 reload 混合，不能归因于某一个动作。已有普通同 Profile fresh page 产生网络 HTML/API 200，Cookie 指纹不变；但缺少该指纹此前实际被拒绝的连续证据。它只证明无需每次更换 Cookie，不证明主动解除了验证。

当前两条身份已自然恢复，不能主动制造验证作为对照。继续被动观察真实业务：记录同一 identity/instance、页面根请求及搜索 API、权威 Cookie 指纹/blockedReasons、cache 类型及响应状态。只有先确认相同请求上下文的真实网络拒绝、随后普通恢复操作、首次真实网络成功，才能形成完整时间链。缺少自然阻断时，将 Challenge→200 因果实验列为未覆盖；不将正常页面再请求 200 当成实验成功。

不把 Cookie 转移、新身份更换或自动 CAPTCHA 交互纳入恢复流程。保留既有普通恢复探测的预算、冷却和人工出口。

## 3. 三身份最小实现

复用 `verification_plan`，不增加上层严格 DTO 字段或第二个调度器：

```yaml
crawler_routing: active_pool
body_routing: active_pool
additional_active_identity_ids: [reuters-active-3]
max_parallel_concurrency: <原两身份合计，最多4>
min_parallel_interval_ms: <原两身份合并节奏，不因增加第三身份加速>
```

- 一个公共成员解析函数，扩展 ScopeResolver、独占 owner、worker 快照、恢复成员检查、人工维护列表及页面打开检查。仅 Reuters 允许第三成员，身份和 Profile 必须独立；依旧禁止带活页/人工现场的成员被移出。
- CRAWLER 与 BODY 均使用现有 `JointBudget.route_members`，选择一次、只执行一次任务；共用现有物理许可、公平与站点限流。没有复制 ticker 计划，也没有重复发现循环。
- BODY 以自身鉴权、能力证明和当前可用性筛选，不借用列表 READY 或其他身份的登录/正文证明。新身份的受限正文等待正常人工登录及验收；不伪造 FULL。
- 三身份均参与长期驻留；停止/维护/验证中的身份不参与选择。单身份可用时回到其真实并发与节奏，不将全部欠账突发灌入；已有保护期和平滑升载继续生效。
- routing-only CAS 变更保留既有两身份的能力版本，不清理其会话；新成员从无证明开始。Legacy dual_active 继续保持原 CRAWLER 行为。
- 新配置脚本默认 preview，通过 Owner API/CAS 创建空 Profile、独立 identity 和源范围，不直接改数据库。回退使用相同 CAS；移除成员前须释放其活页/维护归属，不强行删除现场。

## 4. 回归与真实验收

本地覆盖：三成员列表/正文均摊、不重复执行、route generation 不抖动、第三成员失效跳过、总并发/节奏不扩张、原能力指纹保留、第三身份不能继承登录证明、带原页成员不能迁移、旧双活及维护流程兼容、BODY 准入公平。

生产先记录两身份基线，再发布 Site Access 并注册第三身份，保留 Main/Backup1 instance。列表主要使用自然业务；无新正文样本时，对少量既有文章做普通准入下的受控BODY验收，单列而不发布为新消息，准入延期仅做一次有界重试。保存精简账本快照与有界被动网络观察；观察器自身不增加新闻站点请求，不连续截图或完整抓包。

按相同自然窗口对比每身份列表/正文请求数、网络/缓存结果、实际 QUERY 覆盖和间隔、队列/准入延期、FULL/SHORT_FULL、独立新 Challenge、资源。总成功回执不等于新网络访问；无新正文时明确正文验收不足。两身份在加第三身份之前已经持续恢复，是重要混杂变量，短窗无复发不能单独证明三身份降低风控率。

## 5. 执行中真实现场追加的窄修

- 第三身份的原页SSR被误记为搜索READY：原页观察必须交由已有真实recipe/API probe证明，人工Verify同样不使用无API证明的原页捷径。保留实际空搜索结果。
- 普通正文Verify选中了`/world/`栏目页：排除Reuters非日期型文章入口，复用既有结果的真实文章，不增加发现循环。
- 多站点真实队列空转：延期BODY软资格没有attached协程，不能赢得全局物理许可。保留年龄及同身份正文排序，允许其他就绪身份工作；不扩大容量。
- 不能只减逻辑任务而重复填充三套相同查询缓存：复用现有路由器加最多128项纯内存QUERY归属，正常4/3/3、故障/明显拥塞时借用，BODY保持动态。无新表、队列、Worker或定时器，重启后重新学习。

真实结果、逐版本窗口、原始错误和未覆盖分支详见 `docs/reuters-normal-recovery-three-chrome-implementation-20261120.md`。最终代码200dffc2d，相关13源码哈希与生产一致；不将后续纯文档提交误记为运行镜像漂移。

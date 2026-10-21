# 消息源安全验证分流与自主恢复：实施记录

依据：`dev_plan/workflow_v2/site_access_challenge_classification_autorecovery_implementation_plan_20261021.md`。

## 实现边界

不解验证码、不改变浏览器身份/出口，不增加发布入口，不改变正常 24 小时/60 秒轮询、30 分钟新闻窗口、去重与分发。恢复探测只提交能力证明，不发布消息、不推进业务 checkpoint、不调用 Jev。

### 已实现

|方案范围|实现|
|---|---|
|证据分类|`challenge_detection.py`：AUTO_CHECK / INTERACTIVE / ACCESS_BLOCK / UNKNOWN；供应商识别不冒充交互识别；只读取已加载且可见的供应商 iframe；隐藏控件/脚本不作为人工题；UNKNOWN 保留原始事实，再执行站点策略|
|持久恢复|`BrowserRecovery`、`browser_recoveries`：每身份一个当前事件，租约、generation、配置指纹、实例检查、受影响能力上下文；自动项不创建人工 Task|
|页面交接|RECOVERY owner；精确 target/instance/allocation；close_claim 原子检查，旧 BUSINESS finally 无权关闭已交接页；原页确实丢失后，新探测可留下新的真实人工现场|
|恢复协调|原页轻量观察；到期隔离业务探测；AUTO 60/120/300/600 秒退避；人工待办也可自然恢复；全局最多两个动作、每身份一个；OPEN 人工会话禁止新导航，RELEASED 不阻断后台|
|能力独立|同身份真实 CRAWLER/BODY 分别证明、分别解除门禁；成功列表不证明订阅正文，登录表单消失不证明认证；不存在的旧页永不伪造 LIVE|
|维护|Verify 外层 105 秒、内部独立阶段；丢原页可执行同身份真实业务验证；清理失败不回滚已提交健康，也不永久占 Viewer；ViewerReady 前核实实际 target|
|上游|RECOVERY_PENDING；协议 3 返回新字段，旧协议降级成 BUDGET_DEFERRED；采集延期保留原失败计数；正文恢复延期不消耗失败尝试次数，但仍受原业务过期时间约束|
|认证时间|UNKNOWN 不刷新最后明确认证时间；单独保留 last_check_at/result/reason；维护 inventory 分别提供认证确认、认证检查、恢复真实探测时间|
|资源|Managed 恢复页保护 context；External 控制连接回收不等于关闭 Chrome；原页 TTL 与整个身份寿命分开；有界截图及一次低细节补拍；恢复行 7 天、截图既有 14 天/256 MiB；Supervisor 最近退出摘要及结构化停止原因|
|迁移/回滚|显式 dry-run/apply；保留 Profile/Egress；OPEN 用户延期移交、释放后跟进；持久迁移标志不受日志保留删除影响；rollback-export 只输出旧 schema 投影，不直接改生产库|
|全源契约补齐|CTEE 使用实际分类 API 验证而非栏目首页；复用实际 JSON 列表解析器，兼容 Chrome JSON `<pre>` 包装；Reuters/Yahoo 保留既有 recipe，其他列表使用各自实际解析器|

新增主要模块只保留 `challenge_detection.py`、`recovery.py`；其余沿用现有 Registry、Owner、Worker、Supervisor、维护 UI 与正文队列。

### Reuters 普通自动登录（可选）

`AuthPolicy.auto_login` 默认 `false`。已实现明确 REAUTH_REQUIRED 后的普通表单恢复路径：沿用当前身份/Profile、受保护 CredentialStore、短命 Worker 管道传递，拒绝非 Reuters 域及外域 form action；同一 credential 主备共享 30 分钟尝试间隔；遇到 Challenge/MFA 停止。提交表单后再由独立业务探测证明恢复，不据“提交成功”更新 VALID。

目前只在已有恢复事件明确检测到掉号时适用，不是持续扫描所有 Cookie 的自动登录平台。账号密码不写 Git、审计输出、截图元数据或普通配置。未实测普通表单成功前，不宣称自动登录生产可用；也不把它当成 DataDome 滑块解决能力。

## 验证与发布记录

- 已有离线回归组：203 passed；双轨/维护补充组：73 passed、1 skipped。分组有重叠，不能相加当成独立测试总量。最终候选还需执行增量测试。
- 已检查当前源码未定义变量/未使用导入：`ruff --select F,E9`。
- 生产发布前重新读取：根盘约 51 GiB 可用；7 个未结束历史 Challenge；79 个未终结 target 记录（记录数不等同于实际活页数）。无 OPEN 人工维护会话。
- 已在 root 私有目录 `/var/lib/doxagent/challenge-recovery-20261021/before/` 保存 Registry 在线备份和既有截图。脱敏清单：`eval/challenge_recovery_implementation_20261021/pre-release.json`。
- 用户明确允许重建 Supervisor，保留 Profile 并将实际失去的旧页标记 TARGET_LOST。重建必须同时更新 Site Access 的共享网络命名空间，不能把它留在旧 namespace。
- 最终发布与真实回执见下节。离线测试或容器健康不等同于全源恢复。

### 2026-10-22 窄发布及初步业务回执

已推送 `origin/main`，生产按组件镜像归属发布，不统一重启全部服务：

|组件|实际源码版本|镜像 digest|
|---|---|---|
|Site Access|`1ef3f8b7`|`sha256:ff5b579993c4e71c06bf79515993e8f182bfac7beab2c9984fcf7ea63691c3a2`|
|Chrome Supervisor|`667031ea`|`sha256:8d3c6b9d8e64f283b38eee4e55f29cb6f0e134c852780a6bf58f4d3c7ad5421d`|
|Message Bus|`c37c04c7`|`sha256:e9fa01e07f6bd924315f4d75182f6f5991ec81d2f03fc3fb6cec65d8b16a3edd`|
|Content Enrichment|`667031ea`|`sha256:616efb325378e668232f0297375970c00de6ad968d810bac7288aa98d575d5e4`|

Supervisor 经批准在 UTC 2026-10-21 16:28:45 重建一次；Profile/Cookie 卷不变，旧原页按实际不存在记录 LOST/TARGET_LOST。此后跟进只更新 Site Access 或 Message Bus，未再次重建 Supervisor。最后 Site Access 启动时间为 UTC **2026-10-21 17:27:49**（北京时间 10 月 22 日 01:27:49）。network guard 确认 Site Access 与当前 Supervisor 同在 `net:[4026532329]`。四组件按各自版本核对 31 个相关源码文件，均无哈希差异。桌面工具 inventory 为 READY、非 stale，Viewer 服务可用；这不冒充逐站人工登录成功。

显式迁移首批历史阻塞为 **6 个 AUTO、1 个 HUMAN**，没有新建 Profile 或修改出口；仅修正验证策略与认证时间不确定性。迁移后实际暴露并补齐了 CTEE 旧备用验证入口错误、Investing BODY 旧验证地址实际是 ticker 列表、恢复请求丢失 Source Scope、CTEE 未转换延期异常等内部路径。还修复恢复/冷启动与业务的浏览器写入互斥、BODY 误冻 CRAWLER、迟到失败重新冻结，以及人工 Verify 成功未同步关闭恢复门禁的问题。

最新完整离线组：**216 passed，1 skipped**；`ruff --select F,E9` 通过。该组覆盖分类、精确页面所有权、自动/人工恢复、迁移、旧协议、双轨 Runtime、维护桌面、正文队列和行业源；与此前分组有重叠，不相加。跳过项不冒充生产证明。

初次发布后的自然窗口（UTC 16:28 起，截至约 17:27，**不是最终版本 24 小时窗口**）：

|站点|自然业务记录|验收边界|
|---|---|---|
|Barron’s|381 次 CRAWLER SUCCESS；2 条实际正文 FULL；历史自动阻塞一次探测解除|列表与正文独立成立；不据此宣称长期无复发|
|Investing|251 次 CRAWLER SUCCESS；1 条 SHORT_FULL；主身份与备用的自动恢复已分别 RESOLVED|窗口仍有 2 次 CONTENT_ERROR；发布连接错误需等自然轮询刷新，不能掩盖|
|CTEE|46 次 CRAWLER SUCCESS；1 条 SHORT_FULL；主备历史恢复均 RESOLVED|备用旧验证入口修复后第 4 次实际探测恢复|
|Reuters|仍有 HUMAN 恢复事件；旧原页 TARGET_LOST；后台继续到期探测|未恢复，不宣称 Challenge 已通过；未自动填写用户凭据|
|InvestorsHub|备用仍 AUTO/BACKOFF，当前绑定延期|不放入人工 Challenge 列表，也不伪造恢复；继续真实探测|

最终版本后的自然回执中，TrendForce 两入口、DIGITIMES English、CTEE、Barron’s、GlobeNewswire Search 及半导体 RSS、Yahoo 等继续出现真实成功；与本次无关的普通 Newswire RSS 单项 ParseError 保留原事实。各 source/ticker 完整状态保存在脱敏 `eval/challenge_recovery_implementation_20261021/acceptance-final.json`，不能只看 `status=succeeded` 而忽略当前 error/last_success。

24 小时验收从最终版本时间起算，截止 UTC **2026-10-22 17:27:49**（北京时间 10 月 23 日 01:27:49）。已安排本聊天每两小时只读检查，完整时间窗统计业务/正文/恢复事件及资源，仅重要变化或最终结果通知；不额外请求网站，不修复、重启、切换身份或解验证码。未满窗口前，交付状态是“代码与窄发布完成，持续验收中”，不是全源长期验收通过。

### 可选自动登录的当前边界

Reuters 普通掉号自动登录代码与离线表单/域名/账户节流测试已完成，**生产仍为默认关闭，未保存本次提供的账号密码**。当前实际未解除的 blocker 是待判定/人工安全验证，不能把提交账号密码当成解决 DataDome。只有既有恢复事件明确产生 REAUTH_REQUIRED、受保护凭据已配置并显式开启时，才尝试一次普通登录；遇到验证码或 MFA 停止，之后仍须独立业务证明。当前没有持续全站 Cookie 巡检或经过真实成功登录的生产自动登录验收。

## 验收限制

1. 只有运行至少 24 小时的自然轮询，才能完成方案的长期验收；观察窗无自然验证码不能宣称所有真实验证码分支已验证。
2. UNKNOWN 是证据不足，不是准确识别了题型；Reuters UNKNOWN 按人工策略，其余首批按自动恢复策略。页面拒绝持续存在时保持退避，不保证第三方放行。
3. 首帧保持不可变；小 PNG 只能标记低细节疑似，补拍不能保证消除 GPU/CPU 导致的白图。
4. 自动恢复与账号/订阅/解析/网络错误分开。授权不足、正文非 FULL、非正常解析不会被“没有验证码”掩盖。
5. 回滚必须停止新 claim、等待旧 lease，验证数据库副本的旧 schema 投影，并统一回退消费者；不能直接让旧镜像读取新 JSON。

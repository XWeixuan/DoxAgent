# 消息源安全验证分流与自主恢复实施方案

日期：2026-10-21。方案基线：main `2f9ddcce58b3e0f4a5d766449993c667ae08f5d9` 与本轮生产只读审计。当前工作区其他会话已有未提交修改，实施时须保留并协调重叠文件；本文件不授权自动提交其他会话改动。

本方案是可直接执行的开发任务书。本轮只制定方案，不实施代码、不迁移生产状态、不部署、不重启、不发起网站验证。

## 一 目标与必须明确的边界

将“自动安全校验”从人工 Challenge 待办中移出，为所有受 Site Access 治理的浏览器采集和正文任务建立统一的自主恢复闭环；真正需要交互的 Challenge 仍保留人工原页，但其待办、页面是否丢失、业务是否恢复必须分别治理。

交付后的基本行为：

1. Investing 的 Cloudflare 自动校验、Barron’s 的 DataDome Device Check 等进入后台恢复，不进入 Site Login Maintenance 的 Challenges 页，不要求用户反复点 Verify。
2. Reuters 滑块进入人工待办，同时后台持续检查是否已自然恢复；恢复即解除业务阻塞，不以人工是否点击 Verify 为前提。
3. 原页丢失不再成为业务恢复的终止状态。保留丢页事实，允许同一 Identity 的隔离业务探测建立新的可用证明。
4. 所有处理复用现有 Managed Playwright、External Chrome/CDP、Identity Worker、Registry、站点 recipe/parser、Fallback，不另建浏览器平台。
5. 正常采集仍按现有 24 小时轮询和各入口配置运行，不把正常 60 秒轮询改为交易时段轮询。故障恢复探测独立退避，避免多个 ticker 同时冲击一个身份。

两个不能混淆的承诺：

- 保证自动校验不会被内部永久人工门禁锁死，持续尝试自主恢复；不能保证网站始终拒绝、网络中断或机器无资源时仍能访问成功。持续失败要显示准确原因，不转嫁成“请人工解一个没有交互控件的题”。
- 不承诺仅靠截图百分之百识别验证码子类型。DOM、响应证据、实际交互控件与站点策略共同决策；识别不确定必须保留 UNKNOWN，不编造滑块类型。

治理粒度采用 `scope + identity`，恢复证明保留具体入口、ticker/query 和 purpose。不同 ticker 仍共享该消息源的 Identity，不为每个 ticker 新建 Profile。同一 Identity 的故障不能靠修改一个 ticker 的健康位消除。入口级差异以验收覆盖和失败上下文表达。

### 本轮不做

不接入打码平台，不自动拖滑块/点验证控件，不伪造验证 token，不做 stealth/随机指纹，不新建代理轮换系统，不自动填密码，不重构 Message Bus 全部源配置，不上线维护 Codex Worker，不改正常新闻时效窗口，不抓历史消息补足停机期，不扩张为完整 Web 管理后台。

## 二 本轮证据对应的具体修复对象

完整依据：[Challenge 现场留证与自动恢复机制审计](../../eval/challenge_recovery_audit_20261021/findings.md) 与 [逐图清单](../../eval/challenge_recovery_audit_20261021/image_inventory.md)。统计快照为北京时间 2026-10-21 18:07，不代表实施时的实时生产状态。

|证据|实现层问题|本方案动作|
|---|---|---|
|50 组记录、64 张 PNG，44 张纯白|截图早于渲染，分类与截图不同时|同一采样的结构化证据加补拍，不把白图当最终判断|
|Barron’s 显示 Verifying the device|自动设备检查进入人工流程|识别 AUTO_CHECK，交给后台恢复|
|Investing 26 条 AUTO_CLEARED、13 条事后自动恢复事件|已有有效能力，但只适用 Investing 的特定原页|提取为通用协调器，删除站点名硬编码门禁|
|人工标记在 cooldown 到期后仍阻断|人工待办与恢复准入混在一起|独立恢复准入，不靠人工标记自动过期冒充恢复|
|TARGET_LOST 从 Inspector 循环被排除|丢页无恢复出口|页面存在性和业务恢复分离，丢页改走新页探测|
|Investing target=LOST 仍返回 ViewerReady|实际页面、持久状态和界面回执不一致|精确目标重核对，错误分层，不虚报打开成功|
|Reuters 原页检查超时中止 Verify|原页成为业务验证硬前置|原页观察、能力探测和结果提交分阶段|
|UNKNOWN 认证沿用 VALID 并更新 verified_at|登录状态时间戳虚新|保留最后明确证明时间，单列本次检查结果|
|4 CPU、load 约 22；并非 quota throttling|资源竞争可能放大等待和截图超时|后台恢复有界、资源观测、禁止九 ticker 并发恢复|
|Supervisor 重建丢活页，且 stop reason 未持久保留|现场生命周期证据不足|记录实例/目标生命周期，发布时显式处理活页|

审计中未证实“广告或登录弹窗就是 Challenge”。也未证实 Reuters 掉号和 DataDome 之间的因果方向，不能把这些假设写成修复前提。

## 三 总体结构

```text
浏览器正常访问
  → 页面观察与既有业务解析器
  → VerificationAssessment
       ├─ NORMAL / AUTH / ENTITLEMENT / TRANSPORT：原有对应能力路径
       ├─ AUTO_CHECK：BrowserRecovery，不创建人工 Task
       ├─ INTERACTIVE：ChallengeTask + BrowserRecovery
       ├─ UNKNOWN：站点策略决定处理方式，未知事实仍保留
       └─ ACCESS_BLOCK：后台退避/既有 Fallback，不伪装成可人工解题

BrowserRecoveryCoordinator
  → 只读观察现有页
  → 需要时取得专用恢复准入，以同一 Identity 新建隔离探测页
  → 用同一 recipe/parser 证明具体业务能力
  → 原子提交恢复结果、释放门禁、安排正常轮询
```

保留两个不同对象：

- `ChallengeTask`：只有需要或按站点策略暂按人工处理的任务，负责人工待办及原页引用。
- `BrowserRecovery`：后台恢复状态，一身份最多一条当前记录，自动与人工两类均可关联；永远不占全局 Viewer 席位。

不要把自动校验伪装成隐藏的人工 Task，也不要靠隐藏 GUI 条目而继续保留旧人工门禁。

## 四 类型识别与处理策略

### 4.1 统一观察结果

新增 `challenge_detection.py`，将 `challenge_evidence.classify()` 里的纯识别逻辑提取到这里；旧函数保留兼容包装，既有留证调用不全部推倒重写。

定义 `VerificationAssessment`：

|字段|取值或含义|
|---|---|
|`provider`|CLOUDFLARE / DATADOME / HUMAN_PERIMETERX / HCAPTCHA / RECAPTCHA / UNKNOWN|
|`kind`|NONE / AUTO_CHECK / INTERACTIVE / ACCESS_BLOCK / UNKNOWN|
|`interaction`|SLIDER / PRESS_AND_HOLD / CHECKBOX / IMAGE_SELECTION / NONE / UNKNOWN|
|`confidence`|HIGH / MEDIUM / UNKNOWN|
|`signals`|命中的结构化依据，不写 Cookie、表单值或脚本全文|
|`observed_at`、`sample_id`|该次采样的时间和编号|
|`source`|主文档、已加载 iframe、响应头或站点策略|
|`handling_mode`|AUTO / HUMAN；由明确事实与策略合并，不覆盖原始 kind|
|`policy_reason`|如 explicit_slider、device_check_text、site_unknown_policy|

业务文档是否可用仍由既有解析器决定，不能由“没有匹配到验证码字符串”替代。

### 4.2 识别优先顺序

1. 先确认是正在阻断目标业务的活跃验证文档或控件。正常正文提到 Cloudflare、HTML 加载了验证码脚本、HTTP 403 本身，都不是人工题证据。正常页中无阻断作用的普通 widget 不冻结整个消息源。
2. 已加载页面上明确的滑块/按住/图片选择/可操作人机勾选，判 INTERACTIVE。控件必须有可见性及可操作证据；不只匹配一句 verify you are human。
3. 验证文档的“Verifying the device”“Performing security verification”“Checking your browser”等，与供应商/活跃文档证据合并后判 AUTO_CHECK；已显示自动加载动画不能直接判人工。
4. cf-mitigated: challenge 可以证明 Cloudflare Challenge 响应，不能单凭它确定是否需要人工。DataDome iframe 也只能先证明供应商，不能直接等于滑块。
5. Access denied/temporarily restricted 等明确拒绝页单列 ACCESS_BLOCK，不显示“请完成验证码”。
6. 无法确定类型保留 UNKNOWN；等待、重采样后再依据站点策略处理。自动验证随后出现真实交互控件，可以升级为人工，不能永久假定 Cloudflare 永远自动通过。

同一快照同时出现“正文已可用”和旧验证残留：由实际业务解析成功、目标域名/URL 正确、文档不再被阻断共同裁决；记录过渡，不单靠旧响应头继续冻结。

### 4.3 iframe 与截图的边界

Managed 使用已有 Page 的可见 frame；External 使用已存在的控制连接或原生只读 CDP 观察。仅读取已加载且在供应商允许列表内的 frame，最多 3 个、合计 2 秒；不主动请求 iframe URL、不绕过浏览器同源机制、不点击验证码、不注入环境修改。没有可用 frame 文本时保留 UNKNOWN。

不把跨域 iframe 读取成功作为恢复前置。首批靠本轮真实 DOM/metadata fixture 和站点策略即可落地，避免为了细分某一题型扩张成新的视觉服务。

截图作为审计和人工核对，不进入在线自动视觉模型判题。本轮不引入 LLM/OCR 依赖。测试截图可以帮助确定人工标注，但程序的 UNKNOWN 必须如实保留。

### 4.4 Registry 配置

在 `SiteStrategySpec` 新增 `challenge: ChallengePolicy`，并传递到 `ResolvedSite` 和 Identity Worker 的策略快照。不要只改 seed 而漏掉 resolver/IPC。

```yaml
challenge:
  unknown_handling: auto           # auto 或 human
  automatic_wait_seconds: 60      # 初始后台观察窗口
  recovery_enabled: true
```

初始策略：

|站点|UNKNOWN 默认|已明确识别的事实|
|---|---|---|
|Reuters|human|Device Check 明确为自动仍走 AUTO；滑块走人工|
|Investing|auto|Cloudflare 自动页后台恢复；出现真实交互控件才转人工|
|Barron’s|auto|本轮 Device Check 自动恢复；未来滑块/按住不能被站点默认值压成自动|
|CTEE 与其他已注册网站|auto|明确人工控件优先于默认策略|

这不是给网站固定贴一个永远不变的验证码标签。`unknown_handling` 仅解决类型无法可靠识别时的路线；明确证据优先。

暂不新增每 ticker 策略表。若真实证据证明同源入口稳定呈现不同机制，可在已有 `SourceAccessScope.verification_plan` 增加入口键的 `challenge_overrides`，由 adapter 传入稳定 `entry_key`；ticker 可作为其一部分，但不得改变共享身份的单写者、总并发和原页所有权。首批无证据就不写这些 override。

## 五 后台恢复对象与持久化

新增 `recovery.py`，包含恢复模型及协调器；持久化方法放入现有 `GovernanceRepository`，不创建第二个 Registry。

### 5.1 BrowserRecovery

关键字段如下，允许实现时用嵌套 model 组织，不为每个字段建表：

```text
recovery_id, identity_id, scope_id, site_id
generation, phase=OBSERVING|BACKOFF|PROBING|RESOLVED
assessment, handling_mode, challenge_task_id?
original_allocation_id?, original_target_id?, original_instance_id?
page_status=PRESENT|LOST|UNKNOWN
opened_at, updated_at, next_due_at, resolved_at?
attempt_count, last_real_probe_at, last_error, resolved_by?
affected_contexts[], verified_contexts[]
lease_owner?, lease_until?
```

`affected_contexts` 按 purpose 与入口保存最少可重放请求：原 source、ticker/query、recipe_ref、recipe_parameters、受控 URL、scope/strategy revision。CRAWLER 与 BODY 各优先保存当前失败样本；总数最多 8，超出仅累计计数与最近样本。恢复成功后由正常轮询扩展覆盖，不要求先遍历全部 ticker 才恢复。

URL 的展示/日志仍移除 query；实际恢复请求的 query 需要保留在私有 Registry 中，且从原 AccessRequest 白名单提取，不能复用已经脱敏而丢掉 query 的 original_url。禁止持久化 Cookie、Authorization、密码或任意网页脚本；恢复不支持任意 shell/任意 API 调用。

### 5.2 表与索引

在现有 `migrate_governance()` 增加：

```sql
CREATE TABLE IF NOT EXISTS browser_recoveries (
  identity_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL,
  phase TEXT NOT NULL,
  next_due_at TEXT,
  generation INTEGER NOT NULL,
  data_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS browser_recovery_due
  ON browser_recoveries(phase, next_due_at);
```

当前身份一行，下一次独立故障生成新 recovery_id 并替换已解决行；已解决历史由受限事件和留证保存，不另建无限增长的 attempts 表。

claim、提交、转人工使用短事务和 generation CAS。唯一有效结果必须匹配 recovery generation、scope/strategy revision、当前身份配置及实际 browser instance；旧 Worker 的迟到回执不得解除新一轮阻塞。网络/浏览器操作不放 SQLite 事务中。

lease 默认 100 秒，失效后可由下一轮调度重新 claim 并递增 generation；phase=PROBING 不能成为重启后的永久跳过理由。进程重启不清 attempts、不重置 opened_at。disabled scope 不主动探测，重新启用后恢复到期调度，不把停用期间判成 Challenge 或成功。

### 5.3 人工任务与页面状态解耦

保留现有 ChallengeTask 状态，以减少 API/GUI 迁移风险；补充 `resolution_reason`、`resolved_by` 与可选 `recovery_id`。TARGET_LOST 只表达该人工原页已丢，不再隐含“永远不能证明业务已恢复”。

- 新页在同一 Identity 证明对应能力恢复时，可将旧任务 RESOLVED，resolution_reason=BUSINESS_RECOVERED_ON_NEW_PAGE，并保留原始 target 及丢失事件。
- Fallback 恢复只能更新路由及备用身份能力，绝不能将主身份旧 Task 标成已解决。
- 自动校验迁移时关闭旧人工 Task 的原因是 RECLASSIFIED_AUTO_CHECK，不是虚构 CAPTCHA_SOLVED；BrowserRecovery 仍处于活动状态直到实际验证成功。

## 六 页面所有权和准入修复

### 6.1 增加 RECOVERY 所有者

`OwnedBrowserTarget.owner_kind` 增加 RECOVERY。自动检查的活页从 BUSINESS 原子交接至 RECOVERY，不能走 `handoff_challenge()` 设置 Profile 维护态；只有人工路径才创建 ChallengeTask。

同一 Identity 最多保留 1 个自动观察页及 1 个人工原页。若已有相同恢复事件，后续 ticker 仅合并失败上下文，不再留一堆 Chrome 页。新建业务探测页与自动观察页复用同一页预算；人工原页永不被自动探测重载或导航。

所有权交接必须发生在业务 finally 释放和关闭页面之前，且包含 target state=LIVE、instance、allocation、owner=BUSINESS 的比较更新。持久交接确认后，Worker 才将页面从普通清理列表移除。失败则不发布人工 ViewerReady，进入 OWNERSHIP_HANDOFF_FAILED/后台重核对；不能拿已 LOST 的 target 交接成貌似可用的任务。

`targets.py`、External 的 `_reconcile_targets()`、Managed 页面收尾均更新为：

- 仅清理当前连接确实拥有的 BUSINESS / CLOSE_PENDING 目标。
- 对 HUMAN 原页和 RECOVERY 观察页，只有明确释放/恢复完成/规定的自动观察过期才关闭。
- UNKNOWN 的传输状态不推导为 LOST。明确实例变更/精确 target 不存在才标丢，并记录 reason/source/time。
- 页面丢失的任务仍加入恢复队列，不再从全部恢复逻辑中跳过。

### 6.2 专用恢复准入不能绕过实际锁

在 `ProfileUseCoordinator` 增加 `recovery(identity, recovery_id, expected_generation)` context manager，配套 `IdentityWorkerManager` 的受控恢复命令。

它可以绕过本次 recovery 对应的人工/自动业务门禁，但必须同时满足：同一 Identity、有效 lease、没有正在执行的业务命令、没有 OPEN 人工写会话、现有 Profile 单写者锁有效。不能设置一个全局 allow_maintenance=true 绕过所有维护隔离。

有 OPEN LOGIN/CHALLENGE 人工会话时，只允许原页只读观察，不新建/导航探测页、不重启 Chrome。若观察已证明恢复，可提交恢复事实，但不得自动夺取 Viewer、关闭用户页面；实际轮询若仍被人工独占会话暂停，显示 USER_MAINTENANCE，而非继续显示 Challenge 失败。GUI 提供 Release Viewer 恢复自动工作。不得依赖用户必须点 Verify 才能提交已观察到的恢复事实。

RELEASED 人工待办不持有排他的业务维护锁；保留的原页由 target ownership 保护。修改 `ProfileUseCoordinator.recover()`，避免服务重启后仅凭旧 challenge_url / maintenance_session_id 又把已解决恢复重新锁为人工维护。

## 七 所有消息源共用的自主恢复算法

### 7.1 首次遇到验证

1. 取得统一 Assessment，保存采样元数据并安排有界截图。
2. AUTO 或 UNKNOWN-auto：创建/合并 BrowserRecovery，保留原页为 RECOVERY；manual_attention_required=false，但普通请求进入 recovery gate，不继续轰炸该身份。
3. 明确人工或 UNKNOWN-human：创建 ChallengeTask 并关联 BrowserRecovery，原页交给 CHALLENGE；人工待办可显示，但后台恢复仍运行。
4. 对自动检查，在当前请求还有预算时允许短观察；剩余预算小于 5 秒就直接交接后台，不在原 30 秒调用外偷偷阻塞 60 秒。用户请求返回 RECOVERY_PENDING，页面不能被 finally 关闭。
5. 后台初始观察窗口从本次首次发现计 60 秒，重启或新一次 ticker 调用不得重新计时。此期间 2 秒一采样，不点击/刷新；观察在 Identity Worker 内异步运行，不阻塞 API 事件循环。
6. 观察期间若出现明确人工交互，原子升级为 HUMAN，保留同一真实页，不新开替代页冒充原始现场。

### 7.2 统一调度

以现有 Challenge Inspector 为入口，改为每 5 秒取到期 BrowserRecovery；不是每 5 秒向网站请求。浏览器实际动作由全局 semaphore=2、每身份=1 限制，任务启动加 0—10 秒稳定抖动。续用既有进程，不给每 ticker 创建常驻恢复协程。

|场景|被动观察|新业务探测计划|
|---|---|---|
|自动验证初始窗口|每 2 秒，最多 60 秒|窗口结束前不重复导航|
|自动验证未过、UNKNOWN-auto、ACCESS_BLOCK|之后每 60 秒最多一次轻量观察|初始窗口结束后，依次等 60、120、300、600 秒；之后每 600 秒|
|人工 Challenge，原页仍在|每 60 秒检查是否已自然变成有效业务页|首次 300 秒后，失败后每 600 秒一次隔离业务页|
|人工原页已丢|没有原页可观察|确认丢页后 60 秒内安排首次，之后同人工退避|
|资源/网络/Runtime 暂不可用|只取可用状态，不伪造 Challenge|延期，保留下一次到期；不能因错误退出永久丢队列|

以上为恢复探测默认值，可放在服务级常量，首批不暴露几十个 Registry 参数。每次真正业务探测最多 75 秒，IPC 外层 85 秒，另有 10 秒资源收尾；正常轮询请求预算保持不变。后台无论失败多少次都不因为次数耗尽而永久停用，也不因自动检查超过时限自动转成“人工必须解题”。

初始自动观察页最多保留 15 分钟；超过后先保存最终证据，再只关闭自己拥有的 RECOVERY 页，转低频新页探测。不能周期性清 Cookie、重建 Profile、切换同一 Identity 的出口，或反复重启整个 Chrome。人工作业页不适用这个 TTL。

### 7.3 原页优先，但不是恢复硬前置

按以下顺序执行一次恢复：

```python
claim_due_recovery_with_cas()
if exact_original_page_is_present:
    capture = observe_without_navigation()
    if business_contract_passes(capture, affected_context):
        commit_recovery_for_that_capability()
        return
    update_assessment_if_evidence_changed()
if fresh_probe_is_due and no_open_human_writer:
    with identity_recovery_permit():
        capture = run_existing_recipe_on_isolated_page()
        commit_capability_result_or_schedule_next_attempt()
finally:
    release_lease_and_exact_probe_target_independently()
```

原页 transport timeout 为 UNKNOWN，不立即丢页。明确丢页则 page_status=LOST，继续走新页路线。新页用同一 Identity/Profile/出口/locale/timezone 与同一 recipe；既不访问另一网站代替，也不切到 Managed/直连假装同一身份已恢复。

若新探测再次得到自动检查，利用本次剩余 75 秒预算观察，不嵌套无限重试；再次得到人工题，只保留该 Identity 一个有效人工原页，不把每次探测变成一个新 Task。原人工页确实丢失时可以用新现场建立带 predecessor 的替代任务，旧丢页历史不抹掉。

### 7.4 真实业务验收与按能力解除

- CRAWLER：必须调用该入口原本 recipe 和 `crawler_capture_items()` / `validate_crawler_capture()`。支持 recipe 明确证明的空搜索结果；不是看到 200、页头或空数组就通过。Reuters 必须实际执行对应 ticker 的 query，不用 Reuters 首页替代搜索接口。
- BODY：原失败文章优先，调用既有抽取和质量验证；FULL/SHORT_FULL 是正文可用证明。404/410 才从同站最近成功列表中选一个替代样本；超时不能靠换文章掩盖。
- 识别到正常登录墙/订阅墙：可证明 WAF 检查结束，但对应能力转为 AUTH_REQUIRED/ENTITLEMENT_MISSING，不能谎报 FULL。公共列表可继续，需鉴权正文保持独立门禁。
- 页面仅无验证码、但仍空白或 app shell：不通过；类型不明也不能按“无题”清健康。
- 恢复证明只作用于该 identity、purpose、当前配置指纹和验证入口。CRAWLER 成功不能认证 BODY，备用成功不能认证主身份。

允许一次真实契约成功立即恢复相应业务，不增加“必须连续成功两次才允许抓取”的新阻塞。随后的各 ticker 正常轮询负责补齐覆盖；如果只有单 ticker URL 失效而其他 ticker 已通过，保留该入口失败，不重新冻结全部身份。只有新的实际 WAF 或 Runtime 证据才能重新升级身份级阻塞。

证明恢复后，事务更新能力和 recovery，再清除对应人工/自动阻塞；将受影响绑定的 next_dispatch_at 提前到未来 0—60 秒分散调度，不伪造 last_success_at。恢复探测不 publish、不推进正常 checkpoint、不调用 Jev、不回补过期新闻。

### 7.5 Fallback

沿用现有真实冷启动验收、独立身份/出口和 sticky 激活，不放宽付费正文认证样本要求。AUTO 观察窗口 60 秒结束后仍失败，或明确人工/拒绝页时，可异步安排现有合格 Fallback 接替；人工待办不阻止备用激活。

主身份后台恢复后先记录 primary_recovered，不自动抢回已稳定工作的备用。沿用显式 Activate Primary 或正常部署路由决策，避免主备往返震荡。备用本身遭遇自动检查也进入相同协调器，不因为 schedule 当前只排除 ChallengeTask 而漏掉恢复门禁。

## 八 维护系统与 Verify 的可完成路径

### 8.1 ViewerReady 必须是真实精确页面

OPEN_CHALLENGE/REOPEN_LOST_CHALLENGE 返回前，核对数据库 target 与 native/managed 实际 instance、target 存在性和任务 generation。若 DB 为 LOST、同实例同 target 实际仍存在，只在明确的 owner 校验事务中修复为 LIVE，并记录 TARGET_RECONCILED；若无法证明则返回 TARGET_LOST/STATE_UNVERIFIED，不谎报 ViewerReady。

不调用 ensure() 重建 Chrome 后还把新窗口称为原页。Viewer 服务存在只代表可显示画面，不代表目标页正确。

### 8.2 Verify 分阶段，不再要求先解原页才有资格验证业务

复用现有异步 MaintenanceOperation，继续快速返回 operation_id，GUI 查询阶段；不把 GUI 的 12 秒 bridge 超时拉长为一分钟同步阻塞。

阶段和默认预算：

|阶段|预算|行为|
|---|---:|---|
|RECONCILING_TARGET|5 秒|核对原页/所有权；不可确定要明确告知|
|OBSERVING_ORIGINAL|10 秒|仅观察；丢页可跳过，超时不吞掉全部后续预算|
|VERIFYING_REQUESTED_CAPABILITY|75 秒|用原页或隔离探测页证明当前请求能力|
|APPLYING_RESULT_AND_RELEASING|10 秒|提交、解除对应门禁、独立收尾|

外层操作总时限 105 秒。每阶段保留已完成的有效结果，不因 BODY 超时撤销 CRAWLER 已通过。待检能力在时间不足时返回 PENDING/NOT_TESTED 并交后台恢复，不写成“仍有验证码”。

VERIFY_CHALLENGE 默认只验证该任务 purpose 与 Challenge，不强制顺便跑另一种能力。人工可以点既有高级文章验证检查正文；VERIFY_LOGIN 检查其必要认证能力。后台仍会接着验证其余受影响能力，避免 UI 一次等待所有项目。

LOGIN 仍独立，不因为历史 Challenge 丢页转成旧原页恢复。点击 Open Login 必须新建登录页；如果该登录流程本身确实出现交互题才新增人工任务。LOGIN 遭遇自动检查时页面留在当前登录会话，显示等待浏览器校验，不挪入 Challenges 列表；自动通过后可继续正常登录。

### 8.3 正确反馈与资源收尾

GUI 保持英文，避免此前字体乱码；至少分别显示：

- `Automatic verification — retrying in background`。
- `Human interaction required`。
- `Original page lost — recovery probe scheduled`。
- `Access restored; login required`。
- `Challenge cleared; crawler ready; body pending/failed`。
- `Page state could not be confirmed` / `Viewer target is unavailable`。

Challenges 页只列有效 HUMAN Task；自动恢复概要作为 Login 页身份状态列或只读详情展示，不增加复杂第三套操作后台。任务已解决但用户仍打开旧页时显示 resolved，不自动关闭用户窗口；Release Viewer 后精确清理。

把“提交业务恢复结果”“释放 Viewer/维护准入”“关闭探测或已完成页”拆开执行，分别 try/finally 和持久 cleanup_pending。页面关闭失败不能回滚已证明的业务恢复，也不能永久占住 Viewer 或 Profile 锁；未释放的真实人工写会话仍须显示独占状态，不能并发写 Profile。

## 九 登录态与能力状态观测修复

在 `SiteIdentityAuth` 增加可选 `last_check_at`、`last_check_result`、`last_check_reason`。`verified_at` 仅代表最后一次明确的登录态证明时间；UNKNOWN 检查不更新它，不将原 UNKNOWN 改成 VALID。

可以保留上次 VALID 作为历史结果，但 UI 显示 Last confirmed valid 与 Latest check unknown，不能继续显示“刚验证已登录”。REAUTH_REQUIRED 需要正常登录墙、明确鉴权响应或既有站点判据；验证码页缺少头像不能认定掉号。ENTITLEMENT_MISSING 同样独立。

后续每次检查记录同一 identity/instance 的 last_auth_confirmed_at、首次明确 REAUTH_REQUIRED、首次 WAF 时间、出口 generation/实例变化。仅在状态转变时写事件，不保存凭据或 Cookie 值。这样未来才有条件判断 Reuters 掉号与验证的先后关系；本轮不宣称自动登录可以解决 DataDome。

## 十 留证和资源治理

### 10.1 留证改进

沿用 EvidenceStore 14 天、256 MiB 上限，不为新机制保存整页历史录像。first 保持原始首遇帧；confirmed 扩展为最多两次有意义补拍：分类稳定且已渲染时一次、最终仍阻断或恢复前一次。兼容旧 first/confirmed 接口，可在 metadata 增加 sample_id、assessment_at、screenshot_at、frame_sequence、capture_error；不无限覆盖假装同一时间。

截图超时从统一 2 秒调整为后台最多 5 秒，不占业务事务。白图/截图失败保留元数据，并在 2 秒后最多补拍一次；可以使用已有图像解码依赖做近乎纯色检测，不能为了这个引入重型视觉服务。若无轻量依赖，按“文档已渲染+有可见文本”补拍即可，跳过像素分类。不能删除或覆盖 first 来隐藏空白问题。

DOM/响应/截图无法保证原子，但必须有同次 sample_id 与各自时间，避免早先 Challenge 分类挂在晚些正常页面上还声称是同一最终状态。自动校验也留证，即使没有人工 task；新增按 recovery_id 获取证据的受限接口，权限和脱敏与现有 Challenge 接口一致。

### 10.2 CPU 与浏览器生命周期

- 每身份 1 个恢复动作，全局 2 个，截图全局最多 1 个；重复 ticker 调用只读当前恢复状态。
- 每轮记录实际 probe wall time、队列时间、主机可用 CPU/负载、已有 resource_safety 快照、容器 throttling 指标；不把 load 高直接标成网站 Challenge。
- 使用现有资源安全阀。普通高负载最多推迟轻量观察 120 秒，此后至少串行执行一项现有页观察，避免“因为繁忙永远不恢复”；硬内存/磁盘安全阀仍禁止新的冷启动，明确标 RESOURCE_DEFERRED 并保持可重试。
- 正常活跃 Identity 不因被标 lost 就立即停止整个 Chrome；只有确认实际实例无人工活页、无业务/恢复 lease，才可由既有 idle 策略 graceful stop。自动观察页 TTL 不等于整个 Profile TTL。
- Controller 回收与 Chrome 进程停止分开；Managed 有恢复页时禁止关闭其 context，External detach 不能关闭 Chrome 活页。重启后先 reconcile 再决定是否新建。

### 10.3 事件与磁盘

必须记录 RECOVERY_STARTED、CLASSIFICATION_CHANGED、PROBE_STARTED/FINISHED、RECOVERY_RESOLVED、TARGET_LOST、TARGET_RECONCILED、CHROME_STOPPED，携带少量 ID、原因、instance、duration 和配置指纹。Supervisor.stop 不再丢弃 reason，增加正常 stop/crash/shutdown 的来源。被动重复观察只更新当前行，不每 ticker 写相同大事件。

Supervisor 仍不直接写 Registry：在现有状态 manifest 追加最近一次 stop/exit 摘要，输出受 Docker 现有滚动日志上限约束的结构化日志；Controller 通过原有状态/停止回执写访问事件。无法确定进程退出原因时记录 unexpected_exit/unknown，不推断成 OOM。无需再创建一套无限 JSONL 生命周期数据库。

沿用现有 retention.py：已解决 recovery 当前行保留 7 天；任务和事件按既有保留策略，留证受 14 天/256 MiB 限制；活跃人工 Task 不因日志清理被删除，已丢原页可仍持续后台探测。清理只针对本模块拥有的状态/证据/临时页，不碰 Cookie、Profile、业务库、回滚镜像或运行中 Chrome 的缓存。

## 十一 对上游 Message Bus 和正文补全的契约

新增 `AccessDisposition.RECOVERY_PENDING`，`AccessResult` 增加可选 recovery_id、handling_mode、retry_at。保持 ACCESS_CHALLENGE 作为真实安全验证观测类别，但不再无条件转人工；无 Assessment 的旧调用按新站点策略补齐。

`SiteAccessError.site_access_deferred` 识别 RECOVERY_PENDING。Message Bus 的 crawler adapters 和正文补全的现有延期逻辑统一映射为 `site_recovery_pending`，避免又被包装成不透明 SiteAccessError/search_query_failed。

调度规则：

- 正常 poll 配置不变。恢复中的调用得到 deferred/recovering，不增加新的真实风控次数，也不写成功消息计数。
- last_success_at 只能由真实正常采集成功更新；last_attempt_at 与 last_network_probe_at 分开显示，避免把读缓存门禁当访问了网站。
- 正文队列按 retry_at 延期，不把暂时自动校验永久写为 BODY_FAILED；达到原有业务过期窗口仍按既有规则结束，不能让恢复功能无限持有已失效文章。
- `AUTH_REQUIRED`、订阅不足、parser/content error、预算排队分别保留，不用 recovery_pending 掩盖真正失败。
- 原有 30 分钟新闻时效性、去重、共享分发和 publish 规则不变；恢复试探结果不直接进入消息总线。

需要前后端枚举/客户端同步升级，不能 Site Access 先返回新枚举而旧消费者反序列化失败。实施时搜索所有 `AccessDisposition`、`site_access_deferred`、`site_budget_deferred` 与 `SiteAccessError` 使用方，列入同一发布单。

## 十二 文件级开发任务

|顺序|文件或模块|具体改动与完成条件|
|---|---|---|
|1|site_strategy/challenge_detection.py 新增；challenge_evidence.py|统一 Assessment、证据优先级、旧 classify 兼容；静态脚本/广告不触发人工|
|2|schema.py、resolver.py、seeds.py、identity_worker.py|ChallengePolicy、ResolvedSite/IPC 传递、首批策略、认证检查时间字段；老 JSON 默认可读|
|3|governance_models.py、governance_repository.py、repository.py|BrowserRecovery 表、RECOVERY owner、租约/CAS、resolution 原因与原子提交/迁移|
|4|runtime.py、external_runtime.py、worker_runtime.py、browser_operations.py、targets.py|自动/人工分流交接、精确观察和隔离 probe 命令、正常 finally 不误关已交接页|
|5|recovery.py 新增；challenge_inspector.py|通用到期协调器，替换 Investing-only 恢复，不并行跑两套检查重复提交|
|6|service.py 的 ProfileUseCoordinator；worker_manager.py|恢复专用准入、OPEN/RELEASED 区分、重启恢复投影与单写者一致|
|7|verification.py、service.py.verify_scope_session、maintenance_document.py、maintenance.py|分阶段 Verify、lost 可业务重测、真实 ViewerReady、独立结果和收尾|
|8|health.py、failover.py|manual 标记只为人工事实；恢复门禁独立；fallback/recovery 互斥且不串改身份|
|9|api.py、client.py、deploy/site-login-admin.py、deploy/site-login-ui.py|恢复概要/留证读取、异步阶段反馈、新结果兼容、自动项不出现在 Challenges|
|10|message_bus_v2 与 content_enrichment 的 SiteAccessError 消费点；相关消息源状态投影|延期而非伪失败、真实轮询时间、恢复后的分散调度；不动无关 RSS/API 实现|
|11|chrome_supervisor.py、worker_runtime.py、retention.py、resource_gc.py|停止原因、页保护、恢复记录保留；仅修有关路径，不启动新 GC 平台|
|12|tests 与 changelog|下列测试齐全，记录真实变更及验收局限|

以上为当前真实文件路径，根目录为 `src/doxagent/`，deploy/tests 另列。新建模块仅 challenge_detection.py 与 recovery.py；不再拆十多个 controller/repository/service 新层。

## 十三 必须补充的测试

### 13.1 静态分类与真实现场回放

在现有 test_challenge_evidence.py 基础上，新增脱敏 HTML/响应 fixture，覆盖本轮 DataDome slider、Device Check、Retry、Cloudflare spinner、正常页头过渡、403 非 WAF、普通登录墙、订阅墙、广告、仅加载脚本、正文提到 CAPTCHA、iframe 未加载、未知供应商。

必须验证：供应商 HIGH 不等于类型 HIGH；AUTO 不创建人工 Task；UNKNOWN 按站点策略选择但原始事实仍 UNKNOWN；AUTO 后出现明确滑块会升级；NORMAL 与旧 header 冲突有时间和文档证明。

### 13.2 状态与并发

至少新增以下自动化 case，使用 fake clock，不靠真实等十分钟：

1. 自动校验 8 秒后形成正常页，不人工介入即继续采集。
2. 自动校验超过原请求预算，页面交接不被 finally 关闭，后台 50 秒时恢复。
3. 自动校验长期失败，永远不创建假人工题，但始终保留下一次到期。
4. 原页在交接前/后丢失，分别进入明确故障或新页探测，不发生永久锁死。
5. TARGET_LOST 恢复新页通过，旧丢页事实保留且业务恢复。
6. Reuters 等待人工时原页自然恢复；无 Verify 点击自动解除。
7. Reuters 原页仍是题，但同身份隔离真实搜索恢复，按准确原因结束旧 Task；备用恢复不结束主任务。
8. 九 ticker 同时报错，仅一个 recovery/一个探测；其余只合并上下文。
9. 单 ticker URL/content error 不重新冻结其他已成功 ticker。
10. 旧 Worker/旧 instance/旧 generation/改策略后的迟到成功不能清新门禁。
11. DB=LOST、实际 target 存在的 reconcile 与 target 真不存在，分别返回准确结果，不虚报 ViewerReady。
12. OPEN 人工会话不被后台抢页或并发写 Profile；RELEASED 不阻断后台探测。
13. Profile 维护投影、旧 Task 与恢复对象重启后能重建一致状态。
14. CRAWLER 成功、BODY 超时，采集仍可恢复；正常登录墙只恢复 WAF、不冒充认证有效。
15. UNKNOWN 认证不刷新 verified_at，不覆写最后明确认证结果。
16. closeTarget 失败不回滚健康，不永久占 Viewer；清理只触碰自己目标。
17. Managed context 与 External Chrome 均覆盖同样流程，detach/idle 不误杀恢复页。
18. 主备接替、sticky 及登录要求原测试仍通过。
19. HTTP/RSS/API 不受本次浏览器恢复门禁误伤。
20. Screenshot 超时/白图只导致有界补拍，不冻结 API；事件/保留清理有上限。

建议新增 `test_browser_recovery.py`、`test_challenge_detection.py`；扩展现有 `test_source_maintenance_refactor.py`、`test_site_maintenance_recovery.py`、`test_identity_control_workers.py`、`test_reuters_maintenance_cold_inspection.py`、`test_maintenance_phase_recovery.py` 和客户端/正文延期测试。不要用一个大 mock 测试代替真实 parser/recipe fixture。

## 十四 旧生产状态迁移

迁移作为显式、幂等 CLI：`python -m doxagent.site_strategy.cli recovery migrate --dry-run/--apply`。启动只创建兼容 schema，不静默清空人工待办或删现场。

1. 从当前 SQLite 和留证建立受影响清单，按 identity 找到当前 Task、页面引用、OPEN/RELEASED 会话和 runtime 门禁。生产状态必须重新读取，不直接使用本轮快照值。
2. 优先用已留证的明确 Assessment：Device Check/Cloudflare 自动页改为后台恢复；Reuters 明确滑块保留人工；UNKNOWN 按上述站点策略并标 migration_policy_reason。
3. AUTO 的旧人工 Task 以 RECLASSIFIED_AUTO_CHECK 结束，创建 BrowserRecovery、转换精确页 owner；活页移交而非关闭。OPEN 用户会话不被迁移夺取，待用户释放后再转换所有权和准入。
4. LOST 的任务不伪造成 LIVE，不调用 Reopen 冒充原始页；创建 recovery，首轮只做业务试探。保留旧页面丢失历史。
5. 清除与已转 AUTO 的同一 recovery 对应的旧 manual_attention_required；同时安装 recovery gate，不能在中间态使九 ticker 无限制放行。会话、Task、恢复行与 runtime 在单事务提交。
6. 只修对应 Profile 的旧维护投影，不能清掉另一个有效人工 LOGIN 会话。UNKNOWN auth 的旧 verified_at 无法反推真实时间，标 legacy_confirmation_time_uncertain，不根据旧 VALID 假造最新证明。
7. 同一命令重复运行不重复建 Task/占页/重置首次观察窗口；输出迁移前后数量、未转换原因和实际页状态。

站点策略升级只修改本次 challenge 策略字段及受影响 revision，保留人工维护的 Profile、身份、出口、登录/验证地址与 parser 参数；禁止用整份 seed 覆盖生产 Registry。dry-run 必须把这些不应改变的资源列为前后相同。

迁移后旧人工列表只剩真正人工任务；自动状态可从受限 inventory 查询，不需要用户逐项点击解除。

## 十五 发布与真实验收

### 15.1 发布顺序

1. 完成代码、离线回归和 dry-run，确认所有新枚举读写方在发布单中。记录精确 commit、镜像与回滚镜像，不再依赖远端临时源码 overlay。
2. 保存 DB 可恢复快照、当前任务/活页/会话清单、关键配置与留证；保留所有 Profile，不清 Cookie。
3. 暂停新恢复 claim 和受影响业务派发，给在途操作有限 drain；重建必要 Site Access、Message Bus、正文补全及展示新状态的 API/Projector 等消费者。是否重建具体容器由代码镜像归属核实，不能因为共享基础镜像重启全部服务。
4. Supervisor 的 stop reason 日志变更需要新镜像。若有人工活页，先单独确认维护窗口再重建，明确它们将成为 TARGET_LOST；不得偷偷重启后声称保住原页。可以先发布不需要 Supervisor 重启的核心恢复，再在获准窗口发布日志部分，但日志部分仍属于本方案最终验收项。
5. 容器健康检查通过不等于业务恢复。执行 migration dry-run→apply，再启动恢复协调器和正常派发；Managed/External 均验证。
6. 更新桌面 admin/UI，保留精确 sudo 权限和 token 隔离。校验 Challenges 页自动项已移出，旧人工项和图像仍可查看。

### 15.2 分站与全源验收

必须从 Registry 导出全部 active crawler scopes 和 Browser Runtime 类型清单，不能只测三个站点就声称通用。包括当前实际启用的 Reuters、Investing、Barron’s、CTEE、TrendForce 两入口、DIGITIMES、Yahoo、InvestorsHub、GlobeNewswire 等；仅正文注册的 WSJ/MarketWatch/Seeking Alpha 也应验证 BODY 通用契约，生产真实请求受其当前启用情况和人工授权约束。RSS/API 正常入口做无回归检查，不强行切浏览器。

首批重点：

- Investing：至少覆盖 INTC、BE 与原失败 RKLB 对应 URL，确认自动校验超过 30 秒、丢页后仍可后台恢复，不要求登录。
- Barron’s：ticker 列表与真实订阅正文分别验收，Device Check 不进入人工列表，账号不足时明确需登录。
- Reuters：真实 query、明确人工 slider、自然恢复、原页丢失、账号失效五种分支分别以 fixture/受控内部故障测试覆盖；不能为制造样本反复请求网站诱发验证码。真实生产观察不主动解题。
- CTEE：Managed Runtime 下自动验证和受保护接口/HTML 入口均用其现有 recipe 验收，不用首页 200 代替 API/栏目结果。

生产至少观察 24 小时自然轮询，覆盖所有相关 ticker，记录每个新 recovery 的类型、恢复方式、原页是否丢失、耗时、实际业务成功数及仍未恢复原因。若观察窗无自然 Challenge，明确“真实验证码复发未覆盖”，但仍须通过注入 target loss、worker detach、超时和 CPU 延迟的离线/隔离集成测试；不得以无法制造外站 Challenge 无限拖延交付。

通过标准：

- 已确认 AUTO 的现场没有新增人工 Task；无人点击时可从 OBSERVING/BACKOFF/LOST 恢复；持续被拒则保持可到期重试且状态准确。
- 人工 Task 在业务自然恢复后自动解锁相应业务，原页和 Viewer 处理不破坏用户操作。
- 多 ticker 不造成成倍探测；正常轮询时间与真实成功记录一致；正文不被临时自动校验永久判失败。
- 全源新增正常成功/正文样本与既有状态无系统性回归；未恢复站点按真实 AUTH、INTERACTIVE、ACCESS_BLOCK、TRANSPORT、PARSER 分列。
- 24 小时页面、恢复记录、事件与截图数量有界，资源占用无恢复器自身驱动的持续增长。

一次通过、一次 Verify CLEARED、HTTP 200 或容器 ready 都不能单独作为最终业务验收。

### 15.3 回滚

保留原镜像与迁移前受影响行快照。新表/字段应向前兼容，新读写方统一回滚；由于 SiteModel extra=forbid，不能直接让旧版加载新 JSON 后崩溃。

提供显式 `recovery rollback-export`：暂停恢复、等待 lease 释放后，将涉及旧模型的 JSON 字段投影回旧 schema，并将仍未解决的验证重新投影成旧版可识别的人工/冷却状态；新恢复表、证据和历史不删除。活页按精确 owner 处理，不用回滚恢复旧 target 为不存在的活页，也不删除迁移后产生的消息或 Cookie。先在副本验证旧版能读取投影，再替换镜像。

## 十六 实施批次与完成定义

按以下顺序完成，不把必要修复留作遥远 P2：

1. **分类与持久契约**：Assessment、Policy、Recovery 表、兼容读写、真实 fixture。
2. **原页交接与恢复准入**：RECOVERY owner、精确 LIVE/CAS、专用 lease、Managed/External 均可观察和探测。
3. **全站自主恢复**：替换 Investing 特例、lost 路径、人工自然恢复、能力独立提交、主备协作。
4. **维护与观测**：Viewer/Verify、认证时间、截图补拍、生命周期原因及资源限额。
5. **上游集成与迁移**：RECOVERY_PENDING、正文延期、状态展示、幂等迁移和旧状态回滚投影。
6. **窄发布及持续验收**：24 小时回执、全源覆盖与局限记录、changelog。

完成不以代码文件齐全为准，而以四条闭环为准：自动校验不找人；人工待办不阻止自然恢复；丢页不阻止新业务证明；页面、账号、业务和资源状态互不冒充。

## 十七 外部机制核实依据

以下资料只用于确认机制边界，具体站点判定仍以本轮现场与运行数据为准：

- Cloudflare 明确区分 non-interactive、managed 和 interactive；managed 可先自动验证，也可升级交互。因此不能按供应商名永久决定是否人工。[Interstitial Challenge Pages](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/)
- cf-mitigated: challenge 适用于各类 Challenge，不包含交互类型结论。[Detect a Challenge Page response](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/)
- DataDome Device Check 无需用户交互，但可能最终放行、拒绝或追加 CAPTCHA。因此自动检查超时不等于应直接找人，也不能保证最终一定通过。[Device Check](https://docs.datadome.co/docs/device-check)
- DataDome 将 Device Check、Captcha、Block 列为不同响应页，支持本方案分别治理。[Response pages](https://docs.datadome.co/docs/response-pages)

本方案采用抓取诊断技能中的“页面证据与实际内容契约分别核对”方法，不采用 stealth、身份随机化或自动解题示例。

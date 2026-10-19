# Barron's ticker 持续失败：根因与修复验收

日期：2026-10-20，时间均为北京时间，UTC 证据保留原时区。范围只限 Barron's ticker news / Other Dow Jones（不含 IBD），不将 Reuters 的网站风控结论套用到本源。

## 1. 先区分实际网站访问与旧治理拒绝

- 九个 ticker 当前均报 `SiteAccessError / human_challenge_required`。最早四个 ticker 最后成功在 10/15 18:04–18:05，而不是本轮刚发生的九次独立 Challenge。
- 最后保留下来的实际风险事件是 **10/15 18:06:58**：股票页浏览器请求 401、`ACCESS_CHALLENGE`、出口 `205.198.126.113`、身份 `dowjones-main`。不能据此证明此前每一次故障都是风控，也不能反向否定这一次记录。
- 之后的 scope 状态保持 `manual_attention_required=true`；原始页先丢失，再经两次显式重开产生后继待办。当前待办 `challenge_57ea16aaca22483d99425b2b064cb7c6` 的原因是 `explicit_lost_target_reopen`，不是新抓取收到的 Challenge。`seen_count` 是重复遇到既有阻塞，不是 Challenge 发生次数。
- 02:28 的直接 DOM 读取确认该待办原始 Target `8F2E82D2726ABF3C6B7859F56C01932B` 已在正常 Barron's 首页，没有验证码 iframe 或拒绝文案。其 Profile 仍处在未结束的 CHALLENGE 维护会话。网站正常与治理阻塞同时存在，符合用户所见，不应再笼统要求重做 Challenge。
- 主身份认证记录为 VALID；备用身份认证 UNKNOWN、无已验证订阅能力。因此配置中有备用不等于备用可以代替已登录主身份读取订阅正文。本轮不复制 Cookie、不伪造备用认证、不盲切 IP。

## 2. 导致维护后仍不能恢复的可修复机制

### 2.1 维护验证依赖同一个脆弱的采集 recipe

02:28:38 提交真正的 `VERIFY_CHALLENGE`。原始页面检查进入 `CHECKING_CRAWLER`，约 32 秒后返回：`challenge=UNKNOWN / crawler=CONTENT_ERROR / crawler_reason=TimeoutError / body=NOT_TESTED / session_closed=false`。这不是网站又返回了 Challenge；是业务验证没过，所以旧阻塞、维护占用和人工待办都没能收尾。

### 2.2 抓取把 UI 完成误当成新闻数据可用的必要条件

旧 `_capture_barrons_ticker_cards`：滚动定位 `News_index` → 最多 24 轮等待链接 → 多轮读取 `. ET` 日期文字 → 点击 OTHER DOW JONES → 再等链接变化和日期。后半段还会吞掉异常，只留下主标签。

实测同一正式 Chrome 153、同一 Profile、同一出口，无新的 Challenge：

- 页面主文档约 3.9–4.3 秒返回 200；报价和登录用户能显示，初始没有 `News_index`。
- 自然滚动后，网站 **`/market-data/api/news` 返回 200**，包含正式 `News.StoryCard`、标题、链接和 `timestampUtc.value`；部分 UI 日期仍停留在 `TimeTagPlaceholder`。
- 旧采集对照在 25 秒、35 秒观察预算内仍未完成。跟踪显示 DOM 定位/读取与滚动稳定性等待会额外消耗时间，远大于新闻数据到达所需时间。不能将这些耗时归类成账号掉线或 Challenge。
- 原生前台/CDP 观察同样取得新闻接口 200；因而本轮**没有证据需要更换 Profile、出口或再换一种浏览器身份**。

### 2.3 没有 NCP 痕迹的原因

旧 Barron's recipe 本来就是 DOM、滚动和标签点击，不使用 Yahoo 的 `/xhr/ncp`。本次观察到的 Barron's 原生新闻接口是 `/market-data/api/news`。不应把没有 NCP 操作当作没有执行的唯一证据；需要结合访问 attempts、Worker、原始 Target 和真实网络响应。

### 2.4 冷重连与错误验证 URL：生产复验发现的另外两条链

新 recipe 发布后首次验证仍在 30 秒预算内失败。日志显示 Worker 已开始恢复并确认原生实例，但未完成 attach；原生遗留业务 Target `14B57B7B4EE37B9A0FDC56BF8BDCFCA7` 随后被所有权 reconcile 清掉。代码却继续遍历 Playwright 缓存中的全部 Page 并逐个 `new_cdp_session`；原生关闭与 PageClosed 通知不是一个同步通道，关闭中的旧句柄会拖住恢复。旧页消失后再次冷恢复，attach 约 **6.11 秒**完成，真实 crawler 随即 READY。新增回归用仍未收到 PageClosed 的孤儿句柄重现此机制；修复先查询精确原生 Target 的 URL，仅尝试未关闭且 URL 匹配的候选，最后仍校验 Target ID，不按 URL 直接认领人类页。

该次验证正文仍失败：公开结果 `verification_url=https://www.barrons.com/login`。历史 BODY 待办经旧版本重开后保存了登录地址，`body_verification_url` 仅排除 crawler URL，所以错误地优先选了它。此处不是订阅无效。修复自动正文候选排除已登记 login、首页和已知 crawler 入口；保持人工指定文章、有效订阅证据和最终发布者约束。**不能以“正常首页”证明付费正文有效。**

02:50:56 的实际 crawler 验证已通过正常健康提交解除了旧任务并关闭维护会话；02:52 九个 ticker 都有自然轮询成功、连续失败归零。它仍只是开始恢复，后面继续检验正文和持续性。

### 2.5 普通登录会复活历史 LOGIN，会再次制造业务停顿

02:57:54 主动做普通 Open Login 收尾验收，意外复用了 **10/18 创建的 RELEASED LOGIN**，目标属于早已结束的 native instance，而不是创建新登录页。约 11 秒后 configure 超时；Profile 已被重新改成 MAINTENANCE，九个 ticker 随后都报 `identity_in_maintenance`。这是本轮维护动作触发并可重现的内因，不是新的 Barron's 风控。

代码发现：`OPEN_LOGIN` 自动重连只按 kind/identity/scope 选会话，**没有排除 CLOSED，更没排除目标已丢失的 RELEASED**。所以即便人工曾完成维护，下一次 Open Login 仍可重启旧锁。02:59:31 已通过正常 CLOSE_LOGIN 关闭这个确定过期的旧会话，未触及当前 Chrome/Cookie。

修复：自动恢复只挑 OPEN/RELEASED；已关闭会话不再复活。已知 LOST/CLOSED 或经 Supervisor + loopback CDP 元数据证明原页已不存在的 LOGIN，只结束旧会话占用并新建正常登录页。连接超时、浏览器存在性不明不当成“页丢失”，不关闭或复制真实人工页；Challenge 路径继续独立。另将 configure 冷启动预算从 10 秒调整为 20 秒，整体命令/维护 deadline 仍保持原上限，不做无限重试。

正文业务对照实际读取两篇不同订阅文章，均 SUCCESS/FULL，3,212 / 4,701 字符，native instance 仍为 `011bcc052e3a47b395204e6e7b6b5f35`。旧整页加载/多次分类使每篇网络阶段约 23–26 秒，会挤压维护验证预算；因此 Barron's 正文也改为 commit 后等待实际文章候选达到原质量标准（或明确登录/订阅/Challenge 页面），不再等待广告相关的全页事件。正文门槛没有降低。

## 3. 窄修复

1. 保留 External Chrome、CDP、Profile、出口、单身份并发和持久化；不加入 stealth、不随机化指纹、不重建登录身份。
2. Barron's ticker 主文档等待 `commit`，随后按实际新闻数据合同读取，不再等全页广告脚本或 lazy DOM/日期标签稳定。显式主页面 401/403 仍先等待并分类真实拒绝页，真实 Challenge 的原页保留机制不变。
3. 新 `message_bus_v2/barrons_sources.py` 在**已打开的同源 Chrome 页面内**执行两次浏览器原生 fetch，读取 Barrons 与 BarronsDowJonesNetwork 两个 channel；保留浏览器自身 Cookie、代理、TLS、缓存和网络行为，不在 Python 重建 HTTP/认证头。
4. 两个 feed 都须返回有效合同；HTTP 拒绝、无效 JSON、字段变化不能被吞掉后冒充完整成功。仅新闻接口 401/403 记为接口鉴权/访问不明，不能凭空声称用户正在看的顶层页面出现 Challenge。
5. 使用 `timestampUtc.value` 生成已有 parser 可消费的两个 section；不再依赖异步 `. ET` 文案，不用首次发现时间补日期。继续执行原有精确时间窗口、去重、最终发布者域名路由、正文补全；IBD 和非允许发布者仍排除。
6. 两个 feed 经验证确实为空时允许正常空轮询；缺失一边、普通空壳、数据合同错误仍失败。正文订阅验证和主备验收标准不降低。
7. 维护只在真实 crawler/body canary 后通过现有原子健康提交与精确 Target 清理收尾，不直接写库清掉 Challenge 标志。

候选对照：同一个生产主身份约 **6.11 秒**返回 **35 条**有效新闻（20 Barron's、15 Other Dow Jones），含精确 UTC 时间。它只是修复试验成功，**不是持续生产验收结论**。

本地测试：121 passed，包含新增 18 项原生 feed、两标签完整性、时间、IBD、HTTP 错误分类、超时及 Runtime 保留真实 Challenge 的回归；Ruff 通过。Windows curl-cffi event-loop 警告不影响测试结果。

## 4. 发布与连续验收

实现提交：`07179cec`。使用生产精确父镜像，仅更新 Site Access 与 Message Bus 的三个相关文件；Chrome Supervisor、native Chrome 与人工原始页不重建。发布与连续业务验收记录将在本节追加，不能以容器 running、API 200 或上面的单次 35 条作为已恢复结论。

原始脱敏诊断保存在 `eval/barrons_root_cause_20261020/`；探针只读生产库或创建并关闭自身临时页，不关闭用户原始页。未记录管理令牌、Cookie、网站密码。

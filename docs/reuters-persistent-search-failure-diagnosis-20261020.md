# Reuters 持续 search_query_failed：实证诊断、已部署修复与未通过验收项

日期：2026-10-19 夜间至 2026-10-20，以下时间均为北京时间。

## 结论与交付边界

**这是复合故障，不是“所有失败都是新 Challenge”，也不是“登录一次就恢复”。已修复恢复控制链的程序缺陷，但本轮连续生产试验再次失败，不能交付为 Reuters 已稳定恢复。**

本轮把“没有访问网站的治理拒绝”“维护控制超时”“真实搜索失败”“实际交互验证页”分开取证。恢复之后没有立即结束，而是继续正常生产轮询，实际复现了主身份、冷备用先成功后再次被验证阻挡的完整过程。

当前主、备用均有新的、仍存活的真实 Challenge 原页。没有清空 Cookie、伪造健康、跳过验证、删除任务或注入历史新闻。账号自动登录脚本未增加：它不能解决已登录身份也会遇到的访问验证，且当前没有配置凭据引用。

## 一、最初那些错误并没有再次访问 Reuters

23:48 左右，九个 ticker 的 Reuters 轮询均失败，主要是：

- `search_query_failed / manual_attention_required`。
- `AccessResult.attempts=[]`；已有 Challenge 被关联至新的请求。
- 真实 `access_events` 最后停在当天下午 **14:04:27**，不是每分钟又访问网站被拒绝。
- 当时先有成功搜索，再有一个 BODY 请求触发 Challenge，Identity 的 `manual_attention_required` 随后把搜索和正文都挡住。

因此，之前仅凭 `search_query_failed` 或 `ACCESS_CHALLENGE` 就汇报“现在再次遇到了 Challenge”是不成立的。新请求 ID、任务 `seen_count` 或 `last_check` 更新，也不代表发生新的网络访问。

只读检查当时的原页：

| 身份 | 原页 | 实际识别 |
| --- | --- | --- |
| `digitimes-de-1`（Reuters 主身份的历史名称） | Microsoft/Nscale 正文 | article，FULL，2337 字符，无 Challenge |
| `reuters-backup` | Micron 搜索页 | 正常 listing，无 Challenge |

这与用户所说的“维护浏览器看不到 Challenge”一致。没有削弱正文或 Challenge 识别器来得出上述结果。

## 二、确定的程序根因：恢复控制链不能可靠完成

### 2.1 查看原页错误地依赖完整冷 Worker 启动

原来的 `inspect_challenge` 给 Worker 全链路 **5 秒**，包括：Python 子进程启动、策略快照载入、Playwright 控制驱动启动、CDP 接管原页、读取页面、正文级分类。

实测而非推测：

- 首次完整 `inspect_html` 的通用抽取器冷加载约 **4.58 秒**，另一轮约 **6.69 秒**；热调用约 0.2–0.36 秒。
- 生产阶段日志中，Worker 从请求开始到进入 `maintenance_recovery phase=begin` 约 **9 秒**。
- 随后的原页接管另有一次实测 **6.95 秒**。
- 5 秒超时会回收控制 Worker；再次 Verify 又从冷启动开始。
- 单独把预算改成 15 秒仍复现失败，证明不能把问题简化成“加几秒超时即可”。

**已落地：** External Chrome 的原页检查改为受限的本地 CDP DOM 观察。先核对持久化 Session、Target 所有权、Identity、实例 ID、页面 ID，再读取该页；不启动 Worker、不执行 ensure、不导航、不重新选择 Viewer、不抢控制权。通过 `DOM.getDocument / DOM.getOuterHTML` 读取，不依赖网页主 JavaScript 执行上下文。

仅原页分类使用 `generic_extraction=False`，保留 DOM/站点/访问状态判断，不冷加载通用正文抽取器。**正式正文质量验收仍使用完整抽取链。** Managed Runtime 保持独立 Worker 路径，检查预算有界调整为 15 秒。

### 2.2 已解除旧 Challenge，清理超时却仍锁住 Profile

00:06:58，改后的原页观察正常完成，进入真实搜索检查。00:07:37，搜索合约通过，旧 Challenge 已 RESOLVED，健康提交已完成；但正文检查占用了大部分总预算，随后 CLEANUP 在外层 45 秒到期时被取消。

结果是：界面显示 Verify 失败，Profile 仍在 MAINTENANCE，后续搜索变成 `identity_in_maintenance`。这不是账号没有登录成功，更不是新的搜索页面被拒绝。

**已落地：**

- 业务验收的内部时间预算为 30 秒，在外层 45 秒中为清理留出独立余量。
- 清理有独立 10 秒上限；清理失败保留已经完成的报告，返回 `cleanup_pending`，不将已经完成的检查整体抹成无结果的超时。
- 如果 External Chrome 的控制 Worker 已不存在，显式关闭完成会话不再为了关一个已知页面去冷启动 Worker。只关闭登记的同一实例、同一原页；实例已换代时绝不关闭替代实例的页面。
- 冷关闭成功后才登记 Target CLOSED，正常结束维护、释放 Profile。温 Worker 继续使用原有精确关闭路径。

旧会话通过先只读重接再关闭，00:12:50 完成释放；后续部署修正了需要人为“先预热再关闭”的缺陷。

## 三、持续试验没有通过：这次确实出现了新验证页

| 时间 | 真实结果 |
| --- | --- |
| 00:13:01–00:13:39 | 主身份 4 次真实搜索成功、4 次 BODY 访问成功；正文能力记录有 FULL 证据 |
| 00:14:09 / 00:14:13 | 新的 Intel 搜索保存了新的交互 Challenge 原页，并记录真实风险失败 |
| 00:17:42 | 备用旧原页正常；真实搜索 READY，旧任务解除且会话成功关闭；BODY 因剩余预算超时，未冒充 FULL |
| 00:18:09 | 系统真实执行备用停启、保留 Profile、冷搜索合约验收并切换路由；不是手写 VERIFIED_COLD |
| 00:18:14–00:18:21 | 备用 2 次生产搜索成功 |
| 00:18:38 / 00:18:40 | 备用的 Coherent 搜索也产生新的真实搜索 Challenge，随后两身份均被正确阻挡 |
| 00:27:02–00:27:04 | 新原页 Verify 在约 2.28 秒内明确返回 BLOCKED；不是 Worker 超时，也没有误报 CLEARED |

截至本轮诊断收尾，Reuters **持续运行验收不通过**。没有以 00:13 的短暂成功或 00:18 的冷切换成功作为交付成功。

00:13:43–00:33:45 共采集 **21 个只读生产状态样本，约 20 分钟**；最终九个 ticker 均为 `manual_attention_required`。这些是对自然业务轮询的观察，不是额外向被阻挡页面每分钟发请求；大多数后续失败属于治理拒绝，不能重复统计为新网站 Challenge。本轮有独立新原页证据的复发是上表两次。

主原页真实截图保存在本地：`eval/reuters_root_cause_20261019/original-challenge.png`。画面明确显示 **Verification Required / RETRY**，并显示出口 `205.198.126.113`。原页搜索 URL 是：

`https://www.reuters.com/site-search/?query=Intel&offset=0`

这与在首页、旧正文或另一 Chrome Profile 上确认登录不同，不能相互代替验收。原页标题仍可能是 “Search Results”，不能只用标题判断没有 Challenge。

## 四、普通 Chrome 对比：能证明什么，不能证明什么

使用同一个主 Chrome 实例、同一个 Profile、同一个出口，另开一个只属于本轮试验的原生前台搜索 Target：

- 不调用 Playwright 页面控制 API。
- 不安装业务 `Fetch` 导航拦截。
- 只通过普通 Chrome `Target.createTarget` 打开同一个 Intel 搜索 URL，并只读观察 DOM。
- 3 秒后的文档仍是交互 Challenge。
- 只关闭本轮返回的实验 Target ID，原 Challenge 原页不动。

因此，**当前受限访问不是只有抓取器 `page.goto` 才失败**。但该比较发生在这个身份已经触发限制之后，不能进一步声称“Playwright 从来无关”，也不能精确区分 IP 信誉、Profile 历史、访问密度或服务端风控计分。

没有完成“干净初始身份、相同出口、相同历史、相同负载下，仅改变启动/控制方式”的因果实验；不能编造这一结论。也没有因为当前仍被阻挡，就盲目加入 stealth、随机 UA、验证码自动化或密码脚本。

### 出口核实

| 逻辑出口名 | 实际节点 | 实测 IP | 已登记地区 |
| --- | --- | --- | --- |
| `de-standard-1` | 德国标准 IEPL 专线 1 | 205.198.126.113 | DE |
| `jp-standard-6` | 德国标准 IEPL 专线 2 | 205.198.126.114 | DE |

`jp-standard-6` 是历史名称，**实际不是日本节点**。两出口满足当前的同地区、不同 IP 配置，不能根据名字错误诊断为 DE/JP 不一致。它们仍是同一 /24 的相邻 IP，不能把这种独立性等同于不同运营商、不同信誉网络；本轮也没有证据断言二者的具体 ASN/风控评分。

## 五、登录、NCP 与自动登录评估

Reuters 当前 `AuthPolicy.requirement=optional`，Crawler/Body 继承它。备用在账户认证 UNKNOWN 时确实通过了真实搜索和冷启动接替，这证明“没有先登录账号”不是搜索持续失败的充分解释。

Reuters 实现是 `builtin:reuters_search@1`，由真实 Chrome 打开搜索页并解析 DOM，**不是 Yahoo 的 NCP 采集接口**。维护阶段只看正常页面，或此前因治理状态根本没有发起采集，都不会出现 Yahoo NCP 请求痕迹。如果用户所指是 CDP，则本轮已把未到控制阶段与实际访问阶段分开记录。

掉号自动登录本轮不做：

1. 不能用重输密码解决当前交互验证；反复登录还可能增加安全验证、MFA 或账号锁定风险。
2. 没有配置可供读取的 `credential_ref`，不应把密码硬编码到 GUI/脚本。
3. 应先分别证明“真实登录墙”“权限缺失”“Challenge”，再考虑仅对确定的 REAUTH_REQUIRED 做有界的普通登录恢复；MFA/Challenge 仍由人工处理。

## 六、代码、验证与发布记录

主要修改：

- `site_strategy/maintenance_document.py`：受限原页观察、精确冷关闭；不连接任意远端 CDP。
- `site_strategy/worker_runtime.py`：External 原页观察与冷关闭路径；Managed/登录状态检查的有界冷预算。
- `site_strategy/service.py`：原页轻分类与清理预算预留。
- `site_strategy/maintenance.py`：有界清理并保留已完成报告。
- `content_enrichment/quality.py`：可选跳过通用抽取，默认保持原完整正文链。
- `identity_worker.py / runtime.py`：维护接管阶段的无秘密诊断日志。

定向测试 **79 passed**；另外正式正文抽取/发布者/管线/浏览器回归 **99 passed**。覆盖原页精确绑定、错误实例拒绝、禁止冷加载通用抽取、观察仅使用 DOM 命令、冷关闭不启动 Worker、不关闭替代实例、清理超时保留检查报告、原页仍被阻挡时不伪造解除、现有来源/网络策略/Worker 回归。

源码提交：`6e6237b7`、`09518148`、`6098235c`、`8dbda93d`、`d56836d5`。均已进入 origin/main。中间 15 秒预算版本未解决问题，保留其失败证据，不将中间版本称为成功修复。

最终 Site Access 镜像：

`sha256:43573e4af2efc8f7edc9785d86fc2162bddb4e7fee7da229bd6c2b77c4dde958`

每轮均从当时实际 Site 镜像做精确父镜像窄覆盖，并通过既有 release lock、compose.current/state 发布账本推进。只替换 Site Access；没有重建 Supervisor、主 Chrome、Profile 或其他业务服务。备用在旧人工会话完成后，由正式冷接替机制单独停启，其新原页也已保留。并行的其他开发/发布不属于本次交付，不用整仓镜像覆盖它们。

最终 7 个修复范围源码按 Git/LF 规范化后，SHA256 与运行容器全部一致；Site 与 Supervisor 的实际 network namespace 一致。Supervisor 容器仍为 `617b4e1d59f9…`。

证据目录：`eval/reuters_root_cause_20261019/`。重要记录：`before-detailed.json`、`pages-before.json`、`recurrence.json`、`recurrence-dom.json`、`native-comparison.json`、`backup-canary.json`、`verified-real-block.jsonl`、`sustained.jsonl`、`tests-final.xml`。不保存网站密码、Cookie、管理令牌或 Challenge URL 查询参数。

## 七、当前人工入口与后续验收

主任务 `challenge_8b0f048ea7284bd3a4ac57b9016e059c`，原页 `014BFADC71E5B8946C801BEEA8D8A557`；已通过 Open Original 恢复 Viewer。主页面保留，不再错误引导去重开登录页。

备用新原页 `D8BBAF09B2F8985D9FD128B82A9581ED`，待人工处理。当前活动路由仍为备用，不把主身份人工任务的完成与自动回切混为一谈。

必须如实保留的剩余项：

- 两个新验证现场仍需人工处理，不能将本轮状态标为“Reuters 已恢复”。
- 人工处理后，须再次连续多 ticker 实际轮询并检查新正文与自然发布；单个 Verify 或一次搜索不构成长期验收。
- 如果同样快速再次被验证阻挡，下一轮应以新的完整时间线为基线，对更独立且获准的出口、真实搜索负载/页面生命周期做控制变量实验；不能又把旧治理拒绝当成新 Challenge，也不能直接归因为账号掉线。
- 当前没有证明一个无需人工即可稳定运行的 Reuters 访问组合，也没有完成自然新增新闻持续发布验收。该限制不是通过降低正文质量、放开时效窗口或清空故障记录可以解决的。

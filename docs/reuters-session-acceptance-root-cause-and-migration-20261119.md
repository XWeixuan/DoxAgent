# Reuters 验证会话接受性、生产身份迁移与验收

日期：2026-11-19，北京时间。机器回执采用 UTC；脱敏实验和运行账位于 `eval/reuters_session_acceptance_20261119/`。本文明确区分提供结果、浏览器送达、站点接受、实际业务恢复及持续稳定。

## 结论

1. 已将生产源 `source:reuters_site_search` 的 Main 从 `digitimes-de-1` 提升为现有 `reuters-backup2`，Backup1 仍为 `reuters-backup`。旧 Main 退出生产采集，Profile、Chrome 和配置快照保留，不复制账号到多个身份，不重建 Supervisor。
2. 已证明多笔失败发生在 **Chrome 正确送达验证 Cookie 之后，Reuters 的 DataDome 边缘层仍拒绝真实业务请求**。至少这些案例不能再用写入丢失、未携带、账号没登录或本地代理 CONNECT 失败解释。
3. 最有区分力的对照是：相同正式 Chrome153、Profile、出口及账号状态下，普通 HTML 入口获得的会话可以访问 HTML，却仍被搜索 API 拒绝；实际搜索 API 入口形成的原生会话在同一持久化身份中恢复了真实搜索。Main、Backup1 两组均取得 API200、正式 CRAWLER READY/BODY FULL 验收。该恢复没有新购码，也不是人工解题；现有 Verify 接口记为 `manual` 只是调用路径标签，不得归功于付费 Provider。
4. 这把问题范围收敛到 **验证结果的目标入口/会话上下文接受性**，但尚未证明 DataDome 的具体服务端判定规则。不能声称供应商算法错误、Profile 永久标记、某个 `s` 参数就是站点租户，或德国 IP 是唯一原因。
5. 本轮恢复和诊断补丁不等于自动付费解码已修复。没有把隔离会话实验推广为无限自动清 Cookie/换 Profile/购码机制；持续稳定和完整采集时效仍须由自然业务回执判断。

## 已完成的生产变更

| 项目 | 实际状态 |
|---|---|
| Source Scope | revision6；Main `reuters-backup2`，Fallback `reuters-backup` |
| 新 Main | 同一已有长期 Profile；正式 Chrome153；出口 `205.198.126.113` |
| Backup1 | 原 Profile/Chrome/出口 `205.198.126.114` 保留 |
| 旧 Main | 归档到 `publisher:reuters-retained-main-20261116`，非默认 publisher，自动恢复停用 |
| Supervisor | 未重建；Backup1 进程未重启 |
| Solver 代理绑定 | 只对新 Main 修正陈旧的 egress/parent 对应关系，沿用认证与目标 ACL |
| 18791 | 原生产代理实例保留，未重启；2Captcha 配置未替换 |
| 18792 / 新加坡测试转发 | 有界测试后已停止，无长期公网测试代理 |

迁移复用 `scripts/ops/promote_reuters_identity.py` 的 CAS、配置快照及回退机制。一次仅对候选 Main 做 graceful drain 以使有效出口与身份一致；没有直接终止旧 Main 或 Backup1。旧 Main 本轮开始前已被人工恢复，其历史 Challenge 原页此前已经结束，因此不宣称仍保存着那个旧原页。

## 失败链和对照证据

### 历史付费账，不把 APPLIED 算成功

`historical-chain-summary.json`：自 2026-11-12T23:24:40Z 的完整选定窗口中，27 行 APPLIED；26 行有送达证据；25 次有真实首个搜索 API 网络观察，其中24次 DataDome401、1次真实200。另一次200来自磁盘缓存，没有有效的 Cookie 送达证明；还有一行中断，没有充分应用证据，均不算真实接受成功。

### 本轮新鲜 Challenge 的少量付费对照

- 新 Main 的 Capzy 结果 ready，已知费用 $0.002。原生 Chrome 实际携带同一 Cookie 后，根文档或搜索 API 仍被 DataDome401 拒绝。诊断辅助程序首次取 body 时发生内部异常，不能误当 Cookie 写入失败；随后复用同一 paid result 取证，没有重复购买。
- 新 Main 与 Backup1 各一轮 EZ 的浏览器原上下文校验：校验端返回 HTTP200，JSON 只有 Cookie，未观察到明确 `accepted/pass` 标志；紧随其后的原生请求实际发送对应 Cookie，站点返回 DataDome401，再设置新 Cookie。因此 Cookie 被替换是拒绝响应后的结果，不能反过来说请求前就被本地覆盖。
- EZ 按文档单价两轮合计预期 $0.0052，缺任务级账单，不作已结算费用。自然生产 Solverify 的失败另列，不计入上述手动控制样本。
- 上述根文档401具备 `x-datadome: protected`、CloudFront `LambdaGeneratedResponse`、相应 DD 响应形态，未见登录权限层或 Squid 生成该401的证据。浏览器 TLS 对端证书仍为站点证书。

### 地区、Profile 与控制层

| 对照 | 结果与边界 | 文件 |
|---|---|---|
| Flower/SSRDOG 多个 SG/JP/US 订阅节点 | 503/504 等链路失败，不可称为 Reuters 拒绝；未将其切入生产 | `region-network-probes.json` |
| 新加坡服务器真实直出 `43.163.67.97` | 原生自动检查后实际搜索 API200、正文 FULL4508字符；仅隔离有界样本 | `sg-browser-verified-control.json` |
| 德国同出口全新原生上下文 | 同样可搜索200；说明不是仅换地区才能恢复 | `runtime-subscription-controls.json` |
| CDP Runtime.enable 开/关 | 两组原生表单搜索均200，开组有真实 console 事件；不支持把这个开关单独判为原因 | `runtime-subscription-controls.json` |
| 相同查询的实际 URL | 持久化失败与新上下文成功使用相同搜索 URL；成功为真实网络响应，不是 cache/SW | `native-request-comparison.json` |
| 有/无请求凭据 ABA | 三次均401；简单不发送 Cookie 无法修复 | `credentials-control.json` |
| 只移除 DD Cookie | HTML/API 曾200，随后正式 CRAWLER401而 BODY FULL；不是可靠根治 | `dd-reset-control.json` |
| 普通 HTML 入口会话对照 | 持久化身份 HTML200/API401，Cookie 确实随请求送达 | `cookie-context-control.json` |
| 实际 API 入口原生会话对照 | Main、Backup1 均 API200/7articles，原 listing 的 API200，正式 READY/FULL | `api-context-control.json`、`api-context-backup-control.json` |

API 与根页面的 DD 响应具有相同 hsh 指纹但不同 `s`（52442/44731）。这是实际观测差异，不是字段语义证明。旧 Challenge 的 `t=fe` 同时可能呈现 temporarily restricted，不能凭 URL 就购买或声称题面有效。

当前持久化环境没有发现 Playwright/cdc 全局属性证据、明显 UA/Client Hints 冲突或再次存储 quota 耗尽。非 DD 账号状态没有被清除；上述成功也排除了“必须更换整个 Profile 才能使用”这一强假设。未知的服务端接受条件仍不能仅靠浏览器可见证据完全还原。

## 代码修正：保留真正失败的搜索 API 证据

`reuters_sources.capture_reuters_search` 以前对 API 非200设置状态，却把 response body/url 替换为页面 DOM、headers 置空；若选择器先超时，更会只留下超时错误。这让后续排查无法可靠区分 HTML正常/API拒绝与整体页面 Challenge。

本轮新增独立 `native_search_failure` 脱敏摘要，保留真实 API 路径、状态、白名单头、拒绝层和有限 body 信号；DOM超时也附上已观察到的拒绝响应。Runtime 每个失败操作仅记录一条摘要，不额外发请求、截图或抓包。成功路径零新增响应读取。

诊断读取最多1秒，只有已知 Content-Length ≤64KiB 才读取 body，摘要处理最多16KiB；未知/过大则明确跳过。不得输出 Cookie、Set-Cookie 值、账号、查询串、验证码完整 URL 或原始正文。诊断失败不覆盖原始错误；正常维护页面仍由真实 DOM 判断，绝不把 API响应拼成伪造的 Challenge 原页。

回归：71项通过，覆盖 API拒绝/DOM超时、秘密脱敏、读取上限、成功路径、身份提升、双活及现有轻量取证。另运行旧 `test_source_redeployment_recovery.py` 有7项失败（旧 `fetch_anchors` fixture、已移除 UI 接口/旧返回值等）；用 HEAD 源码重新执行同样7项失败，见 `baseline-stale-tests.txt`，没有为通过测试而改变生产行为。

## 自然运行验收与剩余问题

Main 于 UTC23:29:57（北京07:29:57）完成 READY/FULL，Backup1 于 UTC23:34:32（北京07:34:32）完成同类验收。恢复前提升新 Main 后曾约38秒复发，该失败窗保留，不能以最早 canary 成功代替本次持续验收。

UTC23:41:04 的恢复后窗口：Main128次真实 CRAWLER SUCCESS，Backup1 40次；Main2条正式 BODY FULL。19次物理访问队列延期发生在23:30:20–23:30:56。后续 Task 全窗复核发现恢复后仍有两条原页衍生待办，不能用这些成功数宣布无 Challenge 复发。采集调度账中仍有后续逻辑单元 `site_queue_timeout`，与物理访问账分开保留，不宣布所有 ticker/QUERY/TAIL 每60秒完成。动态 TAIL 的数量会变化，不把退役 `required=-1` 单元混入当前覆盖。

下一步应依托自然请求，把接受与否继续绑定到实际 API/响应上下文；付费结果只在实际搜索接受后才算业务恢复。无需继续轮换更多供应商或增大等待时间。若长期仍被真实外部拒绝，保留人工入口与既有容错，同时考虑有授权的稳定数据接口；本轮证据不支持承诺单靠解码一定长期可用。

## 发布与收尾复核

- `d3b062e96a4710acdd3295dbd61151b3f55d4d3f` 已推送 origin/main 并通过标准精确 SHA 发布，仅替换 `v2-site-access`。两个修改模块的容器文件哈希与 Git 提交字节一致；容器内脱敏 smoke 通过。Windows 工作副本 CRLF 哈希不作为源码不一致的证据。
- 新 Main instance `1947c4dcc97d455a9487586ed0bf8488` / PID628618；Backup1 instance `02337cf2d9ef49ab9afc3c3a111e82c5` / PID273296；旧 Main instance `8465b07e2c064676a88f19b2e4b626e8` / PID1458。发布前后均保持，Supervisor 未重建。
- Task 使用 `opened_at`，不是 `created_at`。修正只读审计字段后查明：原 Main 页在23:30:57、原 Backup1 页在23:35:13各衍生一个新待办，且其后仍有自然搜索成功。发布后恢复协调重新接管，分别在23:45:16、23:45:11由 `fresh_probe` 清除。不是新购码成功，也不能认定期间整身份始终被外部封禁。见 `task-history.json`、`post-release-recovery.json`。需要后续专项区分同一原页的残留验证码/隐藏 iframe 与真实新阻断，不能仅统计新 Task 数就推断服务端再封。
- UTC23:48:58 的收尾窗口（自23:29:57）：Main242次、Backup1 99次真实 CRAWLER SUCCESS；2条正式 BODY FULL。物理队列延期累计97次，包含恢复初期及发布后窗口；当前有效26个 QUERY、5个动态 TAIL。现有成功单元间隔样本和队列延期不足以证明全部查询60秒完成，**采集时效验收尚未通过**。不能将成功访问数外推为完整查询周期数。
- 18792无监听，新加坡临时 relay 已停止，相关一次性私有目录已删除；18791实例不变。根盘剩余约28.7GiB、MemAvailable约16.7GiB。没有清 Profile、账号或其他会话代码；没有建立新常驻组件或后续监控自动化。

### 尚未完成的根治与具体下一步

1. **付费结果接受性仍未修复。** 已证实送达后被 DataDome拒绝，已排除这些样本的 Cookie遗漏/重复发送及请求前覆盖；校验HTTP200返回Cookie不是站点认可证明。服务端的 Challenge 有效期、签名与目标请求上下文之间具体关系仍缺供应商/站点侧证据。上述成功对照是两组独立身份的强线索，仍受顺序/时间等因素影响，不能宣称已经证明唯一因果。
2. **原页待办与业务恢复的状态一致性。** 用已有 target/generation、任务与能力成功时间线对齐，只允许真实阻断重新降低身份健康；历史原页与新业务页不同必须明确记账。需要先捕获原页是否实际遮挡/可交互，而非仅依 iframe资产存在。旧页可为诊断保留，但不应独自重置已被自然业务证明的能力。
3. **时效是独立瓶颈。** 继续按实际采集单元和物理访问区分排队、执行及恢复保护期；本轮不能把高搜索成功数包装成没有 `site_queue_timeout`。应先对齐单元所需工作量与两身份有效容量，再修保护期/调度，不能靠未经验证的并发放大、降低目标频率或反复换 IP掩盖。

本轮做到身份迁移、两身份实际恢复、失败接受环节收敛及诊断缺口修复；没有完成“自动付费解码长期可靠”和“全量QUERY/TAIL始终满足原时效”的验收。

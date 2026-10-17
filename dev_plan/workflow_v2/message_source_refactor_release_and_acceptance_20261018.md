# 消息源隔离、有效主备与人工维护：发布与验收记录

日期：2026-10-18（北京时间；原始收据使用 UTC）。实施依据为同目录 `message_source_isolation_failover_maintenance_refactor_implementation_plan_20261017.md`。本记录区分代码完成、部署生效、真实业务验收和仍需人工处理的站点，不将服务健康或 HTTP 200 等同于全文可用。

## 1. 交付与发布边界

重构代码已进入并推送 `origin/main`。生产 Registry 已迁移，Message Bus、正文补全、Site Access、桌面桥接和 GUI 已更新。Chrome Supervisor 已执行一次经授权的升级，Profile/Cookie 未迁移复制、未重建；之后为保留新产生的人工 Challenge 原页，固定运行 `main-60ec5c5d`，没有反复重建 Supervisor。

最后功能提交：`fe4a768bd61f3f32f819e04ae6cf1407a580d34e`。文档收据提交可晚于该功能提交，不代表镜像漂移。

| 组件 | 生产镜像 | 状态与范围 |
| --- | --- | --- |
| Site Access | `doxagent-site-access:main-fe4a768b` | 当前控制、隔离、维护验证及付费双文章/credential epoch修复 |
| Message Bus / Enrichment | `doxagent-v2:main-de00466f` | 新 scope 协议客户端；后续提交仅修改 Site 包、桌面和验收工具，无须重复替换 |
| Chrome Supervisor | `doxagent-chrome-supervisor:main-60ec5c5d` | 隔离生命周期、持久 manifest、资源上限；刻意保留当前原页 |
| Supervisor 待用镜像 | `doxagent-chrome-supervisor:main-fe4a768b` | 已构建，**未应用**最终冷缓存 GC 等细化；待人工现场可释放后协调升级 |

实际 image digest、组件源码 commit、依赖 DiffID 继承、容器 ID 和源码校验详见 `../../eval/message_source_refactor_plan_20261017/production_receipts/host/`（从本文件应通过仓库根目录的 eval 路径访问）。四个相关生产容器各自按声明 commit 校验 797 个已打包源码/提示词/API 契约文件，导出时无不匹配；远端无未跟踪的 src/prompts 覆盖。没有在运行容器中覆盖生产源码。其余业务容器 ID 未改变。

完整安装依赖的全量重构建仍受磁盘余量限制。本轮使用批准基础镜像、完全一致依赖清单和不可变源码层复用，不绕过源码一致性检查。最后 Site 细化构建仅替换完整 `site_strategy` 包，防止残留已删除模块。

## 2. 对照方案的实现

| 方向 | 已落地做法 | 不承诺的事情 |
| --- | --- | --- |
| 来源隔离 | source/publisher scope、revision/CAS、Identity 唯一归属；同一源不同 ticker 共享，独立源不共享；最终 publisher 重新解析正文身份 | 不用源头 Yahoo/Google 身份访问所有第三方正文 |
| 页面所有权 | 创建前 allocation intent/marker，绑定 instance/target；Challenge/Login 原子交接；跨连接只清理确属 BUSINESS 的页 | 不猜测关闭未知 blank、人工页，不宣称整容器升级可保存活页 |
| 控制隔离 | 每 Identity 独立 Python/Playwright Node、有界 IPC、epoch/PID/start-time fencing；控制退休与 Chrome 生命周期分离 | 无法确认旧写者退出时不会强行增加第二写者 |
| 故障与接替 | Challenge/auth/entitlement/parser/runtime 独立分类；一个同轨同环境冷备；正式业务 canary、24h/fingerprint、sticky、显式恢复主身份 | 配置存在不等于 READY；公开全文不能证明付费备用有订阅 |
| 人工维护 | Challenge Tasks 与常规 Login 分开；持久 operation、阶段/deadline、Later、TARGET_LOST 关联重开；原页只读检查，临时页验证正文 | 不自动填写密码、不绕过 MFA/CAPTCHA、不通过重载原页“验证” |
| 桌面可靠性 | 英文 Tk 双页签、异步 mailbox、失败解除 Working、缓存 inventory；root 固定动作桥接和 generation 校验 | 桌面无 Docker 权限、管理令牌和站点密码保存 |
| 内存生命周期 | 活跃主身份常驻，冷备 idle120s、低频 publisher600s；Managed 上限8、native 上限16，业务全局容量4；Supervisor 8/9GiB、2304PID | 增加上限不是保证所有身份同时启动；人工任务不占业务许可 |
| 磁盘治理 | 有界成功明细/小时汇总、结构化失败形状；UTC索引、分批清理；流式原子快照、排除缓存、每批一份 Registry、两个普通快照及保护集 | 未运行满7天，不能声称全盘净增长已得到长期验证 |
| 兼容与迁移 | reader-first、默认 preview、显式 CAS 及一致备份、唯一继承已有 Profile、不复制 Cookie；保留原鉴权配置与禁用入口 | 不回退到旧共享身份架构掩盖新模型问题 |

新增专用 publisher 身份时保留已独占的旧身份。例如 Seeking Alpha 继承现有专用 Profile、改为按需运行，而不是创建空 Profile 丢失既有登录。WSJ/MarketWatch 因原来共享源身份，获得独立身份，需要各自完成登录。

迁移最初覆盖18 scopes；随后保留既有独占 publisher 身份后 inventory 为19 scopes、37个主备成员（有单主 publisher）。不启用原来禁用的 CTEE。Registry 一致备份保留在服务器 `/site-data/registry/source-isolation-release-20261018/registry.before.sqlite3`，不导出到 Git。

## 3. 测试证据

### 软件与真实原生浏览器

- 最终功能提交 `fe4a768b` 的干净 Git archive 回归：**302 passed / 1 Windows symlink skip，100.93秒**，不混入本地其它模块脏改动。此前精确提交4801e831为301 passed/1 skip，300b37a9为302 passed/1 skip。
- 最后新增付费备用会话回归：使用真实Repository事务验证同一 LOGIN 首篇不关闭、第二篇才关闭、epoch只推进一次、新登录不能复用旧证明；GUI命令允许只提供session_id，不依赖重复传scope_id。维护/治理/迁移38项定向通过。
- 产品 Site 包、桌面脚本及本轮维护测试 Ruff 通过；不据此宣称整个脏工作区或其他模块通过。
- 隔离 Linux 环境实际运行 Chrome153/CfT、临时独立 Profile 和本地不转发代理：控制进程重启保留人工原页及未知页，丢失 createTarget 响应通过 marker 找回，Challenge 释放两层业务许可，临时正文验证精确收尾，Supervisor **进程**重启接管同 native PID/UUID，graceful cold start 保留测试 Cookie，双轨各自可用。该项没有使用生产 Cookie，也不冒充付费网站登录成功。

### 生产维护路径

- 桌面用户 `sudo -n` 固定 list 可用；无 Docker 组；不能读取管理令牌或写控制 socket；VNC 仅绑定宿主机 `127.0.0.1:5900`。
- 8次聚合 inventory 取样中位约0.95秒；这是小样本桥接响应，不是完整 GUI 的长期 p95。
- 原生 Reuters 冷备原页可通过维护打开，最新复测约4.13秒 Viewer 就绪；Later 约0.52秒释放 Viewer，原 target 保留。没有将 Viewer 关闭视为 Challenge 解除。
- 最后两轮公开源维护链实测：Open约4.11–5.69秒、ACK约5–14毫秒；随后单击 Verify约7.27–10.02秒，依次反馈 Challenge/Crawler/Body/Cleanup，结果 CLEARED、NOT_REQUIRED、READY、FULL，健康提交且会话自动关闭。其它4个 Task 的 target 引用未变。存在正常负载波动，不承诺每次严格5秒内打开。
- 早先一次立即 Verify 出现通用 Error，同一保留会话重试成功。原始异常文本未保存，不能断言其唯一原因；已为确定的页面导航期间 content-read 错误增加最多3秒只读等待，未刷新或导航人工原页，之后立即 Verify 成功。失败及后续成功 operation 均保留于脱敏收据。

### 真实列表、正文与冷备用

以下为只读业务 canary，不新增发布，不改变文章时间或业务 checkpoint：

| 来源 | 真实结果 | 限定 |
| --- | --- | --- |
| TrendForce News | 7条解析，2篇 FULL | 瞬时成功不等于7天稳定性 |
| TrendForce Press | 5条解析，2篇 FULL | 同上 |
| Globe Search | 10条解析，2篇 FULL | 自然各 ticker 搜索窗口仍待观察 |
| Globe Semiconductor RSS | 20条解析，2篇 FULL | 独立身份/状态，不借用 Search |
| InvestorsHub | 14条解析，2篇 FULL | 有宽泛市场文章，语义相关性非本重构的验收目标 |
| DIGITIMES English | 11条解析，2篇 subscription_required | 列表可用，未证明订阅全文可用 |
| Barron's | 主身份 Challenge，备用未完成认证 | 初期结果写 AUTH_REQUIRED；终态按主 Task 分类，不算已恢复 |
| Reuters / Investing | 待人工 Challenge / 失去原页任务 | 不因服务健康宣称恢复 |
| Yahoo 浏览器 recipe | http_404:phase=page_js_fetch | 旧 NCP/browser 端点契约问题，不是让用户登录即可解决 |

Globe Search 实际排除主身份后，冷备激活前轮约10.08秒、最终功能镜像复测约8.09秒，BODY 取得 VERIFIED_COLD，两篇不同正文 FULL；后续列表仍走备用，只有显式 ACTIVATE_PRIMARY 正式验证才切回。测试对象为公开 Managed 源，DIRECT/DIRECT，没有网络出口冗余，**不能外推为付费/代理备用已经验收**。

Yahoo 原有 NCP/RSS 业务优先级未改；本轮未修复供应商端点，也没有完成该源自然 fallback 的真实入库验收，列为独立遗留项，不扩大本次生命周期重构为供应商适配重写。

## 4. 人工待办与操作手册

在 xRDP 的 Site Login Maintenance 中操作；不需要 SSH、Docker、令牌或手工 VNC 地址。

| 来源 / Identity | 当前 Task | 操作 |
| --- | --- | --- |
| Reuters / reuters-backup | WAITING_HUMAN，原页存活 | Challenge Tasks → Open，完成人工 challenge 后 Verify；暂离选 Later |
| Reuters / digitimes-de-1 | TARGET_LOST | Reopen Lost Page，创建关联新任务再人工处理 |
| Investing / public-markets-nl | TARGET_LOST | 同上 |
| Barron's / dowjones-main | TARGET_LOST | 同上，完成 challenge 及登录后验证真实文章 |
| DIGITIMES English | subscription_required | 常规 Login 检查登录及账户阅读权；不能从短预览断言账户一定无会员 |
| WSJ / MarketWatch 独立 publisher | 新独立 Profile | 如需付费正文，分别登录；不能继承 Barron's READY 状态 |
| Seeking Alpha | 保留旧专用 Profile | 本轮未重新验证付费登录，按需要维护，不宣称已认证 |

三条 TARGET_LOST 来自升级前导入/核验：其中 Barron's 已有丢失或未确认场景，不可表述为三条确定存活原页全部被升级破坏。当前活 Reuters 冷备 original instance `77d03ea0e6e94d0a98b723b2debf5bba`、target `63913D08336CA62878B3320415447658`，后续 Site owner 替换未改变它。

维护结果分离解释：Challenge cleared 不等于已登录；已登录不等于有订阅；有订阅不等于 parser 或业务 canary 通过。任务可完成，但不同能力仍可能等待或失败。不要反复重登来修复 parser/endpoint 错误。

### 付费备用的两篇文章

只维护一个备用。选择其独立 Profile 完成正常登录后 Verify。首篇证明成功时保持**同一个 LOGIN 会话**，提示使用 Advanced URL 验证另一篇真实订阅文章；两篇证明达标才自动关闭。不要关闭后反复新建 LOGIN 来做第二篇，否则新 credential epoch 会使旧证明失效。高级 URL 仍校验域名与付费语义，不能拿两篇公开文章认证订阅能力。完成证明后还须真实冷启/canary，未达标不列为业务可接替备用。

主/备按相同 runtime、Chrome release、locale/timezone、代理访问类型约束，不再用 Managed/直连异构组合充数。代理地域依真实出口核验，不依节点名：本轮两实际出口在 DE，分别205.198.126.113/114；TR节点实际为185.40.106.84，直连为SG43.163.67.97。两DE出口来自同一供应商，未证明供应商级隔离。

最后收尾同时补齐了能力fingerprint与credential epoch绑定：显式登录先在同一事务提交新epoch，再写当前文章证明；同一会话第二次验证不再推进epoch，新登录不能复用旧订阅样本。这次fingerprint结构升级会使旧能力证明一次性失效，需真实canary/维护重新建立，不把旧缓存证明迁移为新READY。公开源的真实解析代码未因此改变。

代理地理校验先取得当前代理IP，代理元数据端点不可用时仅直连查询该公开IP的元数据；业务请求不降级直连，不进行TLS MITM。元数据字段依据 [IPWhois 官方文档](https://ipwhois.io/documentation)。

## 5. 资源处理与待完成部署

已按授权清理未使用构建缓存（包括 `docker builder prune --all`）和7天前 journal；journal实际可回收0。build cache 的逻辑回收19.17GB不等于根盘实际释放量，根盘由约1GB回到约12GB，最后收据约11.2GB，仍小于20GiB完整依赖构建参考线。**没有借此删除镜像、容器、代码、业务库或 Profile。**

受锁/进程核验保护的冷缓存维护实际回收159,283,203逻辑字节，不删Cookie；不把逻辑字节等同df增量。源码治理不能控制另一个研究系统的持续写入，本轮不扩大删除范围。

最终 Supervisor 冷缓存细化镜像已准备但未部署。当前保留活跃 Challenge 原页优先：待人工确认可以释放这些现场、无进行中的维护会话后，冻结最新 Task/target清单，协调停止 Site Access、优雅升级 Supervisor、恢复同网络 namespace 的 Site Access。Profile 保留，丢失原页准确记 TARGET_LOST；不能只重建 Supervisor 让旧 Site 继续指向旧 namespace，也不能声称此升级无损保存页面。当前生产已有保留/快照治理，但最终 native 缓存细化仍是明确剩余发布项。

## 6. 下一验收窗口及回滚

- 当前周六闭市窗口，不为凑10次成功强制轮询、不重置失败时间、不绕过60s/30min freshness/closed sweep。10自然目标周期、24–48小时跨窗口、7天资源净增长验收尚未完成。
- 人工处理上述站点后，必须分别验证主、备用、真实列表和最终 publisher 的正文；有会员需求的两篇必须确认为订阅文章，不可只验登录页或列表200。
- 最终镜像/source/hash在本收据目录；原 Registry 一致备份只用于有意识的灾难恢复，不能无条件覆盖后续任务和认证状态。回滚必须兼容新 scope/target/session 协议，不将旧共享 Identity owner 接回现行 Registry；常规回滚优先恢复兼容镜像并保留现有 Profile、新任务与CAS数据。
- 本轮没有启用/部署消息源维护 Codex Worker，没有改并行 Jev/初始化/SDK/MCP 工作区变更；60s采集、30min时效、去重、闭市sweep、监测词和Taiwan付费排除保持不变。

## 7. 可复现证据位置

仓库 `eval/message_source_refactor_plan_20261017/`：

- `native_acceptance_20261018.json`：隔离原生生命周期检查点。
- `production_receipts/host/{build,final,compat,pre-supervisor,post-supervisor}.json`：不可变镜像、依赖继承、生产配置与安全检查。
- `production_receipts/site/{migration,inventory,canary,fallback-smoke,maintenance-challenge-smoke,maintenance-verify-smoke}.json`：业务与人工操作路径。
- `production_probe.py`、`release_host.py`、`preserve_supervisor_scene.py`、`export_receipts.py`：本次定向操作程序，不是新的通用部署平台。

导出不含 env.before、Registry备份、Cookie、Profile、管理令牌或原始错误响应字符串。历史收据保留其当时状态；终态以最后 final/inventory 为准，不篡改早先失败记录。

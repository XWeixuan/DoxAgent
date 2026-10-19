# 爬虫消息源反复失效：跨事故机制复盘与架构根因审计

日期：2026-10-17，Asia/Shanghai。当前源码基线：`e36cbb9da562b1291afe48cb8c9878b551f735a3`。

本轮只诊断。未修改应用代码/Registry/代理配置，未开启或关闭生产维护会话，未导航生产网站、附加第二个 CDP 控制器、重启、发布或删除生产资源。新增的只是历史文档副本、只读取证/离线复现脚本及本报告。

## 1. 结论：需要调整的不是“再找一个更好的 IP”，而是恢复和隔离模型

用户对维护成本的判断成立，但不能把一个月的失败全归成同一个尚未修好的爬虫 bug。历史包含四种不同情况：确定性缺陷被修复后出现另一层缺陷；临时修复被后续部署覆盖；网站/代理真实产生新拒绝；实际一直未完成全文或人工维护验收，却被整体恢复印象掩盖。

本轮比以前更重要的发现是：**有些单独看来合理的修复，在组合后互相破坏。**

1. **“保护原挑战页”与“控制驱动恢复”存在真实冲突。** 保留页仍登记在连接的业务 target 集合；驱动恢复底层清理会关闭它，虽然上层开启了保护。随后数据库仍保留 MAINTENANCE 和旧 target，形成“需要人工恢复，但恢复现场已经被内部机制删掉”的状态。本轮已用真实清理方法离线复现。
2. **人工待处理状态没有与业务资源、其他站点维护解耦。** 留页不释放业务页面额度，任一维护会话全局禁止驱动计划回收；任一待处理挑战又阻止其他 Profile 创建普通维护会话。没有人工响应时，局部异常可以无限期影响控制层和维护通道。
3. **同一 Identity 的维护隔离，被错误表达为代理不可用。** InvestorsHub 当前的出口 READY，却因为共享的 Investing 身份在维护而返回 `egress_unavailable`；这条错误生成路径已离线复现。继续换节点不能解决这种失败。
4. **控制驱动恢复不等于真实 Chrome 恢复。** 当前 DIGITIMES 的 Chrome 主进程约占一核，存在 21 个业务分配标记空白页，业务报 CDP attach 失败；owner 的 `active_pages=0` 仍不能证明这些页面已收尾。10/13 修复只完整维护了当前连接内所有权，跨失效连接的失败清理仍可能丢失归属。
5. **“有备用”仍不等于能执行接替。** 用途 override 排除了备份；共享 Profile 维护不在候选排除原因里；部分 Runtime/transport 失败直接结束请求，不进入下一组合。离线测试已证明：两个健康候选存在时，首组合 CDP 失败仍只尝试第一个。
6. **修复验收的能力与实际生产工作负载不一致。** public verification 只需无已知拒绝标记的 2xx 页面，空搜索壳也能解除健康门控；未执行真实搜索 recipe。人工一次登录成功与持续“多 ticker × 多 L1 × 列表 + 正文 + 探测”成功不是同一实验。
7. **共享Identity的认证代际也会发生无谓联坐。** 验证一个站点，即使只是读取公开页面且没有Cookie变化，仍推进全Identity session_revision，使同Identity其他站点先前VALID记录变UNKNOWN。本轮已复现；这是共享身份模型里的内部失效，不是网站把人登出了。

因此，建议保留 Site Strategy、Browser Identity、Managed/External 双轨和既有正文/分发能力，**重构 External 的控制故障隔离、页面所有权和人工维护交接边界**，而不是重写消息总线、安装更多 watchdog 或先启用维护 Coding Agent。

## 2. 文档收集与证据等级

本地检索了本对话涉及的 `dev_plan/workflow_v2`、`docs` 及对应 `eval` 说明文件，冻结收集 **53 份 Markdown**：

| 类别 | 数量 | 使用方式 |
| --- | ---: | --- |
| 事故/修复/验收主链 | 25 | 从 9/21 初始勘察、9/22 对照实验，到 10/15 正式修复逐轮串联 |
| 存储、发布和证据目录旁证 | 15 | 查证共享故障与修复丢失；不把无关 Overview 错误当爬虫故障 |
| 需求/架构/维护 Worker 背景 | 11 | 对照原始边界；长文按相关章节参照，不等同于新业务复验 |
| 10/17 相邻存储任务报告 | 2 | 当前容量旁证，明确不是冒充本对话此前产出 |

收集目录：`eval/message_source_recurrence_20261017/history/`，保留原目录结构；原件没有修改。索引见 [历史文档索引](../../eval/message_source_recurrence_20261017/historical_document_index.md)，逐文件 SHA256、原路径、大小及基线见 [冻结清单](../../eval/message_source_recurrence_20261017/document_manifest.json)。合计约 789 KB。

“全部”指本轮能定位的相关已落盘文档，不声称恢复了未保存、已删除或仓库外未知文件。未把海量投研 evidence Markdown、Worker prompt、普通研究故障和 9/21 以前的无关方案充当本对话排查产出。

以下区分：

- **E1：**本轮生产只读事实或当前真实实现的确定性复现。
- **E2：**历史原始诊断/实施回执支持，但不声称当前仍存在。
- **H：**合理假设，必须另做实验，不能据此直接改生产。

历史文件中的“计划实施”“已发布”“一时可用”“长期稳定”分别判读。未将测试通过数或容器 healthy 替代业务可用率。

## 3. 事故 → 修复 → 下一次失败：真正的连续关系

| 时段 / 原始文档 | 当轮已知机制与动作 | 下一轮揭示的边界 |
| --- | --- | --- |
| 9/21 勘察 | 正文与采集两套 context、代理和冷却；CDP+proxy 创建临时 context，登录态不复用 | 统一治理必要，但统一后要承担公共 owner 的新故障域 |
| 9/22 三次 Barron's 对照 | 普通正式 Chrome 可登录；Playwright 直接启动失败；普通 Chrome+CDP 成功，重启后订阅正文可读 | 证明当时登录实验的差异，不证明持续生产采集无挑战，也没隔离每一个启动参数 |
| 9/22 双轨验收 | 独立 Profile/固定出口、External Chrome 常驻；已有误将一次出口探测失败记 UNAVAILABLE 的修复 | Profile 连续性解决了一个问题，没有解决长期控制连接/页面所有权 |
| 9/24 行业源上线 | TrendForce Managed+DIRECT；其他源不同路径；付费正文和订阅未全部启用 | 单次列表解析不等于消息发布/全文验收；来源增多扩大总资源图 |
| 9/28 恢复记录 | distribution 失败热循环、死 Managed context 缓存、闭市正文排队消耗 deadline、维护 VNC 顺序；Flower 两 NL 路实际同 IP | 节奏/正文预算有实效；两条名义备份仍关联，付费/challenge 未被这些代码解决 |
| 10/01 六源可靠性修复 | 跨 Identity 队头、取消 permit、清理无期限、同步日历/SQLite 拖慢事件循环 | 后续不应继续把所有 timeout 当网络；当时 Barron's 全轮已达 59–71 秒，容量没有闭环 |
| 10/02 新市场源 | 新增 Investing/iHub；受六 Chrome 上限影响，两站先共用 DIGITIMES 身份 | “不增加进程”的选择引入不相关站点共享生命周期、队列与维护故障域 |
| 10/04 根因及实施 | External Node V8 heap fatal，Chrome 活着但缓存 driver 失效；加 epoch/恢复、4h/RSS idle 轮换；两公共站从 DIGITIMES 拆到 public-markets | 修了死亡驱动，但仍是所有 External 共一个 Node；保护人工维护会使预防轮换失效 |
| 10/06 Reuters/Investing | 无 probe_url、错误 BODY probe、人工验证不清 circuit；纠正后仍有真实 challenge；换 Reuters 出口后仍拒绝 | 一部分是确定性恢复 bug，另一部分是实际站点限制，不能全写成“已恢复” |
| 10/09 满盘 | 网页成功后状态提交 SQLITE_FULL，公共 Site Access 500；隔离 storage，补搜索全失败契约 | 多个源恢复有效；Reuters/Investing/部分正文未稳定恢复，35GiB 余量不是持续容量治理 |
| 10/12 NL 固定路失效 | 固定出口失败，purpose override/未认证备用无法接替；Runtime 锁逃出 Bus 主循环 | 换 DE 当时确有业务成功；翌日 DE 又故障，说明没有独立故障域，不是 parser 重坏 |
| 10/13 SERVICE_BUSY | newPage 取消确认无界，target 创建失败归属不完整；Chrome 局部 OOM/工作集；API 替补依赖写库迁移 | 改 raw createTarget、当前连接 owned_targets、独立 finally，恢复 Yahoo；没有跨失效连接的归属延续 |
| 10/13–14 维护恢复 | goto 失败关页→GUI不出现；改先开空白窗口、后导航；补 HTTP 后备/当前延期状态 | 解决“死网络打不开维护窗口”；但自动挑战积累与其他维护互斥不是同一用例 |
| 10/14–15 MAIN 收敛 | 分支未合并、远端 overlay 未提交、Yahoo 配置丢失；正式归并 | 这是真实复发因素，不应继续把它当唯一解释；本轮五个关键 Site Access 文件与当前 Git blob 相同 |
| 10/15 正式修复 | Managed Singleton 跨容器残留、镜像源码权限、延迟 challenge、公开验证状态、CDP attach、selector 误重试、DOM词边界 | 短样本成功真实；当前又出现 native Chrome/CDP 不可用、原挑战 target 丢失、共享维护误报网络 |

这里不是“以前什么都没修”：例如 distribution 主库重复写入和 Managed Singleton 都有明确修复及验收。**真正没闭环的是这些修复之间的长期组合行为，以及故障资源如何退出、接替和恢复。**

## 4. 当前生产只读事实：与历史判断重新对齐

采样 UTC 10/16 21:47–21:55，即北京时间 10/17 05:47–05:55，非原子快照。证据见 `live-site.json`、`live-bus.json`、`live-metadata.json`、`live-chrome-processes.json`。

### 4.1 不是“最新 main 没包含修复”或“代理又全死了”

- Site Access 实际镜像 `main-c9e321d9`；Bus `main-6a3e19ba`。虽然标签不同于本地最新 HEAD，但 `runtime.py/external_runtime.py/service.py/browser_runtime.py/health.py` 的运行 SHA256 **逐个与当前 HEAD Git blob 相同**。Windows工作树 CRLF 原始哈希不同，不是代码漂移；统一 LF/Git blob 后相同。
- 六个出口的持久探测均 READY，最近成功探测仍在推进。`.113` 承载两个槽位、`.114` 承载两个槽位，仍不能算四条独立路。本轮没有额外测试网站连通性，READY 也不代表目标站一定放行。
- Site storage warning 但 unavailable=false，约 19.3GiB 可用，最近事务提交正常。没有本轮 SQLITE_FULL 证据。
- External driver READY、epoch=2、RSS约101MiB、年龄约15小时；活动页计数0，申请页计数1；部分 Yahoo 正卡在 `connect_over_cdp`，但未超本次45秒预算。

### 4.2 逐源现状及机制

| 来源 | 本轮事实 | 不能再据表面错误作出的结论 |
| --- | --- | --- |
| Reuters | 10/15 05:59 UTC起，digitimes-de-1 保留未验证 challenge；原 target 当前仍存在，包含 captcha iframe | 不是“用户从未通过”；历史有通过后再次拒绝。当前是待维护，不是所有失败计数都代表新网络请求 |
| Barron's | 四 ticker 最后成功约10/15 10:04–10:05；10:06 后主 Profile MAINTENANCE、auth仍VALID；保存的挑战 target 当前已不存在，Chrome只见初始空白page | `no_authenticated_profile_available`不能直接推出 Cookie 丢失。主组合被风控排除，备用未认证；维护资源本身也不完整 |
| Investing | 最后成功约10/15 11:45；11:46一篇 BODY challenge使 public-markets-nl 进入MAINTENANCE | 不能把两千多次失败看成两千多次网站挑战。很多只是本地人工门控 |
| InvestorsHub | 与 Investing 共用 public-markets-nl；当前 `SOURCE_DEFERRED:egress_unavailable`、ACCESS_RESULT network_ms=0；对应出口READY | 现有错误把共享 Profile 维护误报成出口故障；并未证明 iHub 正在拒绝这些请求 |
| DIGITIMES English | 三 ticker 最后成功10/16 06:44；之后持续browser_cdp_attach_failed。digitimes-nl-1 Chrome仍运行，HTTP元数据可读，22个page里21个是唯一业务分配标记空白页；主PID646在1秒采样约占一核 | 当前采集故障先发生在控制/实例层，不能只要求会员登录。native Chrome具体忙循环函数未查明 |
| TrendForce 两路 / Globe Search等 | 本轮仍有近期成功；TrendForce走 Managed+DIRECT、共享入口采集 | 是重要对照：并非整个 Bus、所有浏览器、所有 parser 一起坏 |
| Yahoo | 浏览器CDP仍有接入等待，HTTP/RSS后备可继续支撑轮询 | 源成功不等于 browser recipe恢复，后备在掩盖该分支不可用 |

另：public-markets-nl、yahoo-main 的一次 Chrome HTTP元数据读取在3秒内超时；Supervisor仍报告进程running。不能只用 PID 和端口证明页面控制可用，也不能凭一次3秒超时断言两个实例永久死锁。

## 5. 根因一：页面所有权、人工所有权与驱动生命周期互相矛盾（E1）

### 5.1 保存了挑战页，却没有完成业务 → 人工资源的真正交接

当前路径：

```text
业务申请 Manager permit + External permit + Page/target
  → 发现 challenge
  → gate.close，仅卸载导航门控
  → 把原业务 lease 存入 _maintenance_pages
  → Site/Identity 停新业务，原 lease 不释放
  → 等人（没有完成期限）
```

关键位置：

- `runtime.py:1348` `_retain_challenge` 保存原 lease，没有调用 abandon/转移计数。
- `browser_runtime.py:96` 公共页面 semaphore 先于具体 Runtime 获取，Managed/External共用。
- `external_runtime.py:169、274` 所有 Identity active_pages 汇总决定驱动是否可回收。
- `service.py:567` 只要任意 Profile 有 maintenance_session_id，全局 rotation_paused=true。

离线使用真实 Manager 和 retain 方法：两个额度被两张保留页占满后，独立 Managed 请求在进入其 adapter 前超时；释放后额度恢复。默认额度更大时，只是需要更多保留页，不改变机制。

**当前生产计数为0，不能声称现场正好因为四个 permit 被占满。** 复现证明的是确定性缺陷；现场持久维护状态则确实使回收保护长期存在，driver年龄已超过4h目标。

### 5.2 死亡恢复实际关闭原挑战页，上层却仍保留“人工拥有”的记录

`ExternalChromeRuntime._detach_driver` 在 rotation_paused 时不走上层 `_policies` target关闭；然而 finally 始终调用 `entry.cdp.close()`。`_RawBrowserCDP.close()`（`runtime.py:229`附近）会关闭 **全部 `_owned_targets`**。原挑战 target 未从该集合转出，因此保护被下层绕过。

本轮复现：rotation_paused=true、driver FAILED、真实 `_RawBrowserCDP.close`、集合中含原挑战target；调用 `_detach_driver`，确实发出 `Target.closeTarget`。不是只在代码上推测。

已有 `test_dead_driver_does_not_close_human_maintenance_target` 把 cdp.close 整体 mock 掉，只断言上层send没执行，因而没覆盖这次组合行为。单元测试分别正确，集成语义错误。

Barron's现场是持久挑战ID消失而MAINTENANCE未释放，与该缺陷相符；没有完整逐target删除日志，**不把离线复现冒充本次该ID删除的唯一已证实原因**。

### 5.3 一张无法完成的挑战页可以阻止其他网站开始维护

`ProfileUseCoordinator.begin_maintenance`（`service.py:186`）遇到任何 `_challenge_profiles` 就拒绝；自动挑战可以为多个Profile分别登记，普通人工入口却把它们当成“已有一个操作者在维护”。`close_profile_login`（2052）又禁止关闭未验证挑战。

后果：人尚未操作、一个挑战本身无法通过，或target已经消失，都可能挡住无关 DIGITIMES 的登录维护。**“同一时刻只允许一个Viewer/操作者”不应等价于“只要任何网站有待办，其他维护永远不能开始”。** 本轮对真实 Coordinator 已复现 unrelated-profile 被拒绝。

### 修复方向

建立真正的所有权转移：业务 target → 待人工 target，归还业务 permit，保留真实Chrome页/Profile但移出业务关闭集合；人工Viewer占用是另一个短期、单席位所有者。允许选择不同待办、离开当前待办但继续隔离该Identity，不强行关闭未完成人工页。

驱动 detach 不拥有人工 target 的关闭权；单个待办不禁止其他Identity的控制驱动恢复。恢复时检查精确target是否还存在；消失则显示“原现场丢失，可重新打开/重新验证”，不能保留不可达token死循环，更不能直接清掉挑战证据宣称恢复。

## 6. 根因二：只恢复 Python/Node 控制对象，没有恢复长期 Chrome 的真实任务资源（E1/E2）

### 6.1 10/13修复解决了单连接收尾，没解决跨断连的失败收尾

`external_runtime.py:397` 起用唯一marker创建并登记target，确实优于不知target ID的newPage。可是 `_owned_targets` 仍属于当前 `_RawBrowserCDP`；close尝试失败后，`_detach_driver` 仍释放entry引用。后续新连接没有旧集合。

所以存在完整链：

```text
Chrome响应异常/连接失效
 → 自有target关闭失败
 → 丢弃连接和entry
 → 新driver/新entry认为active_pages=0
 → 原Chrome继续持有旧业务target
 → 再试/再创建/再次失效
```

当前DIGITIMES的21个带唯一业务marker空白page，使这一问题不再只是普通about:blank猜测。它们来自明确的业务分配格式；但尚未逐个锁定创建请求、失联代际及创建时间，不将它们全部算作此次885轮失败各创建一个。

**诊断数的是“当前成功取得lease的Page”，不是Chrome真实业务残留target。** 用active_pages=0决定无资源、可恢复，会反复给出错误安全感。

### 6.2 newPage预算已修，但同一SDK取消等待仍存在于其他关键RPC

当前 `maintain_driver` 使用5秒 asyncio.timeout 包住 `new_browser_cdp_session`，随后也通过Playwright发送Browser.getVersion/detach。`_entry` 通过 `connect_over_cdp`，gate启动/关闭、content、evaluate也经过同类SDK通道。

本轮直接运行安装的Playwright1.63真实 `Channel._inner_send`，方法换成 `newBrowserCDPSession`：10ms取消预算触发后，40ms时task仍未结束，直到假transport释放才退出。这是10/13已确认的取消确认机制，不是只对newPage专有。

不能据此说所有RPC都会挂死，或现场15小时driver年龄必由此造成；但可以确认：**外层写了timeout，不足以证明恢复控制路径本身有界。** 尤其维护loop把预热、回收、出口恢复和站点probe串在一起，一个关键等待失效会延迟整个恢复循环。

官方SDK代码见 [Playwright Python 1.63 connection](https://raw.githubusercontent.com/microsoft/playwright-python/v1.63.0/playwright/_impl/_connection.py)；Python也明确取消等待可能超过名义timeout，见 [asyncio文档](https://docs.python.org/3/library/asyncio-task.html#asyncio.wait_for)。这里的因果以本地真实SDK复现为准，不只引用一般文档。

### 6.3 共用一个External Node使局部故障变成全体控制代际变化

`ExternalChromeRuntime`只有一个 `_playwright` 和全局driver lock/epoch，所有Identity映射到它。一次探测挑第一个entry；失败可以触发所有External detach。Chrome进程独立不代表CDP控制驱动也隔离。

10/04的Node heap fatal真实发生过；10/15另有Chrome native主进程长时间忙而CDP初始化失败；当前DIGITIMES再次表现出同类native/控制不一致。三者不能混成同一种OOM，但**现有公共恢复粒度都不能精准对应故障粒度**。

### 修复方向

以现有 Browser Identity 为控制故障单元，External每Identity一个可重建的小型控制worker/Node，不增加Chrome数量、不换Profile/出口；保留单一owner API与统一Site策略，不引入分布式集群。

页面所有权归Identity生命周期而不是某条CDP连接：小型自有target记录至少含target ID、allocation ID、controller代际、业务/人工owner、是否待清理。重连后只对明确自有目标核对；旧ID缺失则结案，关闭失败则保留，不按blank批删。可以复用现有Supervisor/Identity状态，不需要通用租约数据库。

控制worker无法有界退出时，进程边界使旧控制通道可替换而不停止Chrome。连续确认nativeChrome无法执行真实目标命令后，且无人工操作，再按原graceful drain/same Profile方式恢复单实例。不是按年龄/内存周期强杀，也不把rootcause未知的native busy loop写成已永久解决。

**nativeChrome具体CPU忙循环仍需在下一次恢复前采样主线程调用栈/trace，保留最小target事件。** 当前1秒主进程CPU样本只能证明忙，不足以识别Chrome源码函数。本轮未attach调试器暂停生产。

## 7. 根因三：组合候选只知道Site健康，不知道Identity实际维护；错误路由因此误导修复（E1）

### 7.1 正常出口被报成egress_unavailable

`effective_candidates`（service.py:1041）检查enabled、override、egress、auth、site cooldown，却不检查Profile operational_state。`health.candidates`也只看组合站点健康。

业务获得预算后才进 `profile_use.business`，此处抛ProfileUnavailableError；外层catch只执行 `unavailable += 1`。最终 `service.py:1498` 把无attempt且unavailable统一写成egress_unavailable。

离线真实Registry/Service复现：唯一出口READY、Profile在MAINTENANCE，候选视图selectable=true/skip_reasons=[]；访问结果却EGRESS_UNAVAILABLE、0网络attempt。

这解释当前InvestorsHub：Investing BODY挑战暂停了共享Identity，iHub自身Site健康未必坏，但被归到网络错。**本轮不用IP查询200代替网站验收；判断“当前没有真实iHub网络尝试”的依据是业务门控路径及network_ms=0。**

### 7.2 Runtime故障直接return，备用存在也不执行

`service.py:1400`附近，TRANSIENT_TRANSPORT/RUNTIME_UNAVAILABLE/UNKNOWN直接返回SERVICE_UNAVAILABLE；没有推进后续组合。只有部分风险/资源分支continue。内容错误不随便换身份是合理的，但独立Identity的CDP接入失败不该自动等价于“所有路都不可用”。

本轮两个候选、首候选返回browser_cdp_attach_failed，实际只调用首候选一次，第二个没有尝试。

此外生产 iHub/Investing 的crawler、body override仍仅一个External组合；候选名义登记的Managed不是现成后备。DIGITIMES德国备用又与Reuters待人工Identity相同。因此不能简单放开所有组合解决。

### 修复方向

候选解释与实际准入使用同一套Identity/Profile占用判断；维护、控制失效、认证、代理、队列分开分类。站点拒绝不污染其他Site健康，但共享Identity维护必须明确传播为identity_in_maintenance，不再伪装网络。

规定每类错误的动作：内容/权限不盲换；可重试传输有限重试；单Identity控制失败可尝试独立、用途适用的健康备用；人工挑战保留原页并隔离当前Identity，本次不继续访问，但后续业务允许选择已准备好的另一Identity。不能让保留一个挑战页同时永久禁止全站接替。

## 8. 根因四：共享Identity的选择依据由认证关联滑向“哪里还有容量”，形成不相关站点联坐（E1/E2）

原目标是Dow Jones同认证体系、同出口环境共享，合理；10/02为避开六Chrome上限，将iHub/Investing并入DIGITIMES，10/04再拆到同一public-markets。**虽然少了Chrome进程，两个无认证关联网站仍共用维护、控制和单并发预算。**

当前一篇Investing BODY challenge导致两个站点、四个ticker停采，已经是这项取舍的真实后果。不能只强调“站点健康分别保存”，因为Profile排他才是更下层的阻断。

另一个问题是容量没有按实际操作量闭环：四ticker × 两个by-ticker源 = 每分钟最多8个列表意图；还叠加正文、probe、人工验证。若列表平均占Identity超过7.5秒，即使不算正文，该单并发Identity也装不下8次/分钟。该7.5秒是容量界限，不是本轮测出的平均耗时。10/15实际出现正文单次约32秒、10/01全轮超过60秒，已说明不能靠25秒队列等待保证吞吐。

当前Site Access幂等key含整个request及request_id（service.py:1000），主要防原操作重试，不是跨轮/跨ticker同一实际query的共享采集缓存。Bus已有distribution共享、ArticleHub正文复用，不应再另造一套去重总线；但相同L1查询和浏览器列表的重复工作仍值得按实际identity/URL/recipe/窗口合并。

### 修复方向

- 保留Dow Jones认证关联共享；不相关且频繁需要人工挑战的站点，不再为了省一个Chrome无条件共享维护故障域。iHub先验证既有Managed候选，同路径/正文连续通过才放行；否则独立Identity。Reuters不应长期借DIGITIMES认证身份承载恢复。
- 同Identity按页面任务串行治理；待执行的相同实时poll意图可合并/替换，不堆历史即时轮次。相同实际搜索query/分页/窗口可共享原始召回，再分别归属ticker；不能混并不同查询或修改L1定义。
- crawler和body分别统计Identity服务时间、等待时间、有效完成量；用实测负载决定少量拆分，而不是统一提高并发或拉长timeout。
- 60s配置保留，30min实时/闭市cutoff不变；不足容量明确延期，不伪造60s实际成功。

## 9. 根因五：验证与真实能力不等价，短时修复结果被过度外推（E1/E2）

### 9.1 public_access_ready不是search_ready

`verify_identity`（service.py:2137）在verification_kind=public_access、无已知拒绝、2xx时直接public_access_ready；并未调用Reuters/iHub/Investing的正式recipe/parser。离线返回200+空搜索壳，真实Service仍接受并清访问健康。

`verify_login`处理保留页时不再次导航，这是避免重触挑战的合理选择，但没有新导航response时使用状态200。因此必须把“当前页面无挑战”和“当前搜索功能有效”拆开，不能从推定200自动提升业务可用性。

10/06已指出probe路径错，10/15又修public UNKNOWN收尾；这些修复让维护能结束，却没提升验证的实际业务强度。历史多次“正常登录页/首页”与“提交登录后被拒绝”的用户证据也说明这一点。

### 9.2 列表recipe仍有提前return，公共挑战交接并非所有路径必经

`runtime.py:1244`附近Barron's专用卡片提取后直接return；如果初始DOM不是challenge，卡片等待期间变成challenge且helper返回HTML而不抛异常，便绕过下方最终retain步骤。Service之后虽能分类拒绝，原页却在finally关闭。Reuters正常recipe也有自己的提前return。

这是当前控制流缺口，不声称本轮已经用真实Barron's网页逐次复现。应将“成功内容/拒绝/挑战证据形成与交接”变成每个recipe统一出口，而不是每个catch再补一个特判。

### 9.3 CDP路线与成功实验并不完全一致（H，不能当已证实WAF原因）

9/22成功实验没有请求拦截；生产 `_DocumentNavigationGate` 对Document启用Fetch暂停/继续，raw-CDP对page启用autoAttach+waitForDebugger。gate本身承担跨站与内网安全边界，不能直接删除。

另外当前 `connect_over_cdp` 未传no_defaults。官方说明默认会应用focus/media/download等context override，`no_defaults=True`可保留默认context原设置，见 [Playwright CDP参数](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)。这不是UA伪造，但说明“普通Chrome启动”不等于“attach后绝对未变更环境”。

**不能断言Fetch、focus override就是DataDome识别原因。** 9/22成功attach实验也可能使用默认值。后续应在隔离身份、同出口/版本、人工登录后，按完整生产任务序列比较纯观察与现有gate、默认与no_defaults；不自动解挑战，不把网站放行当指纹因果证明，不牺牲内网/跨站边界。

### 修复方向

人完成挑战后先在原页做实际能力验证：搜索需合法结果或真实空结果；ticker列表需正确instrument和卡片；正文需同现行质量链的FULL/SHORT_FULL/权限结果。验证成功仅提升对应site/purpose/Identity能力，不让一次首页放行清全部能力。

正式成功出口统一提供结构化recipe结果、拒绝及原页归属；必要诊断保留有界DOM，不在每轮成功时回传整页megabytes。Yahoo已改为结构化返回，Reuters目前仍返回完整page.content，可以延续相同思路，不重写正文抽取器。

### 9.4 “验证了兄弟站，原站却需重新认证”的内部代际失效（E1）

`verify_identity`每次都将Identity runtime的session_revision加一，再只更新被验证Site的observed_session_revision。`_auth_state`（service.py:1638）对其他Site发现observed revision较旧就返回UNKNOWN。实际Cookie/Profile可以完全没变。

本轮在真实临时Registry中建立两个Site共享Identity：兄弟站已VALID；对另一站重复public verification，没有启动浏览器、没有修改Cookie，兄弟站有效状态从VALID变UNKNOWN。原站记录本身还在，但业务认证准入不再接受。

不能把此复现当作当前Barron's主路失效的唯一原因：当前主路另有真实challenge、target丢失，备用也未认证。它证明的是Dow Jones等多Site共享身份后会产生另一种可确定避免的认证联坐。

修复需区分**凭据/session真实变更代际**与**某Site验证观测版本**：只读验证不推进全身份凭据代际；确实重新登录、退出、恢复旧Profile等才按真实影响使相关认证观测失效。不为规避代际检查把所有兄弟站一律复制成VALID。

## 10. 根因六：基础设施与发布仍能使“修源”本身制造下一次停采（E2，部分当前旁证）

### 存储不是纯运维噪声

10/09确认满盘让网页成功后的状态提交失败，所有依赖Site库的源共同坏。10/13才定位到更深写入机制：约2287篇共享文章形成约1134万delivery，98%多为过期拒绝，run_id使跨轮幂等失效；技术poll历史不断复制进Read，GC被不相关gap全局保护。这些已有修复，不能继续把当前磁盘增长全归同一旧bug。

10/17相邻存储审计又发现：MAIN构建commit/source ARG在依赖安装前，按发布重新生成uv/Playwright大层；十个不同Playwright DiffID合计约9.52GiB。于是“频繁修源→频繁build→系统盘增长→共享服务降级”构成另一条可修的反馈链。原报告见收集目录adjacent类别；本轮未复做全部镜像层取证，也没执行清理。

### 发布漂移既真实发生，也不能成为永久借口

10/04–14很多修复是生产overlay，没有自动回流main；后续全量重建确实丢过源码和Yahoo路由配置。10/15已收敛。当前五个关键文件与Git相同，说明本次核心生命周期/候选缺陷**就在正式代码中**，不是再合并一次过去补丁即可解决。

### 修复方向

只保留一种正式源码/配置发布入口，逐服务记录确切版本、必要源级配置和验收；不要求无关服务每次都同标签重启。发布身份放稳定依赖层之后，正常收尾本次中间资源；既有GC/分发幂等继续验收。不要增加全局磁盘门控平台或拿清库代替业务根因修复。

## 11. 各来源真正应解决什么

| 来源 | 程序可修部分 | 仍不能程序保证的部分 |
| --- | --- | --- |
| Reuters | 独立Identity/控制恢复；原页可恢复；search能力验证；已验证备用；取消控制级等待 | DataDome是否持续放行、人工挑战、代理信誉；目前缺长期同负载A/B证据 |
| Barron's | 原挑战target保护、丢失现场恢复、公开列表与付费正文能力分层、真实认证备用、recipe统一出口 | 账号权益与WAF限制，不能用auth VALID保证所有文章 |
| Investing | 与iHub维护故障域解耦；正确模板和内容等待；完整生产业务序列验收 | 真实challenge/Pro权益，不应自动解或伪判全文 |
| InvestorsHub | 去掉共享维护误报网络；验证Managed或独立External；内容模板准确分类 | 实际页面变化及目标站拒绝，未通过实测的组合不能直接启用 |
| DIGITIMES English | native实例/CDP可控性、跨连接自有target收尾、可执行备用；之后才验付费正文 | 订阅权限，当前不能靠parser放宽解决 |
| TrendForce | 保留较简单的Managed+DIRECT、共享入口；应用公共生命周期改进，不为了统一而迁External | 分页/网站改版与普通网络波动；它稳定不证明“所有源应回Managed” |
| RSS/Google/Yahoo后备 | 继续HTTP独立链、原窗口/最终publisher正文；显式报告成功分支 | 聚合源被最终publisher拒绝，不等于feed本身失败 |

## 12. 建议的架构调整：保留业务合同，替换故障边界

```text
原Bus / Enrichment / Site Registry（不重写）
    → Site/purpose解析与可解释候选
    → Browser Identity
         ├─ Managed：现有受管轨
         └─ External：Identity自己的控制worker/Node
               → 原Supervisor持有的Chrome / Profile / 固定出口

Identity页面所有权：业务页 / 等待人工页 / 待收尾页
人工Viewer：单席位，可选择不同待办；不占业务页面额度
```

这是建议的实质调整，不是已经实施。可按以下顺序落地：

### P0：先闭合本轮已经复现的矛盾

1. 真正移交挑战页所有权/额度，driver detach不关人工页；准确处理原target不存在。
2. 普通维护单席位与待办集合分离，允许切换/暂存待办而不放开故障Identity业务。
3. 候选与准入统一Identity operational判断；修正错误分类和明确Runtime故障接替。
4. 统一recipe结果/挑战交接出口；public验证调用实际业务contract。

### P1：把长期External变成可独立恢复的单元

1. 每External Identity独立控制worker/Node；控制层失效只重建自己的worker，不重建全部Chrome。
2. 自有target归属跨控制连接延续；人工页不进业务关闭集合；真实失败收尾不丢记录。
3. 不可取消SDK等待由控制worker退出边界解决；避免继续把timeout包在无界取消确认外层。
4. native实例持续不可控时受控恢复同Profile单实例；先留采样，再恢复，保留人工保护。

不需要Kafka/Redis/Kubernetes、通用资源治理平台、每站点一套GUI或完整新后台。额外控制worker有Node/Python内存成本，应量测，不能称免费；不等于新增同数量Chrome。

### P2：降低重复工作和不必要联坐

1. iHub/Investing等无认证关联的高风险共享拆分按实测处理；先用已有合格Managed组合，不盲增Chrome。
2. 复用已有ArticleHub/共享采集去重，只合并完全相同原始查询/列表意图，按Identity公平调度实时列表和正文。
3. 保留60s目标及原实时/闭市窗口，用实际服务时间检查容量；不过载承诺不能靠队列timeout实现。
4. 修正式发布大层重复成本，保持源码/配置回流；维护Codex Worker仍关闭，基础确定性恢复不依赖模型。

## 13. 怎样证明这次不是下一轮再修

需要验收“故障链”，而不只是healthy/200/两个FULL样本：

| 验收场景 | 必须看到的结果 |
| --- | --- |
| 原挑战页保留→控制driver死亡→重连 | 原Chrome/Profile/人工target保留；无业务permit占用；不会误关人工页 |
| 同时三个Identity待人工处理 | 其他正常Managed/External源继续；可以选择DIGITIMES维护，不被其他待办阻塞 |
| 挑战target已消失 | 明确现场丢失及重新打开路径；不死循环recover，也不伪造已通过 |
| CDP command不回复、取消确认也不回复 | 控制worker有界替换；其他Identity继续；不再依赖同一SDK通道完成救援 |
| 自有target关闭失败且跨driver代际 | 记录未丢，重连后精准重试/结案，target总量不随事故次数累积；不动非自有页 |
| 首Identity Runtime失败、有合格独立备用 | 真实进入备用；未认证/override排除的路不会冒充ready |
| 一个Site挑战、另一个共享Site未被拒绝 | 状态显示Identity维护影响，不显示假代理故障；不无依据清兄弟Site健康 |
| public页面200但无搜索/列表结构 | 不算业务能力恢复；真空搜索结果可算，DOM错误不可算 |
| 同Identity验证一个Site、没有凭据变化 | 其他Site既有认证不无谓失效；真正退出/更换会话仍正确失效，不互相复制VALID |
| 四ticker、多L1、列表+正文+probe+人工验证正常负载 | 按真实attempt、等待、完成和freshness计量，不用失败计数等价于网站请求量 |

建议正式观察覆盖至少24–48小时、一次闭市sweep和下一实时窗口，再保留一周简洁汇总；期间出现真实challenge不等于架构验收自动失败，**是否有无关源联坐、资源泄漏、错误归因和人工恢复死路**才是本轮架构标准。不能拿“没人维护所以仍失败”掩盖维护通道自己的不可用。

业务效果用以下少量指标即可，不另建复杂监控产品：唯一实际采集operation的成功/真空/拒绝/延期；Site/Identity当前阻断及持续时间；原生target与业务/人工所有权差额；每Identity等待和占用；新文章最终publisher正文结果；30min内有效发布覆盖。技术计数与网站真实attempt分开。

## 14. 本轮复现、取证及边界

- [复现与取证脚本](../../eval/message_source_recurrence_20261017/audit_probe.py)：默认只跑本地桩/临时SQLite；`--remote`只GET现有诊断、只读SQLite和Supervisor status/Chrome HTTP元数据，不导航网站；`--processes`只读进程CPU计数。
- [复现结果](../../eval/message_source_recurrence_20261017/mechanism-results.json)：回收被保留页阻止、跨Runtime额度占用、无关维护被阻止、真实SDK控制RPC取消等待、真实底层清理误关人工target、维护误报egress、200空壳验证、兄弟Site认证无谓失效、Runtime失败不接替。当前实现下均取得预期证据，不是修复后的通过声明。
- 既有两组相关回归仍为**18 passed**（`existing-tests.xml`）；新增机制探针却能复现上述冲突，说明现有测试缺少跨对象/跨代际组合用例。探针脚本Ruff通过（嵌入远端脚本的长SQL/JSON字符串忽略E501），编译检查通过；53份副本hash、五个运行文件与HEAD及报告相对链接核对通过，见`validation.json`。没有声称全仓测试通过。
- 生产字段只保留诊断所需身份、配置和状态；不采集Cookie值、网站密码、管理令牌或代理订阅。iframe标题按query截断，未将挑战query参数留在落盘metadata中。
- 取证第一次误用access_events.created_at，实际字段为occurred_at；已修查询重取，没有将查询失败当生产服务坏。一次直接比较WindowsCRLF原字节出现hash不同，已用Git blob/LF复核为一致。
- 文档收集脚本需PowerShell7；旧Windows PowerShell按非UTF8解释脚本首次报解析错，未执行复制，改当前pwsh后成功。53份副本逐文件hash一致。
- 近期events为各站最多180条有界样本，不是两日所有网络访问历史；PollState是当前durable截面，也不是不可变全量失败日志。没有据此虚构一个月/两天的精确可用率或全量WAF原因分摊。
- 此处已确认内部缺陷能导致哪些结果；nativeChrome具体函数、网站私有风险模型、Fetch/default override对challenge的因果仍未证实。不给这些未知点套上“已彻底定位”的标签。

本轮用了project-discovery核对真实基线及发布差异；按web-scraping的内容有效性原则区分列表、拒绝页、权限和正文，没有使用stealth、随机指纹、Cookie注入或自动挑战处理。

**最终判断：保留双轨和站点策略，但必须把“长期身份”与“可以随时失效的控制连接”真正拆开，并让人工维护、业务预算、target归属和健康候选共享同一套准确生命周期。当前最确定可修的不是某次401，而是这些对象的所有权、恢复权限和故障隔离不一致。**

# Reuters RETRY 与持续失败消息源修复（2026-10-28）

本轮以实际部署 `74c07266` 和新的独立 Collector 为基线，保留其他会话未提交的 Google News、交易、前端和 Compose 修改，不整体重写或重启 Supervisor。基线证据 `eval/source_recovery_20261028/baseline.json`。下文明确区分已证实机制、实现修复与生产验收；不会把付费 ready/APPLIED 或一次 200 当作长期稳定。

## 已证实机制与首批修复

1. **Reuters 备用原页仍在，但原生 DOM 命令超时。** 同 instance/target 精确核对成功，DOM.getDocument 8 秒未返回。旧 free RETRY 在 10-27 12:34Z 记录 UNCERTAIN，此后没有新付费任务。旧实现甚至在前置场景检查、坐标读取尚未执行时，就标为“已可能点击”，前置失败也会冻结事件。新实现区分 PREPARING、NOT_DISPATCHED、实际输入前的 UNCERTAIN、CLICKED；已知未点击的失败不消耗点击额度。旧 UNCERTAIN 仅能基于新鲜、同 instance/target 的可见 RETRY 控件作有界复核，仍最多两次实际尝试；不重复购买、不改写验证码 URL、不模拟滑块答案。
2. **先 attach、后清理形成循环依赖。** 原代码必须先完成 Playwright 对整个 Chrome 的 CDP attach，才能清理旧 BUSINESS 页面；未初始化/高负荷的遗留页又能阻断 attach。改为先用独立 browser-level CDP 核对持久所有权、清理遗留业务页，再 attach。该清理连接不启用 autoAttach，不暂停或接管任何人工页。热连接额外低频重试 CLOSE_PENDING，跳过 LIVE/ALLOCATING 和人工现场；失败继续留待收尾，不伪造 CLOSED。
3. **真实进程资源压力，而非只看 free。** Supervisor pids.events 已命中 max；后续 docker exec 实际报 Resource temporarily unavailable。历史 memory.events oom_kill=11 不是本窗新增 OOM 的证明。public-markets-nl 单身份有 5 个 page、122 个 iframe，进程 RSS 求和约 4GiB（包含共享页，不能当精确独占内存），约 862 线程；其业务 CLOSE_PENDING 页面仍物理存在。不能将所有 457 个未终结账本记录都当作真实活页；按当前 instance 的原生清单核实后再治理，不清 Cookie/Profile、不杀人工页。
4. **CTEE 游标自锁。** 实时轮询沿用 page 并逐步越过 max_pages，while 从不执行，永久空结果且无物理列表证明。实时固定从首页开始；max_pages 约束每批实际请求数，补抓可继续 page=9/10；真正结束后复位游标，未结束不伪报 window_done。保留原时效接纳及语言/分发策略。
5. **TrendForce News 不必要地依赖浏览器。** 同宿主机现行 server-direct 公开 HTTP 实测 200、现有解析器得到 7 条。复用 Press Releases 已有的 public_http 优先、单次失败后浏览器 fallback、60 秒传输重试冷却，不新增代理轮换或绕过私有登录。明确保留实际物理请求与列表证明。

## 其余基线失败与边界

- Yahoo 必需浏览器频道：RSS 正常不能抵消 SUPPLEMENT_HEAD 的 site_queue_timeout/access_budget_exhausted。当前仍需核实旧实例残留页及资源压力、真实导航/控制耗时，不能只加超时。
- AviationWeek 两入口：现行固定 HTTP 路由均 403，定向实测仍 403；尚不能归因 parser。继续测试普通 Managed 浏览器可用性，不能把拒绝页当成功列表。
- IBKR：过去一小时 TimeoutError 多，但采集进程最近重建后状态需重新观察；不未经证据修改另一会话交易代码或 Gateway 登录。
- Newswire RSS：有 ConnectError/ConnectTimeout，需区分其 Newswire/NCP 链路和网站浏览器，不把 API 故障归为 Challenge。
- 其他来源偶发的发布窗口 ConnectTimeout/CANCELLED，与当前持续失败分列；不为历史偶发失败改动已恢复的健康来源。

## 验证与发布

第一批核心回归 70 通过、1 条有条件跳过；新增适配/控制回归 29 通过（批次有重叠，不累加去重总数）。后续补记生产精确版本、保留现场、paid task/费用/应用/原页通关/正式列表/正文/复发与剩余人工事项。

首批整合回归 91 通过、1 条有条件跳过。`cc628390` 于 UTC 2026-10-27 18:24:21 完成 Site Access/Collector 窄发布，Supervisor 容器 ID 未变，Collector 的 `host.docker.internal:host-gateway` 保留。更新后 public-markets-nl 从 5 page/122 iframe 降为 2 page/无 iframe，RSS 求和从约 4GiB 降至约 0.4GiB；Reuters 备用原页与 Chrome PID/instance 未变，但 DOM 仍超时，不能宣称其已恢复。

进一步移除 Challenge 的 Open Original 对 Playwright attach 的依赖：已保留的 external 原页经持久所有权、OPEN 会话、精确 instance/target 校验后，直接选择 Supervisor Viewer 并激活该页；只对该页尝试解除调试等待、恢复 active 生命周期，不导航、不重新建页、不作 CAPTCHA 输入。渲染器不可读不再阻止 Viewer 开放。正常新建登录和 Managed 路径不变，Verify 仍要求真实原页和业务合同通过。相关 85 条回归通过。

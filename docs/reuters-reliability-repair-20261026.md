# Reuters RETRY、采集与正文可靠性修复实施记录

本轮依据 `dev_plan/workflow_v2/reuters_retry_solver_poll_body_root_cause_repair_plan_20261026.md` 实施。此文件区分代码完成、生产发布和持续业务验收；不会把人工恢复或一次 canary 成功归为修复后的持续稳定。

## 已实施

1. **精确 RETRY 准备。** 从当前原页 DOM 所属 iframe/OOPIF 读取可见控件与加载状态，而非依据 URL 中 `t=fe` 就购买。只有唯一可见、未禁用、未被覆盖的 RETRY 控件可以被原生鼠标点击。动作绑定原 instance/target/frame、恢复 lease 和 ownership；人工 Viewer 打开、身份版本变化或帧换代都会停止。动作前持久化 `UNCERTAIN`，中断不盲目重放；每事件最多两次、间隔至少 60 秒，不尝试求解拼图或改写 URL。IP_BANNED、DENIED 不刷新/购买；未加载题面不能购买。
2. **免费准备与付费分离。** FAILED/APPLIED 后仍允许有界免费准备，但该事件绝不再次购买；PENDING 先收账、不改变场景。旧未付费准备在事件结束后 SUPERSEDED，保留付费墓碑、未知费用和原页。原有 20/24h、2/10min 限额保留。
3. **结果归因和复发。** `solver_verification` 与 `business_recovery` 分开。人工/确认窗外的恢复保留关联，但不改写为解码成功；窗口内只能证明应用后恢复，不能证明 Cookie 是唯一原因。CRAWLER/BODY 各有 capability epoch、recovered/last_success/blocked_again 和 probation。旧人工事件继续留原页，不妨碍能力独立恢复和新复发记账。15 分钟内两次经过新业务恢复后的 120 秒内真实 WAF 再封，paid 冷却 30 分钟；同一物理复发的 BODY/CRAWLER 报告不重复计为两次。免费/人工/fallback 不受该付费冷却阻断。
4. **可判读现场。** stdlib 有界 PNG 像素检查区分 READABLE/LOW_DETAIL/BLANK_SUSPECTED/FAILED，不识别验证码答案。不覆盖 first；真实 widget 状态变化可有界重采，保留 latest_actionable、after_action。latest_actionable 替代旧 confirmed 槽，新现场最多三张，旧 GUI confirmed 路径可返回现有最新槽。保留现有 14 天/256 MiB 清理。维护 UI 展示质量、业务恢复方式及分开的 solver 状态，有限管理员接口仍不输出秘密。
5. **分阶段诊断与出口证明。** Probe 保留错误类型、脱敏 reason_code 和既有 queue/control/site/parser/cleanup 时点；运行时/存储错误不被伪装为网站 Challenge。供应商代理验证区分绑定、陈旧遥测、本地代理不可达、IP 不同和 MATCH；成功缓存 60 秒，失败 15 秒。过期主链证明在 Profile writer 外最多每分钟补一次，正常半小时出口维护保留，不再每 5 秒请求 ipify。
6. **Reuters 首页/尾页。** QUERY 单元每轮只做首页；需要继续覆盖的第二页单独 TAIL/checkpoint、公平调度，保留全部 L1、60 秒目标、24 小时采集和去重。旧文章不再导致非时间排序的整页后续被误跳过。逐页及时交付，不重复交付前页消息。当前必需 QUERY 构成显式完整轮次，checkpoint 记录完成时点和耗时，不把各单词过去成功的最大值当成本轮完整成功。
7. **正常业务页和生命周期。** Reuters 每身份最多复用一个普通搜索业务页，每词仍正常导航，禁止冻结页面脚本；转交 Challenge 后不能进入复用池。External 冷 attach 不在同一预算内再重复一次完整冷启动；不为控制故障停止物理 Chrome。活动 Reuters 身份不因控制 Worker idle 而停 Chrome；备用空闲下限配置为 15 分钟，同时取消 Reuters facade 的 120/600 秒截断。仅生命周期字段修改不会触发 Supervisor 的物理浏览器配置变更。
8. **BODY/掉号/存储。** 排除搜索页作为 BODY canary，优先发现真实文章，缺样本不宣布 FULL。正常 BODY 明确 login wall 可建立独立 AUTH recovery，无需先有 CAPTCHA；沿用私有 credential_ref 和账号共享的 30 分钟限额，MFA/验证码/权益不足不填密码。缺凭据只延期 AUTH，不阻断公开采集。BODY 心跳对 SQLite BUSY/LOCKED 短退避，续租不确定就停止该 job 的提交、等待租约重派，兄弟 job 继续；损坏/不匹配不吞掉。

## 接线、迁移和边界

- `scripts/ops/configure_reuters_reliability.py` 在 Site Access 内默认 dry-run；`--apply` 先在线备份 Registry，再通过 Owner/CAS 修改仅 Reuters 的 auth/scope/lifecycle。身份/Profile/代理不换、Cookie 不清、不重建 Supervisor。
- 迁移前实际检查显示两成员的现有 credential_ref 均不可用，因此 **auto_login 不启用**；没有把聊天中的账号密码重新写入任何文件。补好私有引用后重跑同一命令即可启用既有受限能力。
- OR 搜索未做召回等价实验，继续 separate；不删词、不增常驻分片。22 个独立词 × 3 秒间隔至少 66 秒，还未计 BODY、导航/控制成本，不能保证全部词每分钟完成。完整查询周期与延期以实际记录为准。
- 本次不重写 Runtime，不增加打码供应商、OCR/视觉模型、新数据库、出口自动轮换或通用管理后台。没有通过制造 Challenge 来验收。
- 新持久字段在现有 JSON 中默认兼容旧记录；execute/resolve 旧严格协议和已有协商 header 保留。桌面改动仅既有英文字段/证据入口。

## 回归与验收

新增 `tests/test_reuters_reliability_repair.py` 覆盖实际本地 Chrome 渲染的隐藏/被覆盖 RETRY、未加载图片、原生精确按钮动作、免费准备中断、人工/晚恢复归因、原页保留期间分能力复发、paid 冷却、白屏、首页/尾页和心跳丢租隔离。所有本地网页测试使用 `set_content`，不请求 Reuters/provider。

发布前多批回归通过：核心 138 项；采集/正文/客户端/双 Runtime/调度 84 项（另 1 跳过）；新增与分页/调度复合 49 项；策略/客户端/生命周期/现场 61 项。它们有重叠，不将批次相加伪装成去重总数。最后的综合回归及生产结果补记于本节后续。

综合回归 172 项通过；补充浏览器恢复/Investing兼容/维护检查 58 项通过，BODY/新复合 34 项通过。另更新了 10-14 旧登录维护测试的过时 lease/mock 和 Viewer 丢失契约（当前契约保留 LOGIN 原页供恢复，不再因 Viewer 失败关闭页面），7 项通过；没有为旧 mock 修改生产所有权语义。新增 UNSOLVABLE 后人工恢复独立记账用例，不覆盖供应商失败结论。

原始脱敏证据在 `eval/reuters_repair_20261026/`：`audit.py` 查询完整指定窗口而非最近 N 条，分开物理 ACCESS_RESULT、CAPTURE_CONSUMED、各 QUERY/TAIL 成败延期、正式 BODY 结果、paid 状态和费用；`native_inventory.py` 只读本地 CDP 的精确 target/instance，不访问网站。SQLite 分表读取并非原子快照，poll 当前投影可能比窗口终点晚，不能倒填历史。

生产验收自实际发布收尾起观察一小时，约 10 分钟采样一次，使用自然生产访问；未出现当前可处理题面时，RETRY→paid→正式业务分支只能报告未覆盖。若仍存在内部错误继续窄修；若是低负荷真实再封，明确判业务稳定性未通过，不以一次成功交付。

### 外部协议依据

原生输入使用 [Chrome DevTools Input 协议](https://chromedevtools.github.io/devtools-protocol/tot/Input/)；供应商必须使用原浏览器 UA 与一致出口，且封禁状态不是可解题面，参见 [2Captcha DataDome 协议](https://2captcha.com/api-docs/datadome-slider-captcha)。不以协议接线通过代替 Reuters 正式采集和全文回执。

## 生产发布及初次验收

- `e56b8697` 已推 origin/main 并在 UTC 14:11:23 完成三个角色的标准发布；每角色 19 个改动源码 SHA-256 全匹配。Supervisor 容器 `d00bf01c2738...` 未重建，主 Chrome instance `1722ba0e74324f4b813505f691ee4e2f` / PID 712 未变。发布前无 LIVE owned Challenge 待办，旧已 RESOLVED 事件的历史 target 不伪称仍存在；未更换 Profile 或 Cookie。
- 发布前完整 Registry 在线备份保留在宿主机 root 私有 release/backups；Owner/CAS 配置更新也保留一份独立私有备份。Reuters site rev12、scope rev2、backup rev2/900秒；dry-run 重跑 changes=[]。现有凭据引用不可用，auto_login=false。桌面两个现有程序已窄更新，不重装 xRDP。
- 初始 [14:12:36,14:13:33Z) 全窗：17 次 CRAWLER ACCESS_RESULT SUCCESS、1 次 BODY SUCCESS、1 条正式 FULL；另 1 次 BODY BUDGET_DEFERRED/site_queue_timeout。没有新恢复事件或付费作业；不以此宣称持续稳定，也不把排队延期改叫 Challenge。
- 初始查看发现旧 unit 回执能混入迁移后的首次 query_cycle；已增加回归并改为从当前新回执初始化周期，而非继承最老历史时点。后续仅窄发布 Bus；首个跨迁移周期不计入三轮纯新版本覆盖。小时验收仍需分版本、全部查询、BODY 与延期分别统计。
- 两个历史实际白屏 PNG 均目视核对，且由新检查器判为 BLANK_SUSPECTED；未称能从白屏识别验证码。当前没有可解现场，真实 RETRY→slider→paid→正式恢复尚未覆盖。
- 统计修正 `f6ef4040` 已单独发布 Message Bus；Site Access/BODY 继续运行 `e56b8697`，三个角色逐文件核验均为各自预期源码，Chrome instance/PID 再次核对未变。补充调度/词表/迁移回归 36 项通过。最终一小时窗口为 **UTC 14:21:13–15:21:13 / 北京时间 22:21:13–23:21:13**，以 `observation-window.json` 为准；已安排本聊天每约 10 分钟一次验收，到期自动停止并汇报，发现真实内部故障按用户授权继续窄修。

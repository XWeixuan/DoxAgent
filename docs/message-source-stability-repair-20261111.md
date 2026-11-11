# 消息源提频后稳定性修复：实现与验收（2026-11-11）

依据 `dev_plan/workflow_v2/message_sources_post_cadence_stability_root_cause_repair_plan_20261111.md`。本轮不降低任何消息源/ticker/FEED的配置间隔，不调整硬并发上限，不重建Chrome/Profile，不修改Reuters专属代码、配置、出口或验证码现场。

## 本轮确认与实现

1. **公共准入活锁已经离线复现**：120个请求以相反顺序竞争同一组Semaphore，旧`joint_permits`在2秒截止前不能完成，取消后许可数仍归还；这是预约转交不前进，不能靠“没泄漏许可”排除。改为event-loop内按到达顺序选择资源齐备的请求，一次取得全部许可。阻塞host不占role容量，不阻塞其他可执行host。取消覆盖排队中、刚授予未恢复、执行中；保留source pacing与shrink debt。无新增进程/服务/表/锁文件。正常释放立即通知；兼容旧Semaphore使用者仅共用一个50ms内存唤醒timer，队列空时停止，不是网站/DB轮询。
2. **IR host生命周期恢复**：host/global联合准入；仅自有HTTPX client发生PoolTimeout时退休该host的generation，旧请求退出后关闭，下一正常业务周期创建新client。容量排队、普通连接超时、403/429不触发池重建；保留其他host与checkpoint；原urllib线程取消排空机制保留。
3. **NRC准确根因**：通过现有已配置传输取得200的真实报告。事件58409为合法Part 21，`Rep Org: ABB Inc`而Licensee为空且无Facility，旧程序使同页三条事件全失败。保留reporting_org独立语义，不能伪装Licensee或当反应堆查Operator；主体、日期、正文仍校验。单条异常隔离并保留最多8条事件号/错误原因；全页不可用仍失败。旧字段错误的retry仅在该NRC入口释放，未清去重/历史。回归fixture裁成报告正文，不保存整站资源。
4. **TechNews/Quantum首页与补充页解耦**：首页通过缓存新鲜度、传输和parser校验后先提交；分类archive/offset100独立完成，失败则保留`PARTIAL`和checkpoint内`supplement_pending/error`，下个既定轮询继续补充。保留原覆盖范围与全部原有缓存/失败校验，不删第二页。补充失败不再迫使重做已经验证的首页；不能把首页成功称为完整覆盖。Transport证明现在仅绑定本次capture的request_key，避免并行入口互相借用未解析的200。
5. **失败与可用回退分开**：保留主HTTP失败和原有失败状态，不把RSS回退称作爬虫恢复。只有RSS实际传输且delegate解析成功，才保留独立`rss_fallback`证明和`rss_fallback_available`；正常消息仍沿原Capture/去重/BODY路径交付。
6. **轻量等待定位及维护隔离**：复用POLL_TIMINGS/已有unit状态和周期记录，仅保存少量阶段/累计毫秒，失败周期带阶段，无新增采样写入或抓包。Collector定期归档独占一个串行executor，避免与正常解析/提交争用默认executor；维持现有维护频率、批量与保留策略，未增加清理量。

## 最小变更边界

主要修改既有`publisher_http`、`poll_trace`、`corporate_ir/http`、`public_listing`、`energy_sources`及Collector少量状态/maintenance代码。现有schema对外接口不扩张；新增信息为已有JSON中的可选字段。原有效的取消排空、坏curl句柄清理、真实发送证明、缓存新鲜度校验、去重与正文能力保留。

P1逐源判断以原排查表为索引：先在新版本自然流量中复核。如果共享层修复后仍失败，再按真实阶段窄修。Calcalist新鲜token403/SpaceNews或IR的429不会因内部修复自动变成成功；只对明确受限lane遵循服务端要求，不降整批频率。浏览器队列延期与BODY鉴权分列，不自动登录或换身份掩盖。

## 发布和验收记录

待发布及自然业务观察后追加精确SHA、容器、窗口与逐源结果。已有代码回归包含：相反锁序、跨host进度、取消后守恒、source pacing/缩容、IR坏池定点恢复、真实NRC Part 21/单事件隔离、首页先提交/第二页失败及次轮补齐、原有缓存与RSS失败状态契约。验收不能把重启后一次成功算长期稳定；至少30分钟自然业务窗口，之后仍需跨越原约18小时复发点的老化证据。

生产受控取证仅NRC：宿主机直连1次403，现有已配置传输1次200；没有打开Chrome、重建Profile或触发验证码测试。后续验收复用自然请求，以有限窗口聚合为主，不反复读取全历史。

本地组合回归：280 passed（247.75秒），仅Windows curl_cffi现有Selector兼容提示；无失败。另已验证反序竞争旧实现失败、新实现通过；代码compileall与git diff --check通过。

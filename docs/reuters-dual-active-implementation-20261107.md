# Reuters 双活采集：实施与验收

## 最小实现方案

只改变 Reuters Site Search 的 CRAWLER（QUERY/TAIL）身份分配，现有 BODY、人工维护和其他网站保持原行为。Scope Registry 的 verification_plan.crawler_routing=dual_active 启用，删除该配置即可退回主备。保留两个现有Chrome/Profile/出口，不重建Supervisor、不修改账号或验证码处理。

复用 JointBudget：在已有全站/身份并发与节奏预算之外增加内存中的任务分配预约；按实际在途负载、下一可用时刻、身份容量及恢复保护间隔选择健康成员，空闲同分时轮转。预约覆盖整个请求并在异常/取消时释放，不持久化队列，不创建额外调度进程。36个60秒采集单元的目标频率不修改。全站总预算不因双活翻倍，各身份原有预算不提高；单身份故障时只使用健康成员的合理容量，容量不足明确延期。

分配在现有同请求/共享抓取去重之后进行，同一任务不向两个Chrome并发发送。进入实际执行前再检查该身份的维护、恢复与认证状态，状态变化则交回原有候选流程。正常双活采集不切换active route指针，因此不破坏缓存generation、BODY路由或人工激活流程。

复用候选可用性及真实业务证明；缺少证明的成员通过既有有界canary重新验证。双活成员的warm canary不再把非active成员当冷备而停止Chrome，不因正常能力刷新切换健康route。Registry允许双活成员保留各自并发/间隔容量，运行方式和环境仍遵守既有约束。

## 验证

- 离线：并发任务均摊、保护期降权、单成员继续服务、全部不可用、取消释放、共享站点上限、去重、候选失效、无route抖动、非Reuters/BODY不变、warm canary不停止Chrome。
- 发布：main精确提交，仅重建Site Access；保留Supervisor/Chrome实例。通过现有Scope Owner CAS启用，不直接写SQLite。只留一份本轮发布前Registry回滚快照。
- 生产：分别统计两个身份的自然QUERY/TAIL成功与延期、按实际QUERY集合计算完整覆盖、正式BODY FULL/SHORT_FULL、控制故障/新Challenge与资源。使用当前acquisition.sqlite3采集Owner，不能读取已退役轮询投影冒充现状。低频观察，不制造验证码或购买测试任务。

## 执行记录

发布前只读：主身份已于北京时间04:10手工验证恢复，备用也已恢复；20:05 UTC起的旧单活窗口仍主要由备用承担。改动与实际验收结果待完成后补充。

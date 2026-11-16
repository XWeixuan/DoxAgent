# 全局消息源故障与端到端延迟逐源修复（2026-11-16）

## 范围与判断原则

按 Yahoo → SEC → 最严重延迟来源逐项处理。Reuters、Bloomberg 不在本轮修复范围。不修改交易时段、轮询周期、新稿准入、相关性阈值或 recheck 开关。每项保留自然运行回执，HTTP 成功、浏览器成功和正文成功分别验证。

初始页面同口径快照 `eval/global_sources_repair_20261116/catalog-initial.json`：异常全局源是 Yahoo、SEC、Bloomberg。Investing 端到端均值约691秒，Finnhub313秒、Google News288秒、Barron's279秒；这些数字暂不能直接解释为实时服务耗时。页面在24小时不足5篇时回看历史最近5篇，须进一步对齐原始消息、正文队列、分发及投影时间。

## 1. Yahoo（已确认根因与执行项）

历史 Nov10 的依赖停服、Nov11 的共享 HTTP 许可问题不是当前主因。当前浏览器补充列表持续成功，RSS HEAD 连续失败；新 HTTP Session 同样失败，排除单纯旧连接池失活。

部署迁移遗漏：服务器 Compose 把 Yahoo 专用 HTTP 路径留在旧 Bus 角色，实际采集已迁到 Collector。Collector 有效 `yahoo_http_routes=null`，回落至通用代理；该路径访问 Yahoo RSS 的 TLS 握手失败。对同一 MU RSS 进行受控对比：通用路径约5秒 SSLError(35)，直连200/18条、US路径200/18条；原先专用 DE/TR 两路径也均200/18条。浏览器身份没有坏，不能用清 Profile 或重启 Supervisor 修复此问题。

内部放大：Yahoo 的8秒网络预算还包含共享许可和节奏等待，坏路径占住唯一 RSS 网络槽后，其他 ticker 未发请求就超时。

执行：

1. 在 Collector 的正式 Compose 配置补回原有 DE/TR 专用路径；标准宿主机发布 manifest 同步写入该有效配置，防止仅源码正确、运行配置仍错误。
2. Yahoo 复用现有 `joint_permits/network_timeout/network_wait`，排队和节奏等待不消耗网络预算；调用方的总任务截止、限流熔断和许可数量均保留。不添加后台线程或新恢复状态机。
3. 网络与取消等待回归后只发布 Collector；核对有效配置，观察全部订阅 ticker 的自然 RSS HEAD 和 browser supplement，不以一次 canary 作为恢复结论。

## 后续逐源记录

SEC 在 Yahoo 收尾后开始；高延迟先拆 Investing 的真实逐篇时间线，再评估是否有跨源共因。不凭均值把历史等待删除或把周末耗时强制归零。

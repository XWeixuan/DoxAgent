# 非模型调用延时缺失修复

## 根因与生产样本

1. Serving 的 SourceOutbox.event 在两个分支无条件跳过 runtime_v2_effects。decision_metrics 已依赖其中的 decision_effective_at，因此追平 watermark 也不会补出该字段。
2. RuntimeRecallOpenRouterClient.complete 没调用已有 model_clock.start/stop，已有 round_receipt 只能记录空 model_interval。Haiku 回退成功也无法纳入完整区间计算。

只读抽查 BE `case_a6576b4005f54378a7b9574df45227a8`：原库生效时间为 UTC 2026-11-12T21:28:08.264148，Read 对应该 Case 的 effect 数量为0；三次成功 Haiku 调用区间为空，其他失败供应商请求有区间。原始证据见本次 eval 目录。

## 修复边界

- Outbox 在 Serving 模式只提取已 COMPLETED 的 route_to_w3 effect 所需的 effect_id/case_id/effect_type 和 payload.decision_effective_at。无此锚点的 effect 继续跳过。Native payload 若为文件引用，仅打开它的外层 JSON，不递归展开结果、输入和其他证据；非 Serving 读取保持原语义。
- OpenRouter 在调用 HTTP stream 前 start，在响应流结束/异常 finally 中 stop。请求构建、结构化解析不混入模型区间，失败和成功均沿用现有 round receipt 持久化，不新建写库链路。
- 非模型延时仍为 Case 进入 Runtime 到决策真正生效的跨度，减去相关模型调用区间的并集。排除 Operations，不改用模型产出时间，不排除真实长尾，不把缺失区间当0。
- 不回放 Runtime 历史、不重跑模型/交易、不扩表/建索引/加定时任务。过去已跳过的 effect 不会因修改代码自动回填；过去缺失模型时间的 Case 也不能准确复算。完整新 Case 从发布后进入统计。

## 验证与发布

- 50项测试通过，涵盖 OpenRouter 成功/失败请求计时、真实 outbox receipt 的生效时间保留及无关证据不展开、现有投影资源回归、模型并行区间取并集、长尾与重试回执。
- 功能提交 addfc7da36e35d6f0e9905a86e9c6b792de0cca9 先推送 origin/main，再仅定向发布 v2-scheduler、v2-projector；两者运行该版本，零重启。Scheduler 沿用现有20分钟 stop grace，不为此次修复缩短付费请求的退出期限。依赖层命中缓存，Web/API/Worker未重建。
- 生产自然流量的新 Haiku 调用已记录完整起止时间，详见 acceptance.json；未为验证触发额外模型请求或交易。
- 首个已逐条验证的有效新 Case：NBIS `case_668683b58fd543f9beef674242a6de2f`，Read 已保留决策生效时间 UTC 22:01:07.701338，非模型耗时 **23.464992秒**。页面 API 复用的 runtime_metrics_data 返回 non_model_mean_seconds.current.state=AVAILABLE、value=23.464992。该检查复用真实 Read 数据和页面计算函数，不冒充已登录生产浏览器端到端验收。
- 原始 metric_buckets_current 跨维度合计 samples=2，不等于两个不同 Case，验收不按该粗合计声称样本数量。当前明确核实1个有效 Case，其他Ticker会在新完整交易决策 Case 出现后形成样本；旧 Case 仍可保持 NOT_RECORDED。

机器汇总：[acceptance.json](../eval/non_model_clock_repair_20261113/acceptance.json)。

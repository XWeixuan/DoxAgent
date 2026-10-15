# W2 Policy 身份与新事实对齐修复

## 行为与边界

本次按 `persistent-runtime.w2.v2` 发布。R2 的 HIT 使用 `policy_ids` / `hit_condition_ids`，纯 RELATED 使用 `related_policy_ids`，两种身份在不同 Policy 上并存。每个 ID 必须属于本轮 R1 选出的 Candidate，HIT Condition 必须属于对应 Policy；不从召回结果推导 RELATED，不为 RELATED 编造 Condition。

R3 输入明确包含 W1 R2 reason 和 R2 最终输出，继续继承 R2 上下文。输出分为 `condition_results` 与 `related_policy_results`。两部分按本轮待判断集合分别完整覆盖，布尔值严格校验，未知 ID、漏项、空判断及相互冲突的重复判断均进入既有输出修复重试。

NEW + 仅 RELATED 只有完整、全 false 才 ARCHIVE + Delta；任一 true 或修复后仍缺少有效判断都进入 W3。后一种记录 `w2_alignment_status=UNAVAILABLE` 及错误原因，保留 W1 NEW。传输故障、取消、租约丢失仍沿用运行控制机制，不作为业务降级吞掉。NEW + 无 HIT/RELATED、HIT、OLD、LOW 与 WATCH 入口按原有规则处理。标签不是路由开关，本次不增加交易授权或历史 Case 重跑。

## 冻结与兼容

启动发布时将用户已完成的 W2 R2/R3 提示词与新 Schema、Schema hash、契约版本冻结到同一执行 Bundle。新 Case 固定使用其 Bundle。旧 Bundle 没有新标记，继续使用旧模型、旧回执与旧路由；不得静默升级历史 Case。原 `matched_condition_ids` 只在旧结构读取中保留。

W3 队列、历史关联、WATCH 关联与详情接口保留 Policy 层 RELATED 身份。接口及前端展示 `related_policy_results` 和 UNAVAILABLE，不将不可用显示为 OLD。前后端封闭 wire Schema 同批生成发布。

## 验证与上线

回归覆盖 Policy RELATED 无 Condition、混合 HIT/RELATED、完整 true/false、空列表、非法布尔、漏项、未知 ID、冲突重复、修复成功及耗尽、传输/取消/租约错误、SQLite Case/W3 队列回读、旧回执和 Bundle、正式 API 详情。前端执行 Schema 生成、TypeScript 检查及生产构建。

部署使用 origin/main 已提交源码和标准 Dockerfile，更新 scheduler、API、web；其他容器保持运行。上线验收另存部署回执，检查镜像版本、健康状态、执行 Bundle 和自然流入的新 Case。离线回归不等同于真实模型语义质量验收。

扩大回归为 296 通过、3 失败。3 项失败分别位于 gateway-status 的 mock 缺少 batch_get、消息来源测试没有 raw 记录、reference-delta 测试读取已变更的 changes 字段；使用修改前 HEAD 的独立源码快照均复现相同失败，属于既有测试问题，本次不扩大修复范围。定向新增与关联回归、TypeScript 检查及 Vite 生产构建通过。

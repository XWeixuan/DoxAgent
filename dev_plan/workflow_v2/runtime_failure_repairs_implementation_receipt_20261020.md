# Runtime 失败修复实施记录

依据：`runtime_failure_root_cause_and_repair_plan_20261020.md`。明确排除旧 W3 Fork 的超时预算、启动队列和生命周期恢复改造；`thread_lifecycle.py`、`capsules.py`、`w3_recovery.py` 未修改。

## 实现

- 停机先停止认领和 Effect pump，排空已有任务后才关闭服务/客户端；未启动任务交回原租约，不消耗失败次数。按确切 service closed / SQLite 锁证据对账 task FAILED、原 Case RUNNING 的状态脱节，保留原 Case、冻结输入与成功轮次。
- journal 大内容与 Case/Effect/model turn 的编码前移；热写使用短连接等待、完整事务有界退避。WAL 设置移至初始化，保留 FULL 持久性和租约 fencing。临时 SQLite 争用不当作模型失败；heartbeat 不因一次临时争用永久停止。
- 新 READY API 返回结构化错误。仅对没有业务 job、没有已接受 thread_id 的空准备恢复原 slot/原 operation；最多一次额外恢复，原生命周期负责先 reconcile 原 cwd/receipt。保留原错误，不更换 Case/线程身份。永久配置错误对同 profile 暂停补池 60 秒。
- Codex usage-limit 按账户暂缓派发，保留失败 receipt 和 Case，不反复消耗普通重试。15 分钟后仅一个原 Case 探测恢复，成功后解除暂缓；不自动切账户/模型。
- operations 明确允许引用的 E# 范围；只有一个带准确校验错误的输出纠错机会。请求 generation、修复请求、旧回执和拒绝标记原子落盘；不会重新提交交易。
- 新 bundle 的 Case 引用范围同时包含原 pinned reference、同 Case 原冻结 W1 Event Details 和可见 provisional，且两个阶段实际提供同一份证据。旧 bundle 不静默扩大引用范围；URL、PIN、F# 仍不能作为 Event 引用。
- 按本次指定方案，W2R3 删除多余 reason 输出要求，保留原 Schema、严格布尔和 Router 兜底。W1R2/W2R2 增加最小一致性示例，不改研判业务规则。
- 仅完整 JSON 代码围栏可被剥离，其后仍严格验证；百炼 400 保留脱敏诊断；OpenRouter 429 尊重秒数 Retry-After 并有界退避。不改模型、推理强度或节点超时。
- 现有页面和导出已分别展示交易与后置操作状态，本轮复用，不重复开发前端。

## 验证

- 17 个相关测试文件，最终整组 **265 passed**。
- 将 WAL 设置前移后补跑 8 项；最后的抖动退避调整后补跑 19 项，均通过。
- 修改 Python 文件的 Ruff F 类检查、编译与 diff whitespace 检查通过。代码库其他文件已有的 lint 问题不在本轮扩修。
- 测试原始记录：`eval/runtime_failure_implementation_20261020/final-tests.xml`、`final-tests.log`。

## 生产验收边界

部署只更新 scheduler / Codex Worker，保留其他容器的镜像、运行配置和数据。构建使用已提交代码；生产发布回执保存镜像、源码哈希、前后容器身份和健康状态，不回显私有环境变量。

不批量重放终态失败消息，不自动重判历史非法输出，也不追发过期交易。启动对账只恢复有明确基础设施证据的状态脱节记录。业务日过期的交易继续受现有输出控制约束。

百炼 400 的具体上游拒绝原因仍须依据新增诊断辨认，不能宣称已定位全部历史 400；W2R2 长尾的换模型/推理强度测试按原方案另行评估。正常运行中的模型仍可能产生非法输出，不能以“未来失败数归零”作为本轮承诺。

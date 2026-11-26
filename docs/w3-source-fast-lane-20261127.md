# W3按消息源快速研判通道

配置DOXAGENT_W3_FAST_SOURCE_IDS为逗号分隔的稳定source_id，默认和本次生产显式配置均为sec_edgar,corporate_ir_press_releases（SEC EDGAR、企业IR/Press Releases）。空字符串关闭，增加ID扩充；不匹配标题、域名或泛称Press Release。使用已冻结SourceMessageEnvelope.source_id，多成员消息遵循现有latest来源身份，不用其他成员推断。

进入W3时一次性冻结模型路由：上述来源Trade使用gpt-6.2-sol/high/fast；其他来源保持gpt-6-luna/max/fast；Operations统一gpt-6-luna/max/default。双分支共享Case冻结配置，失败重试/重启不重新选择；旧Case已有execution profile不改写。W1/W2路由、交易规则、schema、Prompt和Skill不改。

资源准备与推理配置解耦：source_fast Case的Template兼容身份、READY configure/claim均用原Luna Max。SDK v6驻留匹配对Luna/Sol采用同一物理配置，模型、effort、Fast分别由turn调用传入；Provider、MCP、工作区、安全边界仍参与匹配。因此来源模型不会拆出Sol库存，也不触发新的Fork/Resume。库存耗尽和故障仍沿用现有恢复，不承诺任何情况下零冷启动；Case逻辑隔离、Trade→Operations同线程不变，不新增资源池。

开发从最新origin/main的隔离工作树进行。生产4文件含已部署但未合入main的market_enabled保护，逐文件提取实际镜像并保留其现有行为，避免发布模型路由时回退行情设置。其余并行修改和提示词不混入。

验证：核心48项通过；增加来源Case真实Runner阶段请求测试和SDK effort断言后11项通过（重叠）；覆盖精确来源/未知来源、关闭/扩充、冻结历史、模板Luna/TradeSol/OperationsLuna、相同resident切换无thread_resume、fast/default以及READY现有回归。未以修改延时统计或扩大库存达标。

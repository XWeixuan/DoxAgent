# Qwen3.7-Flash 30 篇真实测试：兼容性结论

## 结论

本轮不构成有效的质量 A/B，也不建议将 `qwen3.7-flash` 以当前配置替换
`deepseek-v4-flash`。M2、M3、M4 的低成本探针均成功，但固定 30 篇全流程在
单文档/Atomic 阶段出现超长非法 JSON 输出并挂起；人为停止前仅完成 29/30 个
文档，尚未进入可用的 Cross-document/Package 产物阶段。

本结论严格保持“只换模型”口径：百炼 provider、既有 key、并发、JSON Object、
strict=false、thinking=false、prompt、payload、repair/fail-open 边界均未修改；
Package V3 的独立 `deepseek-v4-flash-0731` 配置也未修改。

## 实测配置与探针

- M2/M3/M4：`qwen3.7-flash`；provider=`dashscope`。
- M2 使用既有 Chat JSON Object 路径；M3/M4 使用既有 Responses JSON Object 路径。
- strict 继续关闭；thinking 继续关闭。
- `cdecr models probe --tiers M2,M3,M4`：3/3 成功，输出均为合法探针 JSON。

这说明 provider/model 名称、基本 Chat/Responses 连通性和凭据均正常；不能证明其能
稳定遵守本工作流的大 DTO JSON Object 合同。

## 全流程运行状态

输入固定为上一轮的 30 篇 corpus 与 manifest；输出 Registry：
`.tmp/cdecr/mu30_qwen37flash_20260818_r1.sqlite3`。

| 项目 | Qwen3.7-Flash 本轮（中止时） | DeepSeek-V4-Flash 上轮完成基线 |
| --- | ---: | ---: |
| 文档运行 | 29 SUCCEEDED / 1 RUNNING / 1 FAILED | 30/30 SUCCEEDED |
| 已落库 model calls | 524 | 556 |
| 输入 Token | 987,512 | 1,325,375 |
| 输出 Token | 644,827 | 393,471 |
| 累计 provider latency | 4,057,727 ms | 4,147,314 ms |
| invalid_json | 4 | 0 |
| 其他失败 | provider_arrearage 3；invalid parameter 1 | 1 Package description empty response |
| 可用于质量评估的最终 Package | 无 | 28 Package |

上表的 Qwen Token/latency 是**不完整运行**的累计值，不能当作成本或速度胜出。它在未
完成全流程前已经消耗了上轮 163.9% 的输出 Token 与 97.8% 的累计模型时延。

## 直接根因证据

1. `atomic_coreference` 发生 3 次 `invalid_json`，`atomic_coreference_escalation`
   发生 1 次。最大一条非法响应记录为约 1,338,746 字符；解析错误为
   `Expecting ',' delimiter` 或 `Extra data`，不是 Pydantic 业务字段校验失败。
2. 其中一个 Atomic repair 继承了约 2,679,675-byte 请求体，provider 返回
   `provider_invalid_parameter_error`。这使局部 repair 不再是低成本收敛，且导致
   后续单文档运行无法自然完成。
3. 运行库最后一次模型调用落盘为 2026-08-17T20:20:14Z；之后原 CLI 进程连续约
   18 分钟没有任何新落盘或输出，故中止该挂起进程。未启动第二个全流程，也未覆盖或
   删除该 Registry。
4. 另有 3 个 `provider_arrearage`，来自 provider key/fallback；它们是次级运行环境
   信号，不足以解释 Qwen 的超长非法 JSON，因为后者发生在已有成功响应的 M3 JSON
   Object 路径。

## 与 DeepSeek 基线的比较边界

质量指标（Mention、Field、Atomic、Package 准召和碎片化）**没有比较结果**：Qwen
本轮未形成最终 Atomic/Package 分区，任何从部分单文档结果计算的准召都不代表同一
工作流输出。

本轮可作出的模型选择结论仅为：在当前“JSON Object、thinking=false、完整既有 payload”
合  同下，`qwen3.7-flash` 未通过 30 篇稳定性/成本前置验收，不能替代
`deepseek-v4-flash`。

测试结束后，M2/M3/M4 已恢复为此前通过验收的 `deepseek-v4-flash`；provider、key、
strict、thinking 和并发设置没有变化。

## 后续建议（不在本轮自动执行）

若仍要评估 Qwen，需要单独成为一个新的、非“其他参数完全不变”的实验：先为
Atomic/Judge 等大 DTO 增加 response-output 上限与单请求 payload 上限，并在 5 篇
shakedown 中验证大 JSON 合法率、repair 体积和长尾超时。否则完整 30 篇会把模型自身
的非结构化长输出误当成业务质量差异。

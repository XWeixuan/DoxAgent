# CDECR M1 qwen3.7-text-embedding 切换与真实验收重试

## 结论

2026-07-23 已将独立 CDECR 模块的 M1 默认模型从
`text-embedding-v4` 切换为 `qwen3.7-text-embedding`，保持 1024 维和每批最多
10 条不变。真实 M1 probe 成功返回 1024 维向量，30 篇冻结语料重试中 M1
累计成功 248 次，没有 M1 失败。

完整 N5.5→N6 验收仍未完成。两次可恢复运行均被 M2
`deepseek-v4-flash` 的 HTTP 400 `Arrearage` 中断；按异常熔断约束，第二次
同类错误后停止，不再重试，也没有把 M2 擅自替换为其他模型。因此本次只确认
M1 切换有效，不能宣称 Field Resolution 质量门槛或 N6 验收通过。

## 代码变更

- `CDECRSettings`、Embedding Client、SingleDocumentEngine、
  CrossDocumentEngine、FieldCoreferenceResolver 与 `doctor` 的生产默认值统一为
  `qwen3.7-text-embedding`。
- `.env.example` 同步新默认值；现有 `CDECR_EMBEDDING_DIMENSIONS=1024`
  契约和 SQLite Embedding 的 model/dimension/input-hash 隔离保持不变。
- 历史交付文档中的旧模型实测记录保留为历史证据，不回写或伪改。

## 真实运行证据

- M1 最小 probe：成功，1024 维，21 input tokens，约 956 ms。
- 冻结语料：30 篇 Source Message、227 个不可变 Grounder Event Mention。
- 中断时部分 Registry：398 个 Canonical Field Registry Entry、795 个当前
  Field Link、138 个持久化 Embedding、1552 条 Decision Audit。
- 模型调用：514 次成功，其中 M1 `qwen3.7-text-embedding` 248 次、M2
  `deepseek-v4-flash` 266 次；3 次失败均为 M2，包括 2 次
  `provider_arrearage` 和 1 次可修复的 `invalid_structured_output`。
- 旧 M1 失败现场已备份到
  `.tmp/cdecr/field_resolution_30_20260723/runtime_v2_failed_text_embedding_v4.sqlite3`。
- 当前部分状态保存在
  `.tmp/cdecr/field_resolution_30_20260723/runtime_v2.sqlite3`；最终结果 JSON
  未生成，避免把部分运行误报为完成验收。

## 后续恢复条件

只有在 `deepseek-v4-flash` 对连续真实请求稳定可用，或明确批准变更 M2
模型契约后，才能从当前 Registry 使用 `--resume` 继续。完成后仍需检查收敛轮
零增量、逐字段质量门槛、顺序扰动稳定性和 N6 Identity 完整率，方可放行 N7/N8。

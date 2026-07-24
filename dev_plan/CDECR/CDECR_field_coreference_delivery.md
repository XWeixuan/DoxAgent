# CDECR Field Coreference 交付说明

## 交付边界

本模块作为 Entity/KB Linking 的补充能力运行：已有可信外部 ID 时复用外部身份；开放对象或 KB 未命中对象进入同 namespace 的字段共指。它不修改不可变 `EventMention`，也不建立实体画像、关系图、聚类摘要或独立 merge history。

统一结果严格为：

```json
{
  "canonical_id": "field:...",
  "resolution_method": "INTERNAL_COREFERENCE",
  "external_id": null
}
```

模型输入严格只包含 `namespace`、`raw_value`、`local_context` 和可选 `hints`；`mention_id + field_path` 仅作为持久化调用边界，不注入 LLM 输入契约。

## 已实现能力

- 14 个封闭 typed namespace；Participant 使用 role/既有 KB 类型路由，open attribute 使用 key 映射，location、metric 和 package anchor 独立隔离。
- namespace 内确定性文本标准化、canonical/alias 召回、简单字符串相似度和 Embedding Top-5；候选去重后最多 8 个。
- Registry Embedding 复用通用 `embeddings` 表，固定 `owner_kind=FIELD_REGISTRY`；alias 输入稳定、去重、限量，并通过 input hash 控制重算。
- 严格 `LINK/NEW/UNRESOLVED` JSON 契约，`LINK` 只能选择候选 ID，结构失败只允许一次同模型修复，不做宽松解析。
- 空 Registry 的名称型 `NEW`、泛称/代词 `UNRESOLVED` gate；泛称不会被加入 alias。
- KB 成功路径、KB miss fallback、provisional 原位补充 `external_id`、同 namespace 外部 ID 唯一 root、redirect 跟随和最大深度/自环/循环保护。
- SQLite `user_version=6` 新增最小 `canonical_field_registry`、当前 `canonical_field_links`、`atomic_event_recall_fields` 与 package-anchor 召回索引；v1-v5 可重入迁移保留原数据。
- CrossDocumentEngine 在 Atomic 处理前逐字段执行解析；facility/project/product/asset 的相同 resolved ID 形成 `FIELD_ID` Atomic 召回，package anchor 只进入 Package 召回。
- 不同 provisional ID 保持中性；只有现有规则适用且两侧均为不同 trusted external ID 时才保留/增加 Hard Cannot-Link。
- 模型调用与脱敏输入、候选、结果写入现有追加式审计；当前 Field Link 可修订，历史 Mention payload 不重写。

## 验证结果

- Field Coreference focused tests：13 passed。
- CDECR 完整非真实测试集：152 passed，3 deselected。
- `ruff check src/cdecr`：通过。
- `mypy src/cdecr`：通过。
- CLI smoke：`registry init` 成功，`user_version=6`、foreign keys/WAL 正常。
- Wheel build：通过，并确认包含 Field Coreference 实现、契约及 Prompt 文件。

真实百炼请求不属于本次模块契约验收；模块沿用现有 JSON Mode、`effort=none` 的独立 DashScope adapter。

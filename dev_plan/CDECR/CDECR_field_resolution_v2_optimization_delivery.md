# CDECR Field Resolution v2 优化交付说明

## 交付结论

本轮已完成代码、目录和离线测试侧的 Field Resolution 正式优化，但 30 篇真实
验收被百炼 M1 `text-embedding-v4` 的 `Arrearage` 中断，因此不能宣称达到生产
门槛，也没有放行 N7/N8。

## 已完成

- 固化原文 Base System Prompt v2，并为 `FieldNamespace` 的 28 个值逐一建立
  namespace policy；普通 typed candidate 只暴露 `canonical_id`、非空
  `aliases` 和 `hard_dimensions`，仅 `participant.unknown` candidate 暴露真实
  `namespace`。
- Field decision 使用条件式 JSON Schema：普通 namespace 不包含
  `target_namespace`；`participant.unknown` 仅在 `NEW` 时必填，`LINK` 与
  `UNRESOLVED` 禁止出现。审计记录 base/policy/catalog/retriever/input hashes、
  model、token、latency 和 status。
- Metric、Predicate、Fiscal Period 继续使用 exact/alias/string recall，不使用
  Embedding。目录补齐核心 Metric、Predicate snake_case/action aliases、财期五类
  标准表面，并为 MU 等有模板的 issuer 覆盖未来两个财年。
- Fiscal 解析按 `issuer_id + published_date + fiscal calendar + raw surface` 工作；
  KB 命中直接 external link，明确未覆盖期间可进入 issuer-scoped provisional，
  缺 issuer/不可识别表达保持 unresolved，跨 issuer candidate 为 0。
- Participant router 实现强规则、多目录 exact/alias、typed string recall 与
  `participant.unknown` 多 namespace LLM fallback；最终 entry 必须落在真实 typed
  namespace，不会创建 `participant.unknown` cluster。
- 30 篇批处理把 Participant 大目录查询改为一次批量预热：基准从首篇路由
  41.7 秒降为 30 篇预热 8.9 秒，预热后单篇约 1 毫秒。缓存是有界的，不新增
  Registry 表。
- 提供一次性 clean Registry 工具
  `scripts/cdecr_reset_field_registry.py`。正式 30 篇 Registry 已备份旧状态并只
  迁移 30 个 SourceMessage 与 227 个不可变 EventMention；旧 field entries、
  links、redirects、embeddings 与污染 aliases 均未迁移。

## 验证证据

- v2 catalog validator：`valid=true`，0 error，catalog hash
  `82470113a3e7f4c49e1e7d31ff41673f122a94e4843a80aa46f9988b2bdb3c78`。
- Focused tests：`28 passed`，覆盖 Field Coreference、N5.5/N6 与
  cross-document，
  28-policy 穷尽、候选视图、条件输出、Fiscal issuer scope、Participant
  multi-type、顺序稳定和幂等复跑。
- 完整 CDECR：`165 passed, 3 skipped`。
- Scoped ruff、mypy、catalog validator、`git diff --check` 与 wheel build 通过。

## 真实验收状态

真实 30 篇重算使用当前主 DashScope key 和两个 fallback、JSON Mode、关闭
thinking。失败前已持久化 209 个 Registry entry、312 个 field link，并记录
168 次成功模型调用；随后 M1 Embedding 在 key 轮换后返回 HTTP 400
`Arrearage`，另有 1 次失败审计。

这次结果是部分运行，不能计算或宣称附件中的 precision/recall、candidate recall、
顺序扰动和 held-out 门槛。恢复供应商余额后，应从 clean Registry 再运行：

```powershell
python scripts/cdecr_evaluate_field_resolution_30.py `
  --registry .tmp/cdecr/field_resolution_30_20260723/runtime_v2.sqlite3 `
  --output .tmp/cdecr/field_resolution_30_20260723/runtime_result_field_resolution_v2.json `
  --max-passes 3 --resume
```

完成后再执行原序、逆序和三个固定随机序的独立 clean Registry 运行；只有全部
门槛通过，才允许结果进入 N7/N8。

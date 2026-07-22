# CDECR 步骤二交付说明

## 交付状态

截至 2026-07-16，步骤二的代码、离线测试、SQLite v2 迁移、真实 DoxAtlas 只读
基线复核和构建验收已经完成，M1–M4 真实探针也已恢复并通过。步骤二**尚未标记完成**：
优化后的 24 篇真实质量批次、M4 辅助 Gold 和独立人工复核仍未全部关闭。

## 实现范围

- 独立单文档链路：offset-safe 预处理、Dreamer、始终 M3 Grounder、条件式批量
  M2 Judge、字段归一、Event Mention 持久化；没有导入 `doxagent.*`，也没有创建
  Atomic Event 或全局 Event Package。
- 证据：模型只能返回 `segment_id + segment-local [start, end)`；运行时映射成
  `EvidenceSpan` 后再次验证原文切片。标题、导语、连续段落、模板/脚本剔除、长文
  分块和超过 250,000 字符的 Grounder/Judge 有界上下文均有确定性实现。
- 重复：精确指纹、规范化指纹、URL 归一、64-permutation MinHash 和标题 M1
  Embedding；只有标题和正文原始字符完全一致才复用结果，其余关系只追加审计。
- 严格中间契约：Dream Candidate ID、Grounder Draft ID、Judge Decision ID 和最终
  Mention ID 均由运行时确定性生成。全部 Pydantic 模型 `extra=forbid`；结构化输出
  失败仅允许一次同模型修复，第二次失败按文档隔离并审计。
- Judge：固定实现数量、置信度、Grounder 争议、长文、高价值来源/事件及 M0
  重复/数值/状态/Projection 冲突路由；支持接受/修订、拒绝、拆分、去重和属性合并。
- 归一：版本化 `catalog-v1` 覆盖 MU 主体/机构别名、FY2026 财期、收入、GAAP /
  Non-GAAP 净利润和 EPS、资本开支、利润率、目标价以及主要币种、百分比、基点、
  K/M/B。顺序为实体 → 时间/财期 → 指标 → 数值 → Projection 一致性；M1 接受阈值
  为 cosine `>= 0.88` 且领先第二候选 `>= 0.05`，M2 只能选择给定候选或返回未解析。
- SQLite：显式可重入迁移到 `user_version=2`，保留 v1 Source/Mention/审计数据；新增
  document run/idempotency、preprocessing/duplicate、Dreamer、Judge、Normalization、
  run-to-mention 表，并给 model call 增加 stage、prompt/schema/input hash。
- CLI：新增 `documents process`、`documents batch`；batch 本地 Registry 优先，缺少
  本地数据时才执行 market+ticker+UTC 时间窗+limit 的 Supabase 只读 GET。`doctor`
  增加 v2、Pipeline/Prompt/Catalog 版本与模型路由检查。

## 24 篇 MU 评估基线

- 选择规则已固化为：全部 4 篇长/超长、10 篇中等、10 篇短文，按来源轮转；实际
  覆盖 16 个 source label。
- 仓库清单只含 row ID、指纹、长度分层、来源、待复核期望类别和汇总指标占位，未
  提交新闻全文。全文仍只在 `.tmp/cdecr/baselines/`。
- 清单明确标记 `PENDING_M4_AND_HUMAN_REVIEW_ARREARAGE`；没有把标题启发式类别冒充
  M4+人工 Gold。真实门槛测试要求 `.tmp/cdecr/evaluation/` 中存在复核后的 Gold，
  并在处理前先探测 M1–M4，避免欠费时发起 24 篇昂贵调用。

## 验证结果

- CDECR focused：`128 passed, 3 deselected`。
- scoped ruff：通过；scoped strict mypy：通过。
- 真实 DoxAtlas 只读测试：`1 passed`；153 条 MU 基线与步骤一清单一致。
- `doctor`：包边界、SQLite v2、匿名/发布密钥只读 GET、模型配置和单文档路由全部通过。
- wheel：`doxagent-0.1.0-py3-none-any.whl` 构建成功，CDECR Python 包、Catalog JSON
  和 Prompt Markdown 均已包含。
- CLI smoke：registry init、doctor、documents process/batch parser 通过。
- 全仓非真实测试：`467 passed, 21 skipped, 26 deselected, 2 failed`。两项失败均为
  本轮前已有且不在 CDECR 范围：缺少 `dev_plan/PHASE0_BASELINE.md`；Dashboard 测试
  期望成本为 `None`，实际运行时返回已记录成本。
- 真实模型：M1 `text-embedding-v4` 返回 1024 维，M2 `deepseek-v4-flash`、M3
  `qwen3.7-plus`、M4 `qwen3.7-max` 均返回符合探针 Schema 的 JSON。

## 未完成项与恢复条件

1. 完成优化后的 24 篇真实单文档批次，并对所有失败保留可追溯审计或修复后重跑。
2. 用 M4 生成 24 篇标注草稿并完成人工复核，保存到 Git 忽略的 Gold 路径。
3. 显式设置 `CDECR_RUN_REAL_STEP2_EVALS=1`，通过 24 篇 Schema/Evidence 100%、
   recall/precision 均不低于 90%，以及幂等重跑零新增调用/零新增 Mention 的硬门槛。
   按 2026-07-16 用户指示，Projection 暂停且不注入模型输出契约，相关准确率本轮标记为
   `DEFERRED_BY_USER`，不得伪报为 100%。

## 外部接口依据

- [阿里云 DeepSeek API](https://help.aliyun.com/en/model-studio/deepseek-api)
- [阿里云 Responses API](https://help.aliyun.com/en/model-studio/compatibility-with-openai-responses-api)
- [阿里云文本生成模型说明](https://help.aliyun.com/en/model-studio/text-generation-model/)
- [Supabase 2026 Data API 暴露变更](https://supabase.com/changelog/45329-breaking-change-tables-not-exposed-to-data-and-graphql-api-automatically)

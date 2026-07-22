# CDECR 步骤三交付说明

日期：2026-07-16

## 结论

步骤三的代码、离线编排测试、SQLite v3 迁移、wheel 和 CLI 已完成；真实 DoxAtlas
只读测试及 M1–M4 最小探针已通过。真实新闻冷启动已取得成功 Atomic Event 与 Package 样本，
但完整 24 篇增量/边界报告仍在执行，因此步骤三暂不标记为完全完成。

## 已实现范围

- 新增独立 `CrossDocumentEngine`，冷启动和持续增量共用同一条处理逻辑；模块继续禁止导入
  `doxagent.*`，也不注册 DoxAgent 工作流。
- Atomic Event 实现实体、时间、事件族、Schema Identity、Canonical Proposition Embedding
  和来源指纹的有界候选并集；召回只使用 M0/M1。
- Hard Cannot-Link 覆盖主体、对手方、谓词、时间/财期、断言、阶段、地点/资产和三类金融
  Projection 身份字段。目标价数值只作为 Claim，不作为分析师事件的硬身份字段。
- 每篇文章的全部 Mention 与各自 Top 5 候选组成一次批量精排；默认 M2，高影响、低置信度
  或高歧义请求升级 M3。结构化输出失败只允许一次同模型修复。
- 同批 Mention 使用运行期临时 Atomic 候选参与同一次批量判断；模型结果由编排层执行
  `MERGE`、`CREATE_NEW`、`CREATE_AND_LINK` 或 `HOLD`。
- Atomic Profile 采用确定性、逐版本更新：保留全部 Mention、来源 Claim、代表 Mention、时间范围、
  Claim 冲突和身份冲突；明显重复簇只做局部 M0 合并并保存重定向。
- Package 严格在 Atomic 之后执行。BOUNDED Package 在锚点充分时走 M0，否则走 M2；
  EPISODE Package 默认 M2，歧义高时升级 M3。
- 实现 `MEMBER`、`EXTERNAL_RELATED`、`NOT_RELATED`、`UNCERTAIN` 语义及对应编排动作；
  市场反应和分析师反应以 Event → Package 外部边保存，不加入 Package 成员。
- 实现明显重复 Package 的 M0 合并、EPISODE 疑难 Package 的 M3 批量复核、重定向和
  过度扩张 HOLD 标记；未实现全局图聚类、自动复杂拆簇或 Package 嵌套。
- 新增 `PackageExternalRelation` 严格契约，用于设计文档规定的 Event → Package 边；原有
  `ExternalEventRelation` 继续只表示 Atomic Event 之间的关系。

## Registry v3

SQLite `PRAGMA user_version` 升级为 3；迁移可从 v1/v2 重入执行，并回填已有 Atomic/Package
的召回索引。新增：

- `cross_document_runs`
- `atomic_assignment_decisions`
- `package_assignment_decisions`
- `package_merge_decisions`
- `hold_queue`
- `package_external_relations`
- `atomic_event_redirects` / `package_redirects`
- `atomic_event_recall`、实体/来源倒排表
- `package_recall`、实体倒排表

领域 payload、模型调用和决策均追加保存；Atomic Event 与 Event Package 只允许连续版本更新。
处理键包含 Message、严格 Mention payload、引擎/Prompt 版本、模型与阈值配置；成功键重跑直接
返回已有结果，不增加模型调用、Atomic Event 或 Package。

## 模型路由与阈值

- M1：`text-embedding-v4`，每批最多 10 条；Embedding 召回阈值 `0.82`。
- Atomic：默认 M2 `deepseek-v4-flash`；高影响、低置信度或至少 4 个候选时升级 M3
  `qwen3.7-plus`。`SAME_EVENT` 合并阈值 `0.85`，`RELATED_NOT_SAME` 阈值 `0.75`。
- Package：BOUNDED 优先 M0；其余默认 M2，复杂请求升级 M3。成员阈值 `0.80`，外部关系
  阈值 `0.75`。
- Package-to-Package：明显重复走 M0；疑难 EPISODE 批量走 M3，合并阈值 `0.85`。
- M4 不进入在线步骤三主链路。

## CLI

- `python -m cdecr events process --message-id ...`
- `python -m cdecr events batch --market US --ticker MU --start ... --end ... --limit ...`

两个入口默认先幂等完成单文档发现，再进入跨文档主链路；批处理按文档隔离失败。`doctor`
已增加 SQLite v3、步骤三 Prompt/版本和模型路由检查。Supabase 继续只通过受限 REST GET
读取 `raw_media`，本步骤没有远端写入或 Schema 变更。

## 验证结果

- CDECR focused suite：`128 passed, 3 deselected`。
- 新增关键用例：同来源批内临时簇、跨来源同事件合并、不同指标/财期硬拆分、同财报不同
  Atomic 进入同一 Package、不同财期 Package 分离、市场反应仅建外部边、一次修复、持续非法
  输出单文档失败、Registry 重启与幂等。
- 真实 DoxAtlas 只读基线：`1 passed, 1 deselected`；未写 Supabase。
- scoped Ruff：通过。
- scoped mypy：通过。
- wheel：构建通过，步骤三 Python 模块和三个 Prompt 均存在于 wheel。
- CLI smoke：Registry 初始化为 v3；`events` 子命令可用。
- `doctor`：包边界、SQLite、Supabase 只读、模型配置、步骤二/三版本与路由全部通过。
- 全仓非真实 pytest：`467 passed, 21 skipped, 26 deselected, 2 failed`；两个失败均为既有
  非 CDECR 基线问题。

## 未通过闸门与风险

- 真实 M1–M4 最小探针已经通过；当前未关闭项是完整 24 篇真实链路与人工/M4 审查，不再是
  供应商欠费状态。
- 已有真实长文成功生成 7 个 Mention、7 个 Atomic Event 和 7 个 Package，幂等重跑新增为 0；
  但单个成功样本不能替代跨来源 False Merge 与 Package 边界的整批报告。
- SQLite 不引入向量扩展；Embedding 候选在本地读取 float32 向量计算余弦。普通字段召回已有
  索引和 Top-K 限制，但大规模数据下仍需在后续评估 Python 向量扫描成本。
- 完整全仓回归已结束；CDECR 独立测试无失败，两个全仓失败均不涉及 `src/cdecr`。

完成正在执行的 24 篇验收、M4/人工 Gold 审查及高风险边界报告后，才可把步骤三状态改为完成。

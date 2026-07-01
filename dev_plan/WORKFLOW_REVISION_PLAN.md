你现在的任务不是继续修 Document2 smoke blocker，而是对 Document1 + Document2 初始化 workflow 做结构性重构。此前 29 轮 eval/debug/fix 已经证明，继续局部修 blocker 会把复杂度继续堆进 `initialization.py`、normalizer、resolver、sanitizer 和 promotion。现在必须停止这种修法。

本轮重构的总目标是：

Document1 变成稳定、可审计、短周期优先的证据/研究底座生产线。
Document2 变成强类型、可恢复、可审计的 expectation unit 文档事务生产线。
LLM 只负责语义生成、审查判断和修订建议，不拥有最终事务提交权。
程序化事务层负责证据归一化、质量门、修订应用、blocker 状态、promotion 和 commit。

---

## 一、硬性禁止事项

1. 不允许为了通过某个 smoke test 继续在 `initialization.py` 增加 Document2 resolver、sanitizer、promotion 特殊分支。
2. 不允许扩大 `normalizer.py` 对 `expectation_unit` patch 形态的兼容。
3. 不允许继续新增 placeholder marker、unpromotable marker、fallback 文案作为质量闸。
4. 不允许让 O1、A1、C1、C3、O4 直接拥有 Document2 最终 BlackboardPatch 提交权。
5. 不允许让 O1 resolver 直接关闭 objection，objection 是否关闭必须由 deterministic revalidation 或 transaction layer 决定。
6. Document2 内部第一版只接受完整 `ExpectationUnitDocument` candidate，不接受 partial patch、indexed list merge、flat fields、path map、局部字段 overlay。
7. promotion 必须是只读 gate，不允许在 promotion 阶段修改 candidate document。
8. 不允许削弱现有 Document2 eval hard gates、rubrics、Brief State 审查规则。
9. 不允许把 pending patch 当作 stable `expectation_unit`。
10. 不允许写 ticker-specific sanitizer，例如只为 MU、某个 `$36b`、某个特殊 case 写硬编码修复。
11. 不允许一次性大改 Document1、Document2、Document3。必须小步提交，每一步说明是否行为保持。

---

## 二、允许事项

1. 可以新增 characterization tests。
2. 可以新增债务地图文档。
3. 可以做行为保持的物理拆分。
4. 可以新增 Document2 中间模型，但不能立即破坏旧流程。
5. 可以新增 legacy adapter，但必须明确删除条件。
6. 可以把确定性质量规则抽成纯函数或独立 service。
7. 可以保留旧路径作为临时兼容，但必须有测试和迁移计划。
8. 可以重构 prompt，但不能用 prompt 掩盖事务边界问题。

---

## 三、最终目标架构

### Document1

Document1 是证据/研究底座，不是给 Document2 直接塞全文上下文的长文档仓库。

Document1 应输出两类产物：

1. `GlobalResearchDocument`：给用户和 Blackboard 使用的稳定研究文档。
2. `Document1ContextPack`：给 Document2 使用的压缩输入包。

`Document1ContextPack` 至少包含：

* ticker
* 近 30 天核心公司事实
* 近 30 天行业/宏观/市场驱动
* market trace 摘要
* catalysts
* risks
* key variables
* evidence_refs
* known_gaps
* 不应被当成 fresh catalyst 的旧事实

Document1 默认研究窗口应以近 30 天为主，长周期内容只能作为背景解释，不能把旧事实包装成新催化剂。

---

### Document2

Document2 是 expectation unit 文档事务系统，不是多 agent 接力写文档。

Document2 内部流程应变成：

`ExpectationShell`
→ `ExpectationUnitCandidate`
→ `Document2ReviewFinding` / `EvidenceAssessment`
→ `Document2ResolutionPlan`
→ `Document2Revision`
→ `Document2PromotionCandidate`
→ `BlackboardPatch`

强规则：

1. O1 construction 只生成 expectation shell。
2. O1 detail 只生成完整 `ExpectationUnitDocument` candidate。
3. A1/C1/C3/O4 只生成 review findings，不修改文档。
4. O1 resolver 只生成 resolution plan 或 revised full candidate，不直接提交 patch。
5. deterministic transaction layer 负责应用 revision、重新校验、关闭或保留 blocker。
6. promotion 只验证和提交，不修改文档。

---

## 四、阶段拆分

### 第 0 步：冻结边界和债务地图

先不要改业务行为。

新增：

`docs/refactor/document1_document2_workflow_map.md`

文档中必须列出：

1. `INITIALIZATION_NODES` 中 Document1、Document2、Document3 每个节点对应的实现函数。
2. `initialization.py` 中所有主要函数的职责分类：

   * workflow 编排
   * agent 调用
   * context 构造
   * output validation
   * evidence normalization
   * review/finding 创建
   * revision/patch mutation
   * objection/delegation transaction
   * deterministic sanitizer
   * promotion/commit
   * recovery/idempotency
   * audit/trace
   * mock/test fixture
3. 标出哪些函数属于 29 轮 Document2 eval loop 中形成的补丁逻辑。
4. 标出哪些函数应迁移到：

   * `workflows/document1/*`
   * `workflows/document2/generation.py`
   * `workflows/document2/review.py`
   * `workflows/document2/evidence.py`
   * `workflows/document2/transaction.py`
   * `workflows/document2/promotion.py`
   * `workflows/initialization/*`
5. 标出哪些逻辑是 behavior-preserving extraction，哪些逻辑未来需要协议切换。

验收标准：

* 不改变 runtime 行为。
* 不修改 hard gates 和 rubrics。
* 不新增 blocker fix。
* 不新增 normalizer 兼容。
* 提交债务地图和初始测试说明。

---

### 第 1 步：行为保持地拆分 `initialization.py`

目标是先降低文件复杂度，不改变业务行为。

建议拆分方向：

`workflows/initialization/orchestrator.py`
只保留总节点调度、checkpoint 推进、stop_after、resume、异常收口。

`workflows/initialization/agent_dispatch.py`
放 agent 调用、并发派发、retry、timeout、idempotency。

`workflows/initialization/audit.py`
放 Working Memory audit、tool usage audit、failure audit、workflow exception audit。

`workflows/initialization/recovery.py`
放 stale dispatch recovery、parallel outcome recovery。

Document1 相关逻辑迁到：

`workflows/document1/builder.py`
`workflows/document1/context.py`
`workflows/document1/validators.py`

Document2 相关逻辑先迁到：

`workflows/document2/legacy_pipeline.py`
`workflows/document2/legacy_quality.py`
`workflows/document2/legacy_promotion.py`

注意：第一步只做物理拆分，不改行为。可以保留 legacy 命名，明确这是旧流程搬迁，不是新协议完成。

验收标准：

* `BlackboardInitializationWorkflow` 对外导入路径保持兼容。
* eval 脚本入口保持兼容。
* 节点顺序不变。
* 现有测试通过。
* `initialization.py` 明显变薄。
* 没有新增业务判断。
* changelog 明确说明这是行为保持拆分。

---

### 第 2 步：重建 Document1 compact context

目标是把 Document1 固化为 Document2 的证据底座。

新增：

`workflows/document1/context_pack.py`

定义：

`Document1ContextPack`
`ClaimDigest`
`EvidenceDigest`
`MarketTraceDigest`
`Document1KnownGap`

要求：

1. Document1 主窗口以近 30 天为主。
2. 长周期信息只能作为背景，不可作为 fresh catalyst。
3. Document2 construction/detail/review 不应直接消费完整 GlobalResearch 长文本。
4. Document2 应优先消费 `Document1ContextPack`。
5. `GlobalResearchDocument` 仍然保留，不影响用户可读和 Blackboard 存储。

验收标准：

* BuildGlobalResearch 行为不退化。
* Document1ContextPack 可从现有 GlobalResearch 产物生成。
* Document2 context token 明显下降。
* 旧事实不会被标为新催化剂。
* 不改 Document2 resolver。

---

### 第 3 步：新增 Document2 canonical contracts，但不切主流程

新增：

`workflows/document2/contracts.py`
`workflows/document2/legacy_adapter.py`

至少定义：

`ExpectationUnitCandidate`
`Document2ReviewFinding`
`EvidenceAssessment`
`Document2ResolutionPlan`
`Document2Revision`
`Document2PromotionCandidate`
`Document2TransactionAudit`

第一版 Document2 内部只接受完整 `ExpectationUnitDocument` candidate。

`legacy_adapter.py` 只允许从以下旧形态转换：

1. `patch.after` 是完整 `ExpectationUnitDocument`。
2. CREATE full document。
3. UPDATE full document replacement。

必须拒绝：

1. flat fields。
2. partial update。
3. indexed list merge。
4. `{index, after}` wrapper。
5. path map。
6. changes map。
7. 任何无法还原为完整 `ExpectationUnitDocument` 的形态。

验收标准：

* 新 contracts 有单元测试。
* full document replacement 可以转成 `Document2Revision`。
* ambiguous patch shape 必须明确失败。
* 主 workflow 行为暂时不变。
* `legacy_adapter.py` 标明删除条件。

---

### 第 4 步：改 GenerateExpectationDetails，O1 只产 candidate

目标是切断 O1 detail 直接产 BlackboardPatch 的路径。

新增输出 schema：

`ExpectationDetailCandidateResult`

字段至少包括：

* candidate: `ExpectationUnitDocument`
* evidence_refs
* unknowns
* rationale

流程变成：

`ExpectationShell`
→ O1 生成 `ExpectationDetailCandidateResult`
→ 程序验证 identity 不变
→ 程序验证 detail quality
→ 程序生成 `Document2Revision`
→ 存入 Document2 pending revision state

要求：

1. O1 detail 不再直接输出 BlackboardPatch。
2. 每个 shell exactly one candidate。
3. candidate 必须是完整 `ExpectationUnitDocument`。
4. candidate 不能改 expectation_id、expectation_name、direction。
5. 旧 proposed_patches 路径只能通过 legacy adapter 临时兼容。

验收标准：

* GenerateExpectationDetails stop-after 可以导出 candidate/revision 状态。
* pending patch 不再是 detail 阶段的主要内部状态。
* 旧路径测试仍保留，但标记 legacy。
* 不改 resolver。

---

### 第 5 步：拆 review 和 evidence

目标是让 review 专注于审查与补充必要信息，但不直接修改文档或提交 patch。

新增：

`workflows/document2/review.py`
`workflows/document2/evidence.py`
`workflows/document2/numeric_sanity.py`
`workflows/document2/price_reaction.py`

A1/C1/C3/O4 的职责：

A1：审查 DoxAtlas 支撑与反证，并在 finding 中补充必要的证据说明或缺失信息。
C1：审查基本面、filing、财务、公司事实，并在 finding 中补充关键事实或缺口说明。
C3：审查行业、peer、供应链、政策、宏观相关性，并在 finding 中补充必要的上下文或对比信息。
O4：审查 price reaction、OHLCV、market trace，并在 finding 中补充必要的市场行为解释或数据缺口说明。

reviewer 输出：

`Document2ReviewFinding`（其中可以包含必要的补充内容、证据说明或上下文信息，但不直接形成 patch）

deterministic evidence layer 输出：

`EvidenceAssessment`

numeric sanity、price reaction、placeholder detection 必须作为 deterministic quality finding，不直接创建 Blackboard objection，不直接修改 checkpoint。

验收标准：

* reviewer 不输出 proposed_patches。
* reviewer 不修改 candidate。
* reviewer 的补充内容必须封装在 `Document2ReviewFinding` 中，而不是直接写入文档。
* numeric sanity 不在 initialization 主路径中。
* evidence sufficiency 使用 typed status：

  * sufficient
  * insufficient
  * unavailable
  * stale
  * contradictory
* ReviewExpectationFields 可解释每个 finding 属于哪个 expectation_id 和 field_path。

---

### 第 6 步：重做 resolver 和 transaction

目标是把“LLM 修订建议”和“事务落地”分离。

新增：

`workflows/document2/resolver.py`
`workflows/document2/transaction.py`

O1 resolver 只输出：

`Document2ResolutionPlan`

字段至少包括：

* expectation_id
* decisions
* revised_candidate，可空
* evidence_requests
* unresolved_reason
* rationale

transaction layer 负责：

1. 验证 revised_candidate 是完整 `ExpectationUnitDocument`。
2. 验证 identity 不变。
3. 应用 deterministic quality gates。
4. 生成新的 `Document2Revision`。
5. 根据 revalidation 关闭或保留 findings / objections。
6. 记录 `Document2TransactionAudit`。
7. 更新 Document2 pipeline state。

强规则：

1. O1 声称 resolved 不等于 objection resolved。
2. 只有 transaction layer 能关闭或重新打开 blocker。
3. 只有 transaction layer 能把 revision 标为 promotion_ready。
4. deterministic sanitizer 不得直接 resolve objection。
5. checkpoint.pending_patches 不再由 resolver 直接替换。

验收标准：

* `_resolve_blockers` 不再直接混合 A2、O1、deterministic sanitizer、patch replacement。
* O1 resolver 不再输出 raw BlackboardPatch。
* resolver failure 能表达成 typed unresolved state。
* revalidation 失败时 blocker 必须保留。

---

### 第 7 步：promotion 只读化

新增或重构：

`workflows/document2/promotion.py`

promotion 输入：

`Document2PromotionCandidate`

promotion 只能做：

1. schema validation。
2. 检查无 blocking finding。
3. 检查 evidence sufficiency。
4. 检查无 placeholder。
5. 生成最终 BlackboardPatch。
6. 调用 BlackboardService submit_patch。
7. 写 CommitLog 和 audit。

promotion 禁止：

1. 修改 candidate。
2. 重写 price_reaction。
3. 删除数字。
4. 添加 fallback 文案。
5. 修 OHLCV chronology。
6. close objection。
7. 修改 evidence refs。

验收标准：

* promotion 前后 document 内容一致。
* promotion failure 是 typed blocker，不是隐式 rewrite。
* `_promote_pending_patches` 不再调用 price reaction normalizer。
* promotion 只负责 validate 和 commit。

---

### 第 8 步：删除 legacy 兼容和补丁墓碑

在新协议稳定后，删除或迁移：

1. `normalizer.py` 中 expectation_unit flat patch guessing。
2. `_UNPROMOTABLE_EXPECTATION_TEXT_MARKERS`。
3. promotion-time price reaction normalization。
4. resolver-time deterministic sanitizer patch pile。
5. Document2 prompt 中大量用于掩盖事务边界不清的 fallback 文案。
6. legacy adapter 中不再使用的旧 patch 转换路径。

验收标准：

* normalizer 不再理解 Document2 业务语义。
* initialization 主路径不再包含 Document2 sanitizer 细节。
* Document2 failure 能定位到 generation、evidence、review、transaction、promotion 中某一层。
* smoke failure 不再只表现为“又一个 blocker”。

---

## 五、每一步交付格式

每一步完成后必须输出：

1. 本步目标。
2. 是否行为保持。
3. 修改文件列表。
4. 新增测试列表。
5. 删除或迁移了哪些复杂度。
6. 保留了哪些 legacy path。
7. 下一步建议。
8. 当前风险。

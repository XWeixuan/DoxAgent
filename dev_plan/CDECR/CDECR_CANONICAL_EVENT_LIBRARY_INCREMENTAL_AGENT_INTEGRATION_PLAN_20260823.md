# CDECR Canonical Event Library 增量维护与 Agent 接入方案

> 日期：2026-08-23  
> 状态：可执行设计方案，尚未实施  
> 范围：CDECR Runtime Registry 之后的事件库持久化、增量整理、发布、导出和 Agent 读取  
> 不在本方案范围：修改 CDECR Mention/Atomic/Package 生成业务逻辑、同步 API 化 CDECR 全流程、最终 SQL/Prompt/展示 Schema 定稿

## 1. 结论

当前阶段采用以下总体结构：

1. 保留 CDECR Runtime Registry，作为完整运行记录和原始结果库。
2. 新建独立的 Canonical Event Library SQLite，作为唯一可编辑、可发布的业务事件库。
3. Canonical Event Library 内部区分 `Pending Delta`、`Working Revision`、`Published Revision`，不建立第二个可独立编辑的事件库。
4. 每次只从 Runtime Registry 增量提取尚未处理或已变化的 Atomic，不完整复制 Runtime Registry。
5. 事件库 Agent 每批读取：
   - 当前完整的 Published Canonical Event Library；
   - 本轮全部新增/变化 Atomic；
   - 极短的 Runtime Package 归属提示。
6. 当前正式新旧判定采用 LLM 全量注入，不依赖检索候选；检索仅作为 shadow 灰度能力。
7. Mention、Evidence、LLM audit、Runtime 决策过程不进入 Canonical 展示或 Agent payload。
8. 任何被选中或被展示的 Package 必须完整包含其全部 Canonical Atomic，不允许 Top-K Atomic 截断。
9. Agent 只输出批量 Change Plan，不直接逐行修改 SQLite；确定性程序在单事务中校验并 Apply。
10. 其他 Agent 永远只读取原子发布后的 Published Revision，不读取 Working/Pending 状态。

## 2. 当前项目事实与接入约束

### 2.1 CDECR 当前已有能力

- `AtomicEvent` 已具有规范命题、事件类型、时间和身份字段，但同时携带 Mention 等 Runtime 数据；Canonical Event Library 不应原样复制整个对象。
- `EventPackage` 已具有标题、摘要、时间范围和成员 Atomic ID，可作为导入参考，但 Runtime Package 不应成为 Canonical Package 的强制事实。
- Package V3 已有 rolling registry、version、batch、membership 和 finalized snapshot 机制，可复用其版本化/CAS经验，但不能直接把 Runtime registry 当作可编辑事件库。
- 当前 `result_export.py` 可导出 Package → Atomic，但同时包含 Mention、Source、审计型数据，不能直接作为下游 Agent payload。

### 2.2 DoxAgent 当前预留接口

- Document2 已预留只读 `EventLibraryProvider.load(ticker, as_of)`，当前默认 `NOT_CONFIGURED`。
- 当前编排会在公共上下文及多个 O1 阶段重复放入完整 `event_library` payload。
- 因此不能直接把完整事件库塞入现有 `OptionalInput.payload` 后沿所有节点重复传递，否则会形成显著 token 倍增。
- 当前 WorkspaceClient 主要提供文本文件读写，不适合作为 Agent 直接维护 SQLite 事务的接口。

### 2.3 工作树风险

当前 `main` 分支存在大量未提交的 Document2/Codex Runtime 修改。本方案实施时必须：

- 不覆盖现有未提交改动；
- 将 Event Library 作为独立模块落地；
- 对 Document2 只做窄接口接入；
- 在实际改动前重新核对 `src/doxagent/workflows/codex_document2/` 的最终状态。

## 3. 业务不变量

下列约束在后续 Schema、Prompt 和实现变化中都不得破坏。

### 3.1 数据边界

- Runtime Registry 是 CDECR 生产过程与原始结果的事实源。
- Canonical Event Library 是经 Agent 整理后的业务事实源。
- Canonical Event Library 不反向修改 Runtime Registry。
- Runtime Package 仅作为导入和判断提示，不自动决定 Canonical Package。

### 3.2 Atomic 完整性

- Mention 是过程层内容，不进入发布视图。
- Canonical Package 下的所有有效 Canonical Atomic 必须完整展示。
- 运行时压缩只能减少进入上下文的 Package 数量，不能截断已选 Package 内的 Atomic。
- 当前阶段正式路径不减少 Package，直接注入完整 Published Library。
- Canonical Atomic 可以吸收多个重复 Runtime Atomic，但必须保留所有业务事实和最小来源映射。

### 3.3 发布一致性

- Working Revision 未完成时，其他 Agent 继续读取上一版 Published Revision。
- 发布必须通过一个原子 `published_version` 指针切换完成。
- 同一 Agent run 必须固定读取一个 `library_version`；不得在同一轮推理中静默切换版本。
- Agent Change Plan 必须声明 `base_library_version`，过期计划不得直接 Apply。

### 3.4 失败边界

- Delta 编译失败：不启动事件库 Agent，不改变 Published Revision。
- Agent 请求失败：保留 Pending Delta，不改变 Published Revision。
- Change Plan 非法：只修复非法项或保留 Pending，不重做已合法决策。
- Apply/CAS 冲突：整个事务回滚，不形成半发布状态。
- 导出失败：Published Revision 仍有效，允许从同一版本重新生成导出物。

## 4. 目标架构

```text
CDECR Runtime Registry
  - Source / Mention / Evidence
  - Atomic / Runtime Package
  - Model call / Audit / Failure state
            │
            │ Incremental Delta Compiler
            ▼
Canonical Event Library SQLite
  ├─ Import Cursor / Runtime Mapping
  ├─ Pending Delta Batch
  ├─ Canonical Package / Atomic
  ├─ Working Revision / Change Plan
  ├─ Published Revision / Version Head
  └─ Compiled Snapshot Cache
            │
            ├─ Event Library Maintainer Agent
            ├─ Full human-readable Package → Atomic export
            ├─ New/old judgment Agent full payload
            └─ Other DoxAgent read-only consumers
```

### 4.1 两个物理数据库

#### A. CDECR Runtime Registry

继续保存：

- 所有原始和中间数据；
- Mention、Evidence、Field、Atomic、Runtime Package；
- LLM request/response 和编排审计；
- 重试、降级和失败状态。

#### B. Canonical Event Library SQLite

只保存：

- 经过整理且有业务价值的 Canonical Package；
- 每个 Package 下完整的 Canonical Atomic；
- 必要时间、状态、关系和版本；
- Runtime Atomic 到 Canonical 结果的最小映射；
- 增量批次状态和原子发布所需元数据。

Canonical DB 不保存 Mention、Evidence 全文、模型 reasoning 或 Runtime payload 副本。

### 4.2 Canonical DB 三层逻辑状态

#### Pending Delta

保存本轮新出现或发生内容变化、但尚未完成 Canonical 处置的 Atomic 工作集。

#### Working Revision

保存 Event Library Agent 已生成、但尚未发布的 Change Plan 及其确定性 Apply 结果。

#### Published Revision

保存其他 Agent 当前可见的正式版本。Published Revision 只能通过成功事务和 CAS 切换。

### 4.3 SQLite 使用边界

第一阶段使用 SQLite，要求：

- 数据库位于本机固定服务目录，不放入一次性 Agent workspace；
- WAL 模式；
- 多读单写；
- 所有写入通过 `EventLibraryRepository/Service`；
- Agent 无任意 SQL 权限；
- 所有语义变更先形成 Change Plan，再由服务层批量 Apply。

若未来出现多主机并发写入，再将 repository 实现迁移到 PostgreSQL，Agent 工具和业务协议不变。

## 5. 持久化增量处理流程

### 5.1 初次建立事件库

首次建立时：

1. 选定一个已 FINALIZED 的 Runtime Registry snapshot。
2. 确定性提取 Runtime Package 和全部 Atomic。
3. 编译为初始 Pending Delta。
4. Event Library Agent 对完整输入做首次整理。
5. 确定性 Apply 为 `Library V1`。
6. 验证所有被接受 Atomic 恰好归属于一个有效 Canonical Package，或有明确 disposition。
7. 原子发布 `V1`。

当前测试产物存在已知聚类质量问题，因此初次导入状态必须是 `UNREVIEWED`，不得把 Runtime Package 当作已确认 Canonical Package。

### 5.2 日常增量示例

假设昨天是 `Published V42`，今天 Runtime Registry 新增100个 Atomic：

1. `DeltaCompiler` 读取上次 import cursor。
2. 只提取新 ID、新 version 或内容 fingerprint 已变化的 Atomic。
3. 为每个 Atomic 附上极短 Runtime hint：Runtime Package ID、可能的旧 Canonical target、时间/issuer等必要确定性字段。
4. 生成不可变 `Delta Batch D43`，不复制旧 Runtime 数据。
5. 编译 Agent 输入：完整 `Published V42` + `D43` 的100个 Atomic。
6. Agent 输出 Change Plan：加入旧包、创建新包、重复、低价值丢弃、保留待定、调整旧包。
7. 本地 validator 校验 ID coverage、引用有效性、Atomic 唯一归属和操作冲突。
8. 在单事务中形成 Working V43。
9. 验证通过后 CAS：`published_version 42 → 43`。
10. 生成 V43 的人类视图和 Agent 紧凑 payload cache。

### 5.3 Runtime Package 的处理原则

- Runtime Package 与 Canonical Package 之间只维护 suggestion mapping。
- 新 Atomic 被 Runtime 合入旧包时，可以优先提示 Agent 检查对应 Canonical Package，但不能确定性加入。
- Runtime Package 后续拆分或重组，不自动反向修改 Canonical Package。
- 已经由 Agent 编辑的 Canonical Package，下一次导入只能产生候选变更，不能被 Runtime 结果覆盖。

### 5.4 幂等识别

最小识别材料包括：

- Runtime registry identity/scope；
- FINALIZED batch 或 epoch ID；
- Runtime Atomic ID 和 version；
- Atomic business payload fingerprint；
- 上次成功 import cursor。

同一 Runtime batch 重跑必须产生0个新 Pending 项；内容不变但 Runtime Package 归属变化，只生成短 membership-change hint，不重新复制 Atomic。

## 6. Canonical 数据缩减原则

Canonical Event Library 比 Runtime Registry 小是业务目标，不是异常。缩减发生在以下边界：

| Runtime 内容 | Canonical 处理 |
| --- | --- |
| Mention | 不导入 |
| Evidence 全文 | 不导入，只保留必要来源引用 |
| LLM request/response | 不导入 |
| 编排审计 | 不导入 |
| 被拒绝或低价值 Atomic | 保存最小 disposition，不进入发布库 |
| 重复 Runtime Atomic | 映射到同一 Canonical Atomic |
| Runtime Package | 作为提示，不直接复制为 Canonical Package |
| 有业务价值的 Atomic | 完整保留 Canonical proposition 和必要时间/类型信息 |

“完整展示 Atomic”指完整展示全部已被 Canonical 接受的 Atomic，而不是把所有 Runtime 噪声和重复项搬入发布库。

对于多个重复 Runtime Atomic：

- 可以归并为一个 Canonical Atomic；
- Canonical proposition 必须保留完整业务事实，不能降格为泛化摘要；
- 原 Runtime Atomic IDs 作为内部 lineage 引用，不进入默认 Agent payload；
- 如果事实存在重要限定差异，不得为了压缩而合并。

## 7. Event Library Agent 运行协议

### 7.1 启动方式

第一阶段继续采用脚本启动，不建设同步 HTTP 分析接口。

建议形成一个入口：

```powershell
uv run python scripts/cdecr_update_event_library.py `
  --runtime-registry <runtime.sqlite3> `
  --event-library <event_library.sqlite3> `
  --scope MU `
  --export-dir <output-dir>
```

脚本职责：

1. 检查 Runtime batch 已 FINALIZED；
2. 编译或恢复 Pending Delta；
3. 固定 `base_library_version`；
4. 生成 Dedicated Agent workspace 输入；
5. 启动 Event Library Maintainer Agent；
6. 读取并校验 Change Plan；
7. 事务 Apply 和 publish；
8. 生成导出物；
9. 输出简短运行报告。

应支持：

- `--prepare-only`：只生成 Delta 和 Agent 输入，不调模型；
- `--resume`：从未完成 batch 继续，不重复模型成功项；
- `--apply-plan <path>`：应用已人工审查的 Change Plan；
- `--export-only --library-version N`：重建指定版本导出物。

### 7.2 Agent 输入

正式输入只包含两部分：

#### A. 完整 Published Canonical Library

每个 Package 至少提供：

- 请求内短 Package ID；
- Canonical title；
- 必要时间字段；
- 极短 summary；
- 完整 Atomic 列表；
- 每个 Atomic 的请求内短 ID和完整 Canonical proposition。

#### B. 本轮 Delta Atomic

每个新增 Atomic 至少提供：

- 请求内短 Delta Atomic ID；
- 完整 proposition；
- 必要时间/类型字段；
- 极短 Runtime Package hint；
- 已存在 Canonical target suggestion（如有）。

明确排除：

- Mention；
- Evidence 正文；
- 长 UUID；
- LLM reasoning；
- Runtime audit；
- 不影响归属判断的 Schema 字段；
- 同一内容的重复序列化。

### 7.3 全量注入口径

当前正式路径必须把完整 Published Canonical Library 注入维护/新旧判断 Agent。

- 不使用检索结果删除任何 Package。
- 不使用 Top-K Atomic。
- 不把某个 Package 的摘要当作其 Atomic 替代品。
- 一个维护 batch 原则上只注入一次完整 Library，不在多个内部节点重复注入。
- Agent 输出只返回变化，不重发完整 Library。

如果单批 Delta 过大导致输出上限风险，优先拆分 Delta wave；同一 wave 固定一个 base/working version并顺序 Apply。第一阶段不设计并行写入多个 Working Revision。

### 7.4 最小 Change Plan

Agent 输出采用批量结构化 Change Plan。动作集合建议为：

```text
ADD_TO_EXISTING
CREATE_NEW_PACKAGE
DUPLICATE_OF_ATOMIC
DROP_LOW_VALUE
KEEP_PENDING
MOVE_CANONICAL_ATOMIC
MERGE_PACKAGES
SPLIT_PACKAGE
EDIT_PACKAGE_METADATA
SUPPRESS_PACKAGE
RESTORE_PACKAGE
```

每个 Delta Atomic 必须恰好出现一次；旧 Canonical 对象只在发生变化时出现。

Change Plan 最小内容：

```text
base_library_version
delta_batch_id
atomic_actions[]
package_operations[]
affected_package_descriptions[]
```

默认不要求模型输出 reasoning。若无法判断，使用 `KEEP_PENDING`，而不是生成长解释。

### 7.5 本地 validator

必须确定性校验：

- 每个 Delta Atomic 恰好处置一次；
- 所有短 ID 均可还原到本批映射；
- target Package/Atomic 存在且版本有效；
- 同一 Atomic 不会被加入多个 active Package；
- merge 无循环；
- split/move 后 Atomic coverage 完整；
- suppressed Package 不作为新增 membership target；
- `base_library_version` 等于当前 Published head；
- 更新后的 Package 不存在空成员，除非其状态明确为 suppressed/merged。

非法项只进入局部 repair 或保留 Pending；不得因一条非法 action 重新请求整库。

## 8. 工具与 Agent 权限设计

工具作为控制面使用，不作为逐条搬运 Runtime 数据的数据面。

### 8.1 只读工具

```text
event_library.get_manifest
event_library.list_packages
event_library.get_packages
event_library.get_changed_since
event_library.get_pending_batch
```

其中 `get_packages` 返回选中 Package 的全部 Atomic，不支持隐藏式 Top-K Atomic。

### 8.2 维护工具

```text
event_library.submit_change_plan
event_library.validate_change_plan
event_library.publish_working_revision
event_library.discard_working_revision
event_library.export_version
```

第一阶段不向模型暴露任意 SQL、逐行 insert/update/delete 或物理删除工具。

### 8.3 交互式局部维护

将来专门的事件库 Agent 可以分页读取全库并执行局部维护，但仍应形成一个 Change Plan 后批量提交。读取全库不等于一次性把所有数据放在单条回复中；Agent可以分页遍历，具体编辑时完整读取目标 Package 的全部 Atomic。

## 9. Published Revision 与消费者读取

### 9.1 发布指针

Canonical Event Library 维护：

```text
current_published_version
current_published_hash
current_working_batch_id (nullable)
```

发布事务：

1. 校验 expected version/hash；
2. 写入或更新 Canonical Package/Atomic 版本；
3. 写入 active membership；
4. 写入必要 redirect/tombstone；
5. 生成不可变 revision snapshot metadata；
6. CAS 更新 published head；
7. commit。

任一步失败则整个事务回滚。

### 9.2 其他 Agent 读取

- 默认读取任务开始时的 latest Published Revision。
- 长任务将 version/hash 固定在 input manifest。
- 任务过程中不得自动切换版本。
- 如确需刷新，必须作为显式新阶段重新加载并记录新 version。
- Working/Pending 数据对普通消费者不可见。

### 9.3 导出缓存

每个 Published Revision 可以确定性生成：

```text
event_library_v43_<hash>.md
event_library_v43_<hash>.json
```

这些是不可变缓存，不是第二事实源。文件丢失可从 Canonical DB 重建；不得被 Agent 直接编辑后反向覆盖数据库。

## 10. 展示与编译原则

最终具体 Schema 后续单独对齐，本方案只固定不可变展示原则。

### 10.1 人类可读完整版

- 按 Canonical Package 分组；
- 展示 Package title、必要时间、短 summary；
- 展示该 Package 下全部 Atomic；
- 不展示 Mention；
- 默认不展示 Evidence、长内部 ID、审计和 lineage；
- 可选展示 Package/Atomic 的稳定短 ID和当前版本。

### 10.2 Agent 紧凑完整版

- 与人类版使用同一 Published Revision；
- 保留全部 Package 和 Atomic；
- 使用请求内短 ID；
- 删除 Markdown 装饰和重复字段；
- Package公共字段只出现一次；
- 不重复在 Atomic 中携带 Package 信息；
- 输入编译器输出 token estimate，供运行前观测，不作为阻塞性校验。

### 10.3 普通 Agent 接入

`EventLibraryProvider` 返回：

- availability；
- library version/hash/as_of；
- 编译后的 Published payload或workspace artifact引用；
- 不返回 Runtime Registry路径。

不能继续把完整 payload无差别放入所有 Document2 turn。每个工作流必须明确：

- 哪个 Agent/节点需要完整事件库；
- 是否在同一 thread 中只加载一次；
- 其他节点只传 version/hash或必要结果引用。

## 11. 新旧判定与 shadow 检索

### 11.1 当前正式路径

当前最优质量路径为：

```text
完整 Published Canonical Library
+
待判断新消息/新增 Atomic
→ LLM 新旧及归属判断
```

原因：当前 CDECR 本身的 Mention/Atomic/Package 召回仍不足，不能再叠加一个未经验证的检索召回瓶颈。

### 11.2 shadow 路径

可以并行实现不影响业务结果的 shadow retrieval：

- issuer/entity；
- instrument；
- event time/time window；
- event family；
- analyst institution；
- lexical/FTS；
- 可选 semantic embedding。

shadow 只记录它召回了哪些 Package，并与全量 LLM 最终使用的 Package 比较。不得：

- 用 shadow 候选替代正式全量输入；
- 因候选未召回而删除 Package；
- 为 shadow 增加 LLM reasoning 或正式 payload 字段。

后续只有在代表性数据上证明 candidate coverage 稳定达标，才另立方案决定是否切换。

## 12. 最小审计与留痕

审计只服务于幂等、恢复和版本一致性，不追求解释模型思维过程。

### 12.1 必须保留

- Runtime source registry identity；
- source FINALIZED batch/epoch；
- Delta batch ID和input hash；
- base/output library version/hash；
- 每个 Delta Atomic 的 action和target引用；
- batch状态、错误码和attempt count；
- Published head切换结果。

### 12.2 明确不保留或不注入

- 不新增逐 Atomic reasoning；
- 不要求正常通过项解释原因；
- 不把 Runtime model calls复制到 Canonical DB；
- 不把完整 before/after snapshot复制进每条审计；
- 不把审计内容放入 Agent正式输入；
- 不为展示目的暴露内部 lineage 和长 ID。

完整历史由不可变 revision和最小 Change Plan共同恢复，无需额外重复 payload。

## 13. 建议模块与文件边界

具体命名可在实施前调整，但职责应保持分离。

```text
src/doxagent/event_library/
  contracts.py          # Canonical/Delta/Change Plan内部契约
  repository.py         # SQLite实现、事务、CAS、版本读取
  service.py            # 业务操作、validator、publish
  importer.py           # Runtime Registry → incremental Delta
  compiler.py           # Published payload / Markdown / JSON编译
  agent_runner.py       # Dedicated Codex Agent调用与局部repair
  provider.py           # DoxAgent只读EventLibraryProvider实现
  tools.py              # 受限的读写工具注册

scripts/
  cdecr_update_event_library.py
  cdecr_export_event_library.py

tests/
  test_event_library_repository.py
  test_event_library_delta_import.py
  test_event_library_change_plan.py
  test_event_library_agent_runner.py
  test_event_library_provider.py
  test_event_library_export.py
```

### 13.1 与 CDECR 的依赖方向

- `event_library.importer` 可以读取 CDECR port/registry。
- `src/cdecr/` 不得 import `doxagent.event_library`。
- Canonical Event Library 不与 CDECR SQLite 共表或共 schema version。
- 导入器只接受 FINALIZED 或用户显式指定的冻结 snapshot。

### 13.2 与 Document2 的依赖方向

- 新 `EventLibraryProvider` 实现当前只读接口，但返回 Published Revision。
- 在未确认 Document2 当前未提交实现稳定前，不直接大范围改 orchestrator。
- 接入时必须移除完整 payload在多 turn 的无差别重复；版本元数据可继续放 common context。

## 14. 实施步骤

方案可按下列顺序一次性落地，但每一步均有独立验收点。

### Step 0：冻结契约与样本

1. 固定一个小型 Runtime Registry和现有162篇结果作为只读样本。
2. 统计 Runtime Atomic、Runtime Package和当前人类导出数量。
3. 固定 Atomic完整性口径：所有被接受 Atomic 必须出现在导出和Published membership中。
4. 记录当前 Document2 EventLibraryProvider接口和工作树hash，避免覆盖并行修改。

### Step 1：Canonical Repository

1. 新建独立 SQLite schema与repository。
2. 实现 library head、revision、Canonical Package/Atomic、active membership。
3. 实现 Pending Delta、Runtime mapping、import cursor和batch状态。
4. 实现 Working Revision和CAS publish。
5. 实现 soft delete、redirect和版本读取。
6. 完成repository事务、幂等和并发测试。

### Step 2：增量 Delta Compiler

1. 读取 FINALIZED Runtime snapshot。
2. 按 Atomic ID/version/fingerprint识别新增或变化项。
3. 编译最小 Delta，不复制 Mention/Evidence/audit。
4. 提供 Runtime Package suggestion和已知Canonical mapping。
5. 同一 batch重跑验证0新增。

### Step 3：Payload Compiler 与 Change Plan Validator

1. 编译完整 Published Package →全部 Atomic payload。
2. 建立请求内短 ID映射。
3. 编译本轮 Delta payload。
4. 实现最小 Change Plan contract。
5. 实现 coverage、ID、membership、merge cycle、base version校验。
6. 实现非法单项局部repair边界。

### Step 4：Dedicated Event Library Agent Runner

1. 脚本创建独立workspace和固定输入文件。
2. 一次加载完整 Published Library和本轮 Delta。
3. Agent只输出 Change Plan。
4. 成功结果持久化后才允许 Apply。
5. 失败支持 `--resume`，不得重复已成功模型调用。
6. provider失败保持Pending并退出，不改变Published版本。

### Step 5：Apply、发布与导出

1. 单事务Apply Change Plan。
2. 执行Atomic唯一归属和Package非空验证。
3. CAS切换 Published head。
4. 生成完整Markdown与紧凑Agent payload。
5. 检查导出Atomic集合与Published集合完全一致。

### Step 6：DoxAgent只读接入

1. 实现配置化 EventLibraryProvider。
2. 默认只读取 Published Revision。
3. 在input manifest记录 version/hash/as_of。
4. 为需要完整库的节点明确单次注入边界。
5. 取消现有跨多个turn重复携带完整payload的路径。
6. 保持未配置时 `NOT_CONFIGURED` fail-open行为。

### Step 7：shadow retrieval

1. 在Canonical DB建立必要的确定性索引/FTS。
2. 运行shadow召回，不影响正式Agent输入。
3. 对比全量判断使用到的Package，输出candidate coverage。
4. 不在本阶段切换正式路径。

## 15. 回归与验收

### 15.1 Repository/版本验收

- 同一 Delta batch重复导入产生0个新项。
- V42维护期间普通读者始终读取V42。
- V43成功发布后新读者读取V43。
- stale Change Plan无法覆盖更新后的Published head。
- Apply中途异常时所有写入回滚。
- soft delete/merge后旧ID仍可解析到状态或redirect。

### 15.2 增量业务验收

构造 `V42 + 100 Atomic`：

- 一部分加入旧Package；
- 一部分创建新Package；
- 一部分重复；
- 一部分低价值丢弃；
- 一部分保持Pending；
- 一部分触发旧Package merge/split。

要求：

- 100个Delta Atomic恰好有一个最终 disposition；
- 原有Canonical Atomic不被重复导入；
- Runtime Package变化不会自动覆盖Canonical编辑；
- 已发布内容只在Change Plan明确涉及时变化。

### 15.3 Atomic完整性验收

- Published active Canonical Atomic集合与完整Markdown Atomic集合完全一致。
- Agent紧凑完整版的Atomic集合与Published集合完全一致。
- 任一Package的成员数必须等于两个导出中的Atomic行数。
- Mention计数必须为0。
- 不允许使用Top-K或ellipsis替代Atomic。

### 15.4 Agent payload验收

- 完整Library在一个维护batch中只序列化/注入一次。
- 使用请求内短ID，无Runtime长UUID重复扩散。
- 不包含Mention、Evidence、Runtime audit和reasoning。
- Change Plan只返回变化项，不重发完整Library。
- payload token统计仅用于观测，不因超过软预算自动丢Atomic。

### 15.5 失败恢复验收

- Agent调用失败：Published version/hash不变，Pending可续跑。
- 一条action非法：合法action保留，只修非法项或留Pending。
- 停电发生在Agent成功后：resume复用Change Plan，不再次调用模型。
- 停电发生在Apply事务中：恢复后不存在半发布版本。
- 导出失败：可从同一Published version无模型重建。

### 15.6 DoxAgent接入验收

- EventLibraryProvider始终返回Published而非Working数据。
- manifest可追溯version/hash/as_of。
- 未配置事件库时现有工作流继续运行。
- 完整payload不会在同一Agent thread的多个turn重复注入。
- 普通Agent无法调用维护工具或读取Canonical DB文件路径。

## 16. 非目标与暂缓项

本轮不做：

- 将完整CDECR分析改成同步HTTP请求；
- 用检索候选替代全量Canonical注入；
- 把Mention/Evidence加入发布展示；
- 为每条决策增加LLM reasoning；
- 建立第二个可独立编辑的简化事件库；
- 直接复用CDECR Package V3 registry作为Canonical业务库；
- 一开始引入PostgreSQL、消息队列或分布式锁；
- 让Agent直接执行SQL或物理删除记录；
- 在本方案内定稿最终对外Schema和UI。

## 17. 发布判断

只有在下列条件同时成立后，才把 Event Library 接入其他正式 Agent：

1. 增量导入幂等；
2. Published/Working隔离成立；
3. Change Plan Apply具备事务和CAS；
4. 全部Published Atomic在导出中完整出现；
5. 同一维护batch不重复注入完整Library；
6. 失败不会污染上一Published版本；
7. 普通Agent只获得只读Published视图；
8. 当前测试样本中的Runtime Package不被未经整理地自动发布。

首版发布后仍保持脚本启动。同步能力、检索裁剪和分布式存储应分别在有真实瓶颈和A/B证据后再设计，避免当前阶段为潜在规模问题叠加不必要复杂度。

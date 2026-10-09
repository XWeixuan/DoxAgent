# 双 OPEN 与独立 D1 产品编排实施记录

依据：[实施方案](./d3_dual_open_d1_products_implementation_plan_20261009.md)。本轮实施编排、输入投影、资产路由及回归；按用户追加要求同步 O3 AGENTS.md。未部署、未切换正式默认版本、未发起实模 Pilot。

## 实施结果

初始化固定采用 Shell owners + OPEN_RESEARCH + OPEN_EVENT + GLOBAL。两个 OPEN 分别负责 C1/C3/C5/Future Nodes 与 Entity Map/网络报告/General + L1-01 Atlas；共享 D1/D2 读取权限保留。GLOBAL 仍仅做 Planning/Integration。

Discovery 先按现有 ticker 并发额度完成全部 Shell，再冻结完整的主/late Lead 原文、原 file#line 引用、producer 状态、缺件和坏行诊断，随后启动两个独立 OPEN。两个 OPEN 读取同一冻结包，互不依赖对方当轮输出；所有当轮 Discovery 主/late Lead 一起进入 Planning。Shell 耗尽重试不阻断健康 OPEN，但保留缺失标记。后续 Build/Integration late 不重开 Planning。

Build 与唯一补研继续采用既有 owner 队列与并发，不增加 OPEN 专属屏障。Agenda 统一规范为真实槽位 ID；旧 OPEN 别名归到 OPEN_RESEARCH，未知、歧义和 GLOBAL 路由同样回落并记录诊断，不产生第三条 OPEN。两个 OPEN 的 workspace/thread 分离，并各自从 Discovery 延续到 Build。

Atlas 从现有静态文件冻结，只物化到 OPEN_EVENT Discovery/Build；task 携带 General + L1-01、文件路径与 SHA。恢复沿用冻结字节，新 run 才读取新版本。MAINTAIN 继续单节点，既不要求 Atlas 依赖，也不启动 OPEN。

新增薄共享投影器 `research_products.py`：以固定已发布 bundle 的三项权威字段及产品状态为准，通过对应 producer 的已接纳 attempt 定位 report/completion，保存原引用、SHA 和独立 citation manifest。结构化节点的空 Markdown 不抹掉 typed 产品；empty、failed、历史 unrecorded、网络 unavailable 明确区分。源文件不可读时明确缺失 provenance，不换 run/attempt 或旧 pre-scan 补值。

D3 在 `shared/d1/` 提供稳定 C1/C3/C5、Future Nodes、formal scan、network JSON/Markdown 及 products_manifest 路径。网络 JSON 为完整 Markdown 的只读包装；原件 SHA 与投影 SHA 分开，保留原 citation。兼容的编号原件与 structure 入口继续存在。

D2 loader、O0/O1 legacy/v2.1 实际 context、Pilot bootstrap 统一携带三项正文、状态及源元数据。manifest 包含 producer report/completion ID 和可读原件路径；网络正文沿用 D1 qualifier，原始 provenance 字节不改写。短网络正文也进入 reports 导航，O1 重复产品的嵌套指针映射到同一读取入口。

D3 Pilot driver 已有完整 SQLite backup 与按 artifact 原路径/SHA 导入的机制；本轮通过独立产品 fixture 验证复用，无需再写一套导入器。SDK 四节点暂停/续跑边界与动态 owner 报告保持。

新 run 使用 orchestration_revision=3；旧活跃 run 保留冻结任务并要求新 run，已完成历史不混入新调度。Lead/Topic/Result/Review/PolicySet 业务字段、D2 DAG、发布合同及正式版本默认不变。

## O3 AGENTS.md 对齐

仅调整 `prompts/codex_v2/document3/AGENTS.md` 的编排事实：真实 owner/职责、Shell 前置结果、两个 OPEN 的材料责任、双 Discovery skill、独立产品状态、Atlas 静态属性和当轮 late 的 Planning 接纳规则。保留既有只读输入、schema、输出路径、ID、Integration/MAINTAIN 与 Pilot 审计约定。其他 prompt、skill、Atlas 正文未因本轮改写或删除。

## 验证记录

新增执行级测试位于 `tests/test_d3_dual_open.py`、`tests/test_d1_product_context.py`，并扩充 `tests/test_context_index.py`。覆盖 N=3/并发 2 的事件屏障、并发 1、Shell 失败、零 Shell、主/late 原行号、双 OPEN 隔离与 Build 线程延续、Shell/OPEN 中断恢复、冻结 Atlas 更新、旧活跃 revision 拒绝、真实路由归一化和 MAINTAIN 依赖边界。

产品测试采用互不相同的三项哨兵与独立 producer/citation，并放入未接纳旧 completion 检验来源选择。实际运行 D2 legacy/v2.1 Worker，检查全部实际节点 context、网络正文索引、来源角色、Pilot 投影一致性；验证 D3 稳定视图与原 SHA、合法空/失败/历史状态、不可读原件及 D3 Pilot 快照导入。

回归结果：

| 检查组 | 结果 | 耗时 |
| --- | --- | --- |
| D2 workflow/v2.1、D2 Pilot coordinator、context index、D1 独立产品回归 | 89 passed | 619.51 秒 |
| D3 orchestration/repair/maintenance、D3 SDK Pilot 回归 | 78 passed | 596.01 秒 |
| 新增双 OPEN/产品输入执行测试及完整 context index 测试 | 16 passed | 101.77 秒 |

三组存在索引回归重叠，不把执行次数直接当作独立用例数。受影响代码与新测试的 Ruff、compileall、合同 JSON 解析和定向 diff check 通过；默认真实资产路径冻结检查确认初始化包含两份完整 Atlas，维护仅要求 role/common/maintain。

最后将产品 fixture 改为通过完整 GlobalResearchBundle 类型校验构造，复验产品输入组 5 passed（44.73 秒），确认正式 FutureNode/EntityRelation 模型的中文别名投影也正确；只剩既有 OpenTelemetry 依赖 DeprecationWarning。

测试中遇到 Windows 长临时目录叠加 attempt-local 索引与原子写入后缀的路径上限，使用较短的测试目录验证真实索引文件；没有扩大本次工作区存储改造范围。正式长路径仍遵循既有索引失败可降级到完整原始 context 的约定。

## 验收边界

离线测试证明调度、合同、完整材料读取入口、来源绑定与恢复行为；不证明两个 OPEN 有实际增量发现，也不证明 Atlas 有效。后续实模需使用具备三个新 D1 产品的同源 MU/D2 case，按四节点串行驱动，评估实质遗漏入口、重复研究与 Integration 补救量，不能以 Topic/Policy 数量增加作为成功标准。

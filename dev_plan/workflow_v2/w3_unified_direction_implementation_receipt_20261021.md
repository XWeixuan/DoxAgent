# W3 单一方向决策重构实施记录

日期：2026-10-21。依据：`w3_unified_direction_refactor_implementation_plan_20261021.md`。

本轮完成本地编排、Schema、Read/API、前端、导出和回测适配。未修改实际 AGENTS/Skill，未调用真实模型、未进行生产历史恢复、未部署。保留其他会话已经存在的启动修复修改。

## 1. 已实现的业务行为

- 新契约 `persistent-runtime.w3.v5`，入口仍为 `w3-entry-v5`；v4 和更早历史保留原解释，不改写原始回执。
- 第一阶段仅 `decision/reason/prior_expectation/expectation_delta` 四字段，方向为 LONG/SHORT/NO_TRADE；LONG/SHORT 必须有非空 prior/delta。旧交易模式、嵌套分支和额外 HIT 字段均拒绝。
- 第二阶段仅 novelty、delta_candidates、watch_actions、pin_actions；不能改写 Policy，继续校验 OLD 的真实 E# 覆盖和 NEW 必有 delta。非法动作逐项隔离，核心输出错误仍走原纠错。
- v5 的 HIT/RELATED、Policy/Condition 身份、reason/confidence 始终来自冻结 W2。HIT+NO_TRADE、无 HIT+LONG、Condition 建议 LONG 但 W3 SHORT 均合法。
- v5 的有效 Policy 召回不按历史交易消费额度裁剪；W2R2、W3 输入及研究工具不再附带额度视图。原消费记录保留，旧 Case 的消费与投递流程保留。
- 新交易统一 origin=W3，按模型根 decision 构造提案，不填写 executed_policy/condition，不自动写 Policy 消费。权限、过期、CLOSED 候选、Case/intent 幂等、外部执行风控不变，不新增 Event/RECENT_TRADE 去重。
- 最终 novelty 只认成功提交的二轮；此前显示原 W1 并标明来源。二轮不能撤销一轮交易。NEW 保留 delta，无 HIT 时保留 coverage gap；OLD+HIT 保留 Badcase，归因来自 W2。
- WATCH SET 仅接受 W2 HIT/RELATED；CANCEL 仍可取消本轮未再次命中但实际可见的原 WATCH。容量、TTL、PIN 和 Sweep 逻辑不变。
- NO_TRADE+成功 OLD+无 HIT+无其他正式产物，宿主在 operations 原事务内幂等补一条 ArchiveRecord，并记录 result_basis。后置失败/未完成、LONG/SHORT、CLOSED 候选不会被假归档。
- 最终 OLD 的 Event 关联与 operations 同事务落盘；失败回滚不提前产生关联。

## 2. 主要代码落点

| 范围 | 文件 |
|---|---|
| 新旧 Schema、显式版本解析 | `persistent_runtime_v2/w3_contracts.py` |
| Bundle/阶段派发/Template 隔离 | `execution_bundle.py`、`factory.py`、`w3.py`、`w3_execution.py` |
| W2 召回与入口 | `service.py`、`router.py` |
| 交易、反馈、归档原子提交 | `w3_execution.py`、`repository.py`、`schema.py` |
| WATCH、历史与研究输入 | `policy_watch.py`、`w3_context.py`、`w3_research_store.py` |
| 共用只读判定 | 新增 `persistent_runtime_v2/case_outcomes.py` |
| 列表/图/Policy 指标/API | `v2_read/projectors.py`、`graph.py`、`policy_hits.py`、`serving_fields.py`、`api_v2/case_detail.py` |
| Read 有界重投影 | 新增 `v2_read/w3_outcome_backfill.py` |
| API Schema 与界面 | 共享 TS 契约、后端 wire_schema、前端生成 Schema、Runtime 列表/详情/导出 |
| 本地回测 | `w3_backtest/runner.py`、`report.py` |

上表 Python 相对路径均以 `src/doxagent/` 为根；第一组已注明模块后的同组短文件名位于同模块。

补齐了 Read 原生字段白名单中的 `w3_contract_version`，否则 API 会丢失版本、错误套用旧分支。新旧 wire 均保留兼容，旧 expert 字段对新版返回 NOT_APPLICABLE，界面与导出改为中性交易判断和继承 W2 Policy 判定。

## 3. 提示词完成后如何启用

用户已确认新版提示词尚未完成，本轮不做真实测试。因此工厂不会将现有未完成的旧提示词静默配到新版 Schema。

在已对齐的新 W3 提示词目录，与 AGENTS.md 同级增加 `contract.json`：

```json
{"w3_contract_version":"persistent-runtime.w3.v5"}
```

这是宿主发布元数据，不作为额外 Skill 或提示词文件注入 Agent。Agent 仍只继承 AGENTS.md 和唯一 skills/skill.md。没有该标记的现有提示词包继续发布 v4。

生产工厂和本地回测均读取该标记。发布时冻结实际契约和两份 Schema hash；v5 的 Template/Case 兼容域包含这些 hash，与旧母线程隔离。旧 v4 的兼容 key 不额外追加空 hash，保持原有恢复能力；缺 contracts 的旧 Bundle 继续按既有 v4 默认解释。

后续应同批构建/发布 API、前端、Scheduler、Worker 和新版提示词包，再核验实际准入 Case 的冻结版本与 hash；已有在途 Case 继续使用自身冻结版本。本轮没有创建该标记文件，也没有提前宣称新版提示词已生效。

## 4. 历史“结果未记录”的修复工具

新增工具只读源 Runtime，更新 Read 列表/图/指标和原贡献。必须明确提供 1—100 个 Case ID，可分批、可重入，使用现有 projector 锁；不在 HTTP GET 中修复。

```powershell
.venv/Scripts/python.exe -m doxagent.v2_read.w3_outcome_backfill `
  --runtime-db <runtime.sqlite3> --read-db <read.sqlite3> `
  --case-id <case-id-1> --case-id <case-id-2>
```

默认为预览，核对后同一命令加 `--apply`。需先完成源回执的常规投影，使 Case、来源与业务准入证明已存在。工具使用原生内容解码器，不依赖可能被裁剪的 Read 研究大对象。

只有源库证明无其他产物、原始 NO_TRADE、operations COMMITTED、最终 OLD 且原契约无 HIT 的 Case 才派生 ARCHIVE。源库中的意图/分析决策/候选/归档/事实/Badcase/gap 都通过确切键或已有索引核对。Read 数据尚未到齐不等于没有产物；失败 operations 不变成成功。修复不向原 Runtime 写 ArchiveRecord，不重判历史 W2，不新增交易。

本轮仅对离线构造库验证此工具，未对生产 13 个成功/4 个失败样本执行回填。

## 5. 验证与证据

证据目录：`eval/w3_unified_direction_implementation_20261021/`。

- Python 汇总：223 项通过（3 条 warning），见 `final-tests.log`、`final-tests.xml`；覆盖新旧契约、W2 消费解耦、阶段恢复、归档回滚与幂等、WATCH、CLOSED、Template 隔离、研究工具、Read/图/指标/API、回测及相邻启动兼容回归。
- 前端全套 Vitest：67 项通过，见 `frontend-suite.log`。
- Schema 生成、TypeScript 检查及生产构建通过，见 `frontend-build.log`；保留原有构建包体提示，未扩展为无关打包重构。
- 1262/1559 两个宽度下新旧 Case、成功/失败后置状态共 6 项浏览器检查通过，见 `browser.log`；无页面异常及横向溢出。截图另存同目录与前端 test-results。
- 本次变更模块的 Ruff F 检查、Python 编译、`git diff --check` 通过。扩大到整个 Runtime 目录的额外 lint 扫描发现未修改的 `event_branch.py`/`resource_gc.py` 共 3 项既有 unused 告警，未纳入本次重构。
- 新版本代码使用离线 Fake Worker/模拟 SDK；没有真实模型评估、付费回测、券商调用或远端运行验收。新版提示词完成后，真实同批启用与生产自然流量验收仍是后续工作。

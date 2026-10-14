# W3 本地回测实现与第一批数据集回执

当前状态：工程实现、73条冻结数据集及首轮完整baseline已完成。73条全部产出合法交易判断，71条两阶段成功，2条运营校验失败。C盘启动批次因磁盘满失败，排除于模型评估；有效结果位于D盘。

## 已实现能力

新增独立 `python -m doxagent.w3_backtest` 入口，提供准备、build、run、resume、report、compare。复用当前 W3 的 Template/Fork、Trade Decision、Post-Decision Operations、输出 schema 和宿主事务；每个 case 为独立情景，历史生产 Thread 不复用，后一个回测 case 不继承前一个回测的新判断。

准备与回放分开：远端仅只读恢复 case/研究/历史意图并拉取 IBKR K 线；本地回放通过文件交付 Web/行情，关闭实时 Web、Data MCP、IBKR、子代理和生产服务配置，不启动投递器、Executor、Message Bus ACK、MAINTAIN 或投影。

原始 D1/D2/D3 保存，旧 Policy 顶层方向确定性继承至已有 Condition，并生成 revision/消费执行视图。当前 case 的旧 W3 结果及 Gold 不注入。历史其他 case 已产生的 W3 判断保留；不可恢复的当时成交状态为 UNKNOWN，后续状态不倒灌。RECENT_TRADE 只根据实际匹配身份构造。

本地业务时钟固定于各自 t0，模型/Worker 耗时使用真实时钟。完整 run 冻结 AGENTS 与唯一 Skill，记录输入、代码、提示词、模型、能力与选集身份；发生变化时不得借 resume 复用结果。Gold 单独重评不重跑模型。报告将第一阶段决策、宿主处置和第二阶段运营分别展示，保留实际模型选择、token/耗时和原生工具轨迹。

## 第一版数据

- 73 条：44 条既有评估、4 条指定补充、固定 seed 20261014 的25条随机一般 case；case 身份无重复。
- 62 条取得原 Worker started_at；10 条无原 W3，以原 W1/W2 完成路由的可恢复时间为截止点；NASA 采用明确标记的合成 t0。
- 已恢复原版本研究：8 个 D1/D2 handoff、21 个 D3 版本；不使用 current 研究替代旧版。
- NASA 使用 Bloomberg Law 公开可见正文部分与预截止 RKLB 研究，完成当前 qwen3.8-flash W1/W2 三个实际轮次，结果 NEW/无 Policy HIT。完整付费正文缺失不补写；Gold 留空。
- 73/73 行情文件 AVAILABLE，共96,433根10分钟 K线；每条验证ticker、20日范围及 bar_end <= cutoff，0条越界。日线由截断后的10分钟数据聚合。
- 从不可变历史意图恢复75个历史来源、223次72小时窗口成员引用。保留历史判断，去掉当前 daily_status 和成交状态。
- 44条 Gold 原评估全部保留：37条有明确动作（34 NO_TRADE、3 TRADE），7条条件文字保留且不强行评分；额外29条为空。
- Web 证据全部由显式 gpt-6-luna/max 任务准备，保存 SDK 模型读回、真实 Web Search 调用、来源、发布时间/更新时间筛选及缺口。采集已完成：73条精确Luna/max回执、540次真实搜索、395来源保留、2条截止后更新来源剔除。16个同日仅日精度来源和52个同日观察标为UNKNOWN；2来源检索回执UNVERIFIED，1来源为原消息SEED。采集阶段未保存完整工具面审计，不能证明Web为唯一可用或调用工具；此限制已入交接标记。

## 真实运行验证

`eval/w3_backtest/runs/smoke-current-20261014/` 包含三条真实 SDK 两阶段结果，全部 SUCCEEDED，运营全部提交；原生工具审计的 policy_blocks 均为0，真实读取了 Skill/研究/离线证据。

| Case | 模型 | 结果 | Gold对照 |
|---|---|---|---|
| BE-01-03 | gpt-6-luna / max | NO_TRADE | 符合明确NO_TRADE Gold |
| MU-02-02 | gpt-6-luna / max | NO_TRADE | 漏机会；保留新判断原因为复盘依据 |
| EXTRA-RKLB-SYNSPECTIVE | gpt-6-sol / medium | POLICY_TRADE | 用户未标注，仅展示结果 |

这是工程与输入交付验证，不以三条结果推断全量效果。开发中修正输入/代码后，早期 smoke run 不作为最终冻结 baseline；早期被阻断读取的诊断 run 也不作为有效评估。

52项 W3/回测/权限/生命周期/持久回执测试通过（含Gold保留与网页时间精度）；另12项 Document3 SDK Pilot 回归通过。新增模块及相关 SDK 修改 Ruff 通过，未改提示词两文件。

## 必须明确的运行边界

Windows SDK 的 read-only 进程策略会阻断 Get-Content。早期“合法输出”因此不足以证明 Agent 读取了提示词和证据，已从有效验收中排除。本地运行改为明确的 full-access OS sandbox，仍使用 case workspace 只读任务合同，禁用业务工具和服务凭据。SDK新增可选 sandbox_override；生产不传该参数，行为不变。这不是 OS 级网络隔离，不能用它宣称操作系统阻止了任何可能的外部请求。

SDK认证和私有运行状态在 `%LOCALAPPDATA%/DoxAgent/w3_backtest/`，不进入仓库。SDK env是合并而非替换父环境，因此显式覆盖桌面桥接及生产环境键，并关闭 Apps/Plugins/自动记忆。SDK公开计数未覆盖全部code-mode内部命令，逐case另存去重后的原生工具调用与回执，不保存模型隐藏推理。

现行退出窗口配置只保留绑定身份/30分钟退出设置，用历史时钟计算 holding_exit_at。旧 WATCH/PIN/消费不能完整恢复的部分继续标缺口；NASA与网页更新/时间不明的问题如实保留。本次不追加严格零未来函数证明、收益回测、29条自动标注或发布审批。

## 完整 baseline

有效批次：`D:/DoxAgentBacktest/runs/baseline-20261014/`，4并发。完整数据集输入指纹运行前后一致，实际模型分配为72条gpt-6-luna/max、1条gpt-6-sol/medium。报告包含实际运营提交回执，逐case保留决策、宿主处置、运营输出及工具轨迹。

| 指标 | 结果 |
|---|---:|
| 完整回放/合法交易判断 | 73/73 |
| 两阶段成功 | 71 |
| 运营校验失败 | 2 |
| 明确Gold匹配 | 33/37（89.19%） |
| 误交易 | 2/34 |
| 漏交易 | 2/3 |
| 条件Gold暂不评分 | 7 |
| 未标注 | 29（3 LONG、26 NO_TRADE） |

误交易：MU-01-05、MU-03-04；漏交易：BE-02-02、INTC-01-04。仅三个明确正样本，不从总体89.19%推断alpha捕获效果；当前正样本命中1/3。MU-02-02在最终冻结批次为EXPERT_TRADE LONG，与早期smoke不同；两批输入快照不同，不作为受控同输入稳定性比较。

最终交付：同目录 `report.md`、`results.jsonl`、`summary.json`、`final_audit.json`；每条case保留完整阶段产物。当前两条失败是现行业务校验冲突，未自动resume重跑以追求通过率。修复后应创建新run用于对照。

使用命令和文件合同见 `eval/w3_backtest/README.md`。重要变更已追加 changelog。本轮未git提交、push或部署生产。

## 满盘后的本地存储安排

C盘剩余空间为0。保留全部既有文件，不执行删除清理。有效批次位于 `D:/DoxAgentBacktest/runs/baseline-20261014/`，4并发；私有SDK状态在 `D:/DoxAgentBacktest/local_state/DoxAgent/w3_backtest/`，临时文件在 `D:/DoxAgentBacktest/tmp/`。输入仍只读使用C盘冻结数据集。

从原仓库根目录执行：

```powershell
.venv/Scripts/python.exe D:/DoxAgentBacktest/run_baseline.py resume
.venv/Scripts/python.exe D:/DoxAgentBacktest/run_baseline.py report
```

该启动脚本只设置本进程LOCALAPPDATA、TEMP/TMP及禁用Python字节码写入，然后调用标准回测CLI；源码、提示词、模型分配和数据内容均不因盘符变化而改变。后续新版本可采用同样环境设置，再调用标准CLI指定新的绝对run-id路径。

数据集当前73/73搜索与行情可用，73个Case合同、33份研究bundle均通过解析；第一批初始类型为72个OPEN、1个Policy HIT，没有RELATED样本。这个分布来自原上游判断，本轮不改写W1/W2以补覆盖。

## 运行中发现的现行编排冲突

EXTRA-INTC-OPENAI 第一阶段 NO_TRADE 已提交，运营阶段因 `w3_phase_output_invalid: novelty references must be present in the pinned Reference View` 失败。拒绝的是 E214；它来自原 W1 reference_ids，并已作为固定版本的 current Event Details 注入，但不在旧版 Reference View 中。即“case提供的可匹配Event”与“运营输出允许引用集合”不一致。保留该失败作为现行W3结果，不通过回测专用放宽校验或改提示词掩盖；第一阶段仍按合法模型决策统计，运营失败单列。


NOISE-17也出现同类冲突：输出引用E1、E6，其中E1来自W1及注入EventDetails但不在旧Reference View，E6可引用。两条case第一阶段均为NO_TRADE且已提交；失败只计入运营阶段。逐case `rejected_operations.json` 保存被拒绝的实际JSON与错误，不保存隐藏推理。本轮保留生产校验语义，不为基准单独放宽规则或修改提示词。

最终工程验证：52项W3/回测相关测试及12项Document3 SDK回归通过，报告补充运营回执后重跑16项回测测试通过；Ruff和diff空白检查通过。测试通过不代表全部case业务通过，2条运营失败如上单列。完整首轮运行未发出Web或子代理工具调用（原生工具轨迹复核），仍不宣称OS级物理网络隔离。

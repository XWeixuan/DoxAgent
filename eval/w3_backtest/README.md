> Gold更新（2026-10-21）：用户最终图片标注已补齐36条，现有73条全部可评分（7 LONG、4 SHORT、62 NO_TRADE）。下文首轮基线的37条评分/29条空白/7条条件结果是历史口径；最终标注出处与原Gold备份见 [录入回执](annotation/20261021/final_gold/README.md)。

# 本地 W3 历史情景回放

入口为 `python -m doxagent.w3_backtest`，从仓库根目录运行。此入口不接入生产 scheduler、HTTP Worker 或交易执行器。本地 SQLite 记录模拟意图/候选、WATCH、PIN、Delta 和阶段回执。

```powershell
.venv/Scripts/python.exe -m doxagent.w3_backtest build --dataset w3_history_20261014
.venv/Scripts/python.exe -m doxagent.w3_backtest run --dataset w3_history_20261014 --run-id baseline-01 --concurrency 4
.venv/Scripts/python.exe -m doxagent.w3_backtest resume --run-id baseline-01
.venv/Scripts/python.exe -m doxagent.w3_backtest report --run-id baseline-01
.venv/Scripts/python.exe -m doxagent.w3_backtest compare --before baseline-01 --after prompt-02
```

单条或选集：`run ... --case BE-01-01 --case EXTRA-RKLB-SYNSPECTIVE`。替换提示词：`run ... --prompt-root <包含 AGENTS.md 和 skills/skill.md 的目录>`。每个 run 冻结两份提示词。改提示词、代码、模型配置、输入或选集后使用新 run-id；同 run 的恢复复用已提交阶段，运营失败不重跑已提交交易判断。`report` 单独读取最新 Gold，不调用模型；`build` 保留已存在的人工 Gold，不覆盖后来补充的评估。

## 数据准备

```powershell
.venv/Scripts/python.exe -m doxagent.w3_backtest prepare-cases --dataset w3_history_20261014 --ssh-host doxagent-sg
.venv/Scripts/python.exe -m doxagent.w3_backtest prepare-nasa --dataset w3_history_20261014
.venv/Scripts/python.exe -m doxagent.w3_backtest prepare-search --dataset w3_history_20261014 --workers 4
.venv/Scripts/python.exe -m doxagent.w3_backtest prepare-prices --dataset w3_history_20261014 --ssh-host doxagent-sg
.venv/Scripts/python.exe -m doxagent.w3_backtest build --dataset w3_history_20261014
```

准备命令需要相关账号/远端权限；回放只需要本地文件和 Codex 模型认证。搜索由真实 `gpt-6-luna/max` SDK 任务执行并保留模型读回、调用、来源和时间审核。行情在准备阶段通过远端 Message Bus 容器的 IBKR 历史接口采集，回放期间不连 IBKR。`prepare-search`、`prepare-prices` 支持 `--case` 选集；`prepare-cases` 构造固定 seed 20261014 的整份清单。

第一版包含 44 个既有评估 case、4 个指定补充 case、25 个同一股票范围内随机一般 case，共 73 条。NASA 无原 case，以公开可见文章部分与预截止研究启动真实 W1/W2；登录墙缺失正文明确标为 PARTIAL。

## 文件合同

数据集目录 `datasets/w3_history_20261014/`：

- `manifest.json`、`cases.jsonl`：清单、分组、准备缺口、输入指纹。
- `inputs/<case>/case.json`：消息、截止点、旧 W1/W2 与版本绑定；当前 case 的旧 W3 结果不注入。
- `inputs/<case>/historical_state.json`：截止前 72 小时意图、已确认命中身份、当时 Event Details、可恢复的 WATCH/PIN/消费信息。无法恢复的状态有缺口记录；当前成交状态不倒灌。
- `inputs/<case>/evidence/index.json`、`web_search.jsonl`、`sources/*.md`、`price_10m.json`、`price_daily.json`：离线证据。跨截止点的未完成 10 分钟 K 线剔除；日线只由已截断的 10 分钟数据聚合。
- `research/<key>/`：按原版本恢复的 D1/D2/D3、Reference View、Event Details，旧版原文和确定性执行视图分开保存。
- `research/execution_window.json`：本轮采用的现行退出窗口配置。仅保留绑定身份和退出分钟数，不带账户权限；用历史截止时钟计算持有窗口。
- `gold.jsonl`、`gold_review.md`：44 条原结论和定位，29 条留空。37 条有明确动作 Gold，7 条保留条件文字不强行评分。
- `preparation/`：远端原始导出、旧结果、采集回执、时间审核和行情缓存。这些文件不复制给回放 Agent。

Run 目录 `runs/<run-id>/` 包含版本/能力/指纹回执、`prompt_snapshot/`、逐 Case SQLite/Workspace、第一阶段决策、第二阶段运营、宿主模拟受理、SDK 模型选择/耗时/token、原生工具调用轨迹、汇总与 Markdown 报告。原生工具轨迹补充 SDK 对 code-mode 内部 shell 调用计数不完整的问题。

## 执行边界

复用当前 W3 的 Template/Fork、交易判断、后置运营、schema 校验与宿主提交方法。普通 case 为 Luna/max，Policy HIT 为 Sol/medium。生产入口规则仅作为观察字段；历史选集允许直接回放，即使新版生产规则不会再次路由到 W3。

SDK 使用独立 CODEX_HOME，仅复制当前用户认证，禁用 Web Search、生产 MCP、Apps、Plugins、自动记忆和子代理，剥离父桌面会话桥接与生产服务环境。认证及 SDK 私有状态保存在 `%LOCALAPPDATA%/DoxAgent/w3_backtest/`，不写入仓库。

Windows 的 read-only 进程策略会拦截 `Get-Content`，因此本地 SDK 使用 full-access OS sandbox 以执行文件读取，任务仍要求只读 case workspace。此模式不是操作系统级网络隔离；Web/IBKR 隔离依靠已关闭的工具能力、无生产 MCP 和移除服务配置。生产默认 sandbox 与权限保持原行为。

回放保留研究版本不完整、旧 WATCH/消费不可恢复、网页时间不明/更新和 NASA 正文不全等证据缺口。它用于情景评估，不宣称严格零未来函数，不把未知补成否定事实，也不根据收益生成 Gold。

报告以模型第一阶段判断评分；宿主拒绝不算模型 NO_TRADE。未标注样本只报交易比例；运营成功与失败单列。版本比较只配对相同数据输入。

生成的 `runs/*/cases/*/worker/` 工作区留在本地并由 Git 忽略；第一版输入、Gold、报告仍保留。整个 `eval/w3_backtest/` 不进入 Docker 构建上下文。

## 已归档基线与源码同步

首轮冻结基线已完成：73 条均产生交易判断，71 条完成两阶段；另外两条运营阶段因旧 Reference View 缺少 Event ID 被现行校验拒绝，保留实际失败。明确 Gold 37 条中 33 条相符，其余 7 条条件文字、29 条未标注不计入准确率。

本仓库保存两个冻结数据集的输入、版本研究、Gold、来源时间审核和 `reports/baseline-20261014/` 的报告/汇总/最终审计。SDK 私有状态、逐案运行数据库、重复原始远端导出与准备阶段原始模型回执继续留在原本机位置。重新回放只需已冻结输入；重新准备需要远端与模型账号权限。原始完整 run 仍在 `D:/DoxAgentBacktest/runs/baseline-20261014/`，这里的报告归档不作为可 resume 的运行目录。

首次重跑先检查可用空间；建议用绝对 `--run-id` 指向充足空间，并为当前进程设置 `LOCALAPPDATA`、`TEMP/TMP`，不覆盖原 baseline。当前生产默认能力保持不变；MAIN 的维护 MCP 60 秒/W3 90 秒预算和 writer 所有权修复均保留。

## 最新提示词离线评测（2026-10-21发起）

最新提示词的 `contract.json` 与 AGENTS.md、skills/skill.md 一起冻结；它只选择输出schema，不启用生产研究MCP。离线runner不声明生产的w3-entry-v5能力入口，继续从文件读取旧研究、预采集Web和K线，因此可以使用现行LONG/SHORT/NO_TRADE结构而不依赖新增MCP或行情快照。任务环境明确以预采集证据替代网络，不修改被测提示词。

本轮run为 `D:/DoxAgentBacktest/runs/latest-prompt-20261021/`，启动脚本 `D:/DoxAgentBacktest/run_prompt_20261021.py`。完整Gold快照位于run根目录，仅供宿主评分，不复制给模型。普通case依现行选择器为gpt-6-luna/max；Policy相关case依现行选择器为gpt-6.1-sol/medium，以实际model_selection回执为准。

```powershell
.venv/Scripts/python.exe D:/DoxAgentBacktest/run_prompt_20261021.py resume
.venv/Scripts/python.exe D:/DoxAgentBacktest/run_prompt_20261021.py report
```

上述命令面向该次已冻结运行；其他新版提示词应创建不同的run-id及独立快照，勿覆盖本轮快照或旧基线。

本轮已完成：73/73两阶段成功，完整Gold匹配61/73（83.56%），误交易4、漏交易8、方向错误0。11个交易正样本命中3个；旧基线同Gold为63/73，本轮下降2.74个百分点。详情见 [完整评测回执](reports/latest-prompt-20261021/evaluation_receipt.md)。

## 模型对照

本地入口可用 `run --model gpt-6.1-sol --effort medium --case <key> ...` 覆盖所选Case的Template/决策/运营模型；覆盖进入run与Case身份，resume保持原配置。省略参数仍沿用现行Case模型选择，不影响生产。

2026-10-22对上一轮12个错误Case完成同提示词/输入/Gold的Sol medium重测：纠正4个，误交易4→1、漏交易8→7，12条两阶段均成功。详见[评测回执](reports/sol61-medium-20261022/evaluation_receipt.md)。这不是全量73条的Sol准确率。

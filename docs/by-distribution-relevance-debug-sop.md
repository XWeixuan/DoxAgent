# By Distribution 逐 ticker 调试 SOP

本流程由 Codex 阅读坏例、修改候选并执行脚本；脚本只是抽样、回放、评分、对比和记账工具，不负责自动调参，也不控制验证集权限。验证时机由本文约定。

## 1. 口径与轮数

|板块|每 ticker 调试/验证条数|常规候选最多|额外过拟合修正|
|---|---:|---:|---:|
|正则|100 / 100|5|最多 1|
|Decision Model|50 / 50|3|最多 1|

- 单位是文章×实际目标 ticker。`INSUFFICIENT` 保留但不计入二分类 P/R。没有正预测时 P 未定义，没有相关 GOLD 时 R 未定义，不能据此宣称达标。
- 过线：**P > 80% 且 R > 80%**，严格大于；不是 F1、平均值或任意一项超过。数据、GOLD、阈值 0.5 和来源上下文在该次调试中保持固定。
- 过拟合由 Codex 结合指标和坏例判断，不额外设分差门槛。脚本显示 P/R 下降幅度，并对“调试两项过线而验证未过线”给出检查提示；提示不是自动确诊，也不限制 Codex 对其他明显过拟合情形的判断。
- 调试最佳：调试 `(P+R)/2` 最高，平手优先较高的 `min(P,R)`，再优先较早 attempt。
- 最终最佳：所有候选都各有一次完整调试/验证结果后，选 **(调试P＋调试R＋验证P＋验证R)/4** 最高者；平手比较四项最小值，再比较 attempt 名称。未达标时明确交付“最佳但未达标”。
- 验证用于最终版本选择，因此该成绩不是独立、无偏的最终验收估计。小样本也不能保证长期生产始终达到 80%。

## 2. 固定数据和配置

项目根目录 PowerShell：

```powershell
$py = '.venv/Scripts/python.exe'
$tool = 'scripts/distribution_relevance_debug.py'
$data = 'eval/distribution_relevance_gold_v2_20261028/sample1800'
$work = 'eval/distribution_relevance_debug_20261028'
$modelData = "$work/decision50"
$run = "$work/runs/MU-20261028-01"
New-Item -ItemType Directory -Force $work | Out-Null
```

正则使用原来的 `development-gold.jsonl`、`validation-gold.jsonl`，每 ticker 各 100。模型从这两集再次按 ticker 均匀无放回抽样，各 50，不按 GOLD、原判定结果或正文长度挑选：

```powershell
& $py $tool sample --dataset $data --output $modelData --count 50 --seed 2026102804
```

本轮已生成该模型子集，两集各 450、合计 900；直接复用。以后换数据时使用新目录及 manifest，不反复重抽直到结果好看。准备验证文件不等于拿它做测试，不向调参上下文展示其内容。

读取真实生产当前词表和绑定语言，不构造有写入副作用的业务 Repository。以下只读导出不带来源参数、API key：

```powershell
Get-Content -Raw $tool | ssh doxagent-sg 'sudo docker exec -i doxagent-v2-v2-message-bus-1 python - export-context --db /data/bus/bus.sqlite3 --output -' | Set-Content -Encoding utf8 "$work/context.json"
```

也可对本地已有 Bus 数据库运行 `export-context --db 路径 --output context.json`。固定这份快照；绑定或运行代码发生变化时，另开调试目录或完整重跑可比候选。所需语言来自实际启用的 by_search/by_distribution 绑定，L1 不属于本轮修改范围。

建立首个候选文件，随后由 Codex 编辑：

```powershell
& $py $tool candidate --context "$work/context.json" --ticker MU --output "$run/regex/attempt-01/candidate.json"
& $py $tool candidate --context "$work/context.json" --ticker MU --output "$run/decision/attempt-01/candidate.json"
```

正则只改 `l2`，模型只改 `definition.relevant/irrelevant`；保留其他字段。语言按实际绑定，不照搬旧作者资料的固定三语要求。

## 3. 常规调试：只使用 development

先阅读共享经验、GOLD v2 规则、调试集，以及当前生产配置。需要生产实际判定对照时，`--history` 可接上轮 `final/full-day-archive.jsonl`，只展示匹配输入版本的历史决策，并输出 `historical_badcases.jsonl`；未执行或版本不匹配不是负例。也可先将当前生产原配置在调试集做一次 `baseline` 回放，作为参考，不称它为已修改的候选。

每个 ticker 的正则按以下顺序执行，最多 5 个常规候选；模型同样执行，最多 3 个：

1. 仔细读 FP/FN 的完整冻结输入、GOLD 理由和当前规则。定位漏判、子串、消歧、跨字段凑词、邻近市场范围或过严条件等机制。不要只看标题或自动抄模型结果。
2. 设计能解释一类坏例的修正。必要时重新组织规则，而不是给每篇新闻加特判。写清本轮假设，保留上一版文件；不要修改 GOLD 或补写正文。
3. 回放当前候选，读取指标和 `badcases.jsonl`。结果有 `errors` 时先解决输入/服务问题，重试同一目录；成功的模型回答会复用，只重试失败项，不能不断重抽成功回答挑高分。
4. **每轮测试后更新一次共享经验**，再决定停止或写下一轮。达到 P/R 目标则停止本板块常规开发，否则直到轮数上限。

正则回放使用正式 `regex_relevant`，同时导出命中组、实际命中子串和邻近文本：

```powershell
& $py $tool evaluate --track regex --ticker MU --dataset "$data/development-gold.jsonl" --terms "$run/regex/attempt-01/candidate.json" --context "$work/context.json" --output "$run/regex/attempt-01/development"
```

模型使用正式 `JevClient` 与生产 `_jev` 的切段、64K 上限、重试及聚合逻辑，独立测试全部 50 条，不以正则未命中作为前提。只传文章状态与候选 definition，不传 GOLD、理由或历史判定：

```powershell
& $py $tool evaluate --track decision --ticker MU --dataset "$modelData/development-gold.jsonl" --terms "$run/decision/attempt-01/candidate.json" --context "$work/context.json" --output "$run/decision/attempt-01/development" --env-file .env --concurrency 4
```

模型需要已配置的 `DASHSCOPE_API_KEY`；可通过现有环境或 `--env-file` 提供，环境变量优先。不要打印、复制进结果文件或向用户索取本来已有的密钥。

查看调试最佳，默认**不会读取验证指标**：

```powershell
& $py $tool compare --root "$run/regex"
& $py $tool compare --root "$run/decision"
```

每个输出目录保留 `candidate.json`、`metadata.json`、`predictions.jsonl`、`metrics.json`、`details.jsonl`、`badcases.jsonl`。预测日志支持中断后续跑；完整评分不把错误当负例，存在错误的候选不能作为完整结果择优。

每轮将通用经验合并写入一个临时文本片段，例如 `$run/lesson.txt`，然后更新唯一共享 MD 中本 ticker 的同一条记录：

```powershell
& $py $tool note --root $run --ticker MU --track regex --session MU-20261028-01 --text-file "$run/lesson.txt"
```

`--root` 指向同时包含 `regex/`、`decision/` 的 ticker 目录；两块更新使用同一个 session。下一轮修改文本片段再运行，更新而不新增重复复盘。不要往共享经验写具体行业词表、新闻答案或验证坏例。

## 4. 常规候选冻结后，才运行验证

**同一 ticker 尽量先完成正则和模型两个板块的常规开发，再看任一验证结果。** 模型验证 50 条是正则验证 100 条的子集，先看正则验证再调模型会泄漏答案。若只调一个板块，也不能在以后把已看过的子集宣称为“未见验证”。

1. 停止常规编辑，保留所有候选；用调试集均分选各板块最佳候选。
2. 只对该最佳候选运行一次验证，把命令中的 dataset 改为对应 `validation-gold.jsonl`，output 改为该 attempt 的 `validation`。候选文件和 context 不变。
3. 若结合坏例确认出现过拟合，允许**额外一个**候选：分析这次反馈，做通用修正，不抄验证文章关键词。新目录取下一编号并加 `-overfit`；例如常规正则已有 5 轮则用 `attempt-06-overfit`，模型已有 3 轮则用 `attempt-04-overfit`。该候选都跑 development 和 validation；不再开启第二次验证反馈调参循环。
4. 在额外候选冻结或确定不需要额外候选后，把其余所有已有 attempt 的验证补跑齐。已经成功跑过的结果复用，不为抬分重抽模型回答。**不是给每个 attempt 分别一次额外机会。**
5. 所有 attempt 都有完整的两集结果后再做最终择优：

```powershell
& $py $tool compare --root "$run/regex" --with-validation
& $py $tool compare --root "$run/decision" --with-validation
& $py $tool note --root $run --ticker MU --track decision --session MU-20261028-01 --text-file "$run/lesson.txt" --with-validation
```

`final_best` 仅在目录内全部候选都有完整成对结果时给出；报告含四项均分、各项 P/R 和过拟合信号。它是汇总提示，不是验证访问控制。SOP 轮数与验证纪律由执行 Codex 遵守。

## 5. 收尾

交付每 ticker/板块的所有候选、两集指标、最佳候选、是否达到目标、额外修正是否使用，以及共享 MD 中合并后的通用复盘。具体坏例留在运行目录。

当前 1,800 条 GOLD 的部分来源/语言字段由来源 ID 和冻结快照重建，不是逐字节复刻历史模型请求；多源消息的正则按各来源语言做 OR 回放。不要把这种离线回放直接称为生产历史实绩。原数据的 CEG/SNOW 验证日期例外与历史留存限制继续有效。

评估器没有生产写入路径，也不自动发布最佳候选。正式使用时通过已有监测词校验/版本更新流程处理，保留最新 expected_revision。本轮交付仅搭建辅助工具、模型子集和 SOP。

## 本轮工具自检

11 项离线回归及 Ruff 检查通过，覆盖正式正则回放与命中解释、模型真实客户端的模拟 HTTP 调用、失败项续跑、输入上限、动态语言、历史输入版本匹配、无提前验证读取的调试汇总、成对择优和共享记录合并。

另用生产当前 MU 第 5 版配置回放 100 条真实调试消息，TP/FP/FN/TN 为 25/10/7/58，P 71.43%、R 78.13%，执行错误 0；这里只验证工具可用，没有修改规则或开始正式 attempt。真实验证集未做评分，真实 Decision Model 请求为 0。数据配额、父集包含关系、事件组隔离及绑定语言核验见 [回执](../eval/distribution_relevance_debug_20261028/verification.json)。

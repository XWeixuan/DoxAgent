# W3 提示词回测操作交接说明

更新：2026-11-23。适用仓库：`C:\Users\WEIXUANXIE\Desktop\DoxAgent`。

本文交接的是一项完整工作：收到一份新的 W3 提示词后，使用已有历史情景测试集执行真实模型调用，处理技术失败，与相同口径的现行基线和 GOLD 比较，并交付能指导提示词优化的分析报告。本文不登记历次提示词版本、固定 Case 数量或某次成绩；这些以对应实验的冻结资产为准。

## 1. 接手后先确认这几件事

1. **任务类型**：通常是离线历史情景回测。不要因提示词要求 Web Search 就开启真实搜索，也不要改用“当前时点的真实能力评估”。两者是不同实验。
2. **新提示词布局**：统一文件按 `AGENTS.md` 注入；双文件按 `AGENTS.md + skills/skill.md` 注入。保留用户提供文件的原始字节，不替用户润色、合并旧规则或补写交易逻辑。
3. **测试范围**：读取现行 Catalog 和 manifest，按 `decision / timeliness × dev / validation` 列出实际成员。“全量”覆盖约定范围内所有有效成员，各集合分别评分。不要根据某次旧报告中的数字硬编码数量。
4. **基线身份**：核对提示词、输入、GOLD、模型、运行时、时间感知、服务档位及多轮来源。生产生效版本、本地最新文件和实验分支可能不同；不能按修改时间选择基线。
5. **默认调用口径**：未另行指定时沿用当前普通对比的 `gpt-6-luna / max / default`，Fast 关闭，`trade_mode=single`，时间感知与选定基线一致。CLI 应显式传模型与服务档位，不能依赖生产路由或默认 Fast。
6. **费用与重试**：调用是真实付费/订阅模型调用。成功样本保留；仅补测技术失败或经证据确认无效的样本。判断与 GOLD 不同是有效结果，不是重试理由。

用户只给一份新提示词时，以上日常选择应根据现有资产自行完成，无须逐项请求确认。确实无法确定模型、基线或入选口径，且会改变实验含义时，再提出具体问题。

## 2. 权威资产与阅读顺序

当前正式数据目录为 [w3_reviewed_20261121](C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/w3_backtest/datasets/w3_reviewed_20261121)。日期是目录身份，不表示成员以后不再增加。

| 资产 | 用途与优先级 |
|---|---|
| [catalog.jsonl](C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/w3_backtest/datasets/w3_reviewed_20261121/catalog.jsonl) | 唯一机器目录；确定身份、集合、事件 GOLD、置信度、传导类型、标签。 |
| [Case Catalog.md](<C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/w3_backtest/datasets/w3_reviewed_20261121/Case Catalog.md>) | 机器目录的人工可读投影，便于了解全貌。 |
| [Case Gold Review.md](<C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/w3_backtest/datasets/w3_reviewed_20261121/Case Gold Review.md>) | 逐 Case 背景、同维度 Prior、GOLD 依据、历史误判、时效及边界说明。报告分析必须读相关条目。正文中的历史 GOLD/历史 W3 不是第二套现行答案。 |
| [manifest.json](C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/w3_backtest/datasets/w3_reviewed_20261121/manifest.json) | 数据集契约、输入路径、成员及准备状态；应与 Catalog 身份一致。 |
| [Excluded Cases.md](<C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/w3_backtest/datasets/w3_reviewed_20261121/Excluded Cases.md>) | 排除记录；舍弃不等于 NO_TRADE，不评分、不因旧目录仍存在而重新纳入。 |
| `inputs/<case_key>/`、`research/<research_key>/` | 实际消息、可恢复上下文、历史研究、模拟搜索与行情；执行时读取冻结副本。 |
| `preparation/` | 原始导出、评估底稿、构造过程和审计。仅供宿主分析，不交给被测 W3。 |
| [测试集构造规范手册](C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/W3测试集构造规范手册.md) | 只在增补或修复 Case 时使用；顶部新修订优先于后文历史规定。 |

旧 README、旧方案及旧目录中的数量可能已经过时。以本次冻结的 Catalog、manifest 和实际输入为准；若两者身份不一致，先解决这项具体问题，不能悄悄跳过条目。

查找基线可从 [既有基线索引](C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/w3_backtest/reports/baseline_refresh_20261121/README.md) 进入，再看实验的 `experiment.json / provenance.json / baseline.json / *_validated_results.jsonl`。索引是定位线索；若之后已有扩展基线，应按更新后的来源关系选择，不把本链接当成永久最新指针。

## 3. GOLD 与集合的业务含义

### 3.1 身份和属性

- `case_id` 是原生产 Case 身份；`case_key` 是输入目录兼容别名。两者都要保留；CLI `--case` 支持目录别名及 Catalog 中的原生 ID。
- `suite=decision` 测普通交易判断；`suite=timeliness` 测有价值事件在特定回放时点的时效判断。分别统计，不能混成一个总准确率。
- `split=dev|validation` 是样本使用边界。未真正封存的已审查材料不能改称验证集；空验证集如实报告，不临时挪样本凑数。
- `gold=LONG|SHORT|NO_TRADE` 是事件 GOLD。`gold_confidence=A|B` 是 GOLD 置信度，**B 不自动等于边缘**。
- `transmission=direct|indirect|industry` 是经济传导属性，**industry 不是标签或 NO_TRADE 的同义词**。
- `case_source` 描述历史来源类型，如历史误交易、漏交易；不是本次模型输出或新的预期答案。
- 边缘样本只按 `tags` 中的 `edge_case` 分组。其他误判特征标签和置信度可以辅助解释，不替代这个定义。

### 3.2 当次预期决策在宿主侧派生

当前代码 [catalog.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/w3_backtest/catalog.py) 定义：

```python
expected = "NO_TRADE" if "timeliness_nontradable" in row["tags"] else row["gold"]
```

因此，一条时效 Case 可以保留事件 GOLD=LONG，同时因为已过期而在本次回放应判 NO_TRADE。不能为了评分方便把其事件 GOLD 改成 NO_TRADE。时效集也支持不带过期标签的新鲜正例；应从当前 Catalog 计算实际构成。

Catalog、GOLD、标签、评估底稿、历史 W3 决策及收益不进入模型输入或检索资料。模型根据中性消息与材料自主判断。用户已经核实并确认的时效结论不因模型反对而重标；若发现真实资产错误，记录具体证据并单独处理。

## 4. 当前回测实际怎样工作

### 4.1 调用链与产物边界

```text
冻结数据集 + 本轮提示词包 + 明确模型/运行时配置
  → CLI 选择 Catalog 成员并记录运行身份
  → DecisionReplayRunner 初始化本地 Worker、SQLite、线程注册表
  → 按提示词与执行 profile 初始化规则母线程（ready=true）
  → 每个 Case 独立 fork
  → 生产 v6 上下文投影 + Research Assets 绑定 + 固定时点行情
  → 真实模型执行一次 trade_decision
  → 校验 schema，保存四字段及调用回执，RECORDED_ONLY
  → 宿主评分、对照基线、撰写报告
```

母线程读公共规则，不研究某条消息；不同 Case 不串成一个长期业务会话。模型 profile 不同会产生不同模板。每次独立实验/独立复测轮使用新的运行目录和身份；不要用 `resume` 充当额外一轮复测。

新执行只支持 `w3-backtest.dataset.v2` 和 `persistent-runtime.w3.v6`。代码中仍有旧二阶段适配类及旧评分兼容逻辑，它们不代表本次应运行 Operations。

| 代码入口 | 接手时应了解什么 |
|---|---|
| [cli.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/w3_backtest/cli.py) | `run / resume / report / compare`、冻结身份、并发、成功跳过、筛选。 |
| [decision_runner.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/w3_backtest/decision_runner.py) | 母线程、Case fork、单阶段执行、回执。 |
| [context.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/w3_backtest/context.py) | 复用生产 `base_trade_input / trade_input`，可选上下文、模型覆盖及行情校验。 |
| [runner.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/w3_backtest/runner.py) | 本地隔离 Worker、Research MCP、私有状态目录、运行时依赖和原生工具轨迹。 |
| [report.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/w3_backtest/report.py) | 规范化决策、评分、原始运行报告、集合分开统计。 |
| [生产 W3 引擎](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/w3.py)、[v6 投影](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/w3_v6.py) | 回测复用的实际模型调用及输入契约；提示词重测不另造一套业务决策器。 |

### 4.2 输入与能力

每个 Case 通常含 `case.json`、可选 `context.json`、`evidence/index.json`、`web_search.jsonl`、`sources/*.md`、`market_view.json`，另引用历史 D1/D2。`context.json` 可以恢复 W1/W2、关联对象及已有冻结业务上下文。**字段缺失与明确空值不同**；部分 Case 缺资产是允许的，不补造 Policy、PIN、WATCH 或交易历史，不把今天的生产状态倒灌进历史。

回测会使用真实 Research Assets MCP 的 `catalog / search / read / market_view` 读取准备好的资料，不能笼统写成“无 MCP”。v6 研究范围保留 D1/D2，不把 D3/Policy 全集重新开放为研究目录；可恢复的关联 Policy 信息走业务输入。但它关闭真实 Web Search、Data MCP、IBKR、账户和交易权限、子agent、生产写操作；模拟搜索与真实历史行情均提前落盘。模型指令要求搜索时，它应使用离线可见材料完成研判。

模型公共输出为：

```json
{
  "decision": "LONG | SHORT | NO_TRADE",
  "reason": "公开决策理由",
  "prior_expectation": "同维度原预期，或允许时为 null",
  "expectation_delta": "相对原预期的净增量，或允许时为 null"
}
```

`LONG/SHORT` 必须有非空 Prior 和 Delta；`NO_TRADE` 可为 null。不要替模型补齐本应由其提供的输出。宿主只记录，不生成交易意图、不发单、不运行 Operations，不修改生产 Case 或其他生产状态。

### 4.3 两种时间

- **业务时钟**：每条 Case 的 `as_of`，投影为 `input.cutoff_at`。模型假定自己处于这个历史时点，新闻与行情只包含此前可用内容。推理花几分钟不会推进业务时间。
- **运行时钟**：本次执行真实 UTC 起点与 Elapsed，供耗时和软提醒使用。公共母线程就绪后，在单 Case 研究准备和 fork 前起算；不把母线程初始化算成每条 Case 的研判时间。

原消息发布时间、历史 Case 时点、历史采集/启动时间需要留档，但不相互替代。仅日期消息的数据库占位时分不能当真；这种 Case 的时钟按构造规范和已确认资产执行，普通提示词重测不要重定时间。

`market_view` 与 Case ticker、`observed_at=as_of` 必须一致。日线、小时线、细粒度线只保留截止前已完成 bar；不带 forming bar，不补造价格。缺失行情允许明确缺失，时间错位或未来已完成 bar 是数据错误。

## 5. 实验准备：冻结什么、提示词怎样放

### 5.1 建立一个独立实验目录

建议沿用现有普通文件，不新增任务平台：

```text
eval/w3_backtest/runs/<独立实验名>/
  experiment.json                 # 模型、基线、来源、运行时/镜像、哈希、并发
  dataset_snapshot/               # 本次 Catalog、Review、manifest、inputs、research
  execution_source/src/           # 实际执行代码副本
  prompts/                        # 本轮提示词包
  decision/                       # 普通集运行产物；不同 split/轮次另建目录
  timeliness/                     # 时效集运行产物
  decision_retry_01/               # 可选：仅无效样本的独立补测
  *_validated_results.jsonl        # 每 Case 本轮唯一选定有效输出及来源
  comparison.json                 # 按相同口径重算的指标与逐条对照
  case_matrix.md                  # Gold/当次预期/基线各轮/新结果/耗时
  public_reasons.md               # 重点 Case 四字段原文和 thread 路径
  evaluation.md                   # 面向用户的分析报告
  acceptance.json                 # 覆盖、身份、技术失败及隔离核对
```

这些是建议命名，不是要再开发一个框架。CLI 会冻结提示词和 Catalog，但**不会替你复制完整数据集或整个源代码**。若直接使用还会更新的目录，后续补测与重算可能不再是同一实验。

优先从已完成的可比快照复制；使用现行目录时，检查复制前后输入摘要及成员一致即可，无须要求其他开发停止。不要把半写入的 Catalog 和另一个时刻的 manifest 拼在一起。

`input_fingerprint()` 当前只摘要 `inputs/` 与 `research/`；Catalog/GOLD、manifest、提示词、代码、运行时还要分别记录哈希。`git HEAD` 不能代表未提交代码，冻结实际执行源码更可靠。命名和操作系统路径只是索引，不是版本证明。

### 5.2 提示词包

统一文件：

```text
prompts/
  AGENTS.md             # 用户文件原字节复制
  backtest.md           # 复用选定基线的回测情景规则
  contract.json         # {"w3_contract_version":"persistent-runtime.w3.v6"}
```

双文件再增加 `skills/skill.md`。离线 CLI 不自动把根目录 `skill.md` 改成这个路径，准备时需要正确放置。统一文件模式下不要留下旧 Skill，也不要制造空 Skill 来假装双文件。

`backtest.md` 是独立的中文情景规则，说明固定历史时间、离线证据与价格的使用、无需因未提供业务机制而补造状态等约束。对比双方应使用相同回测规则；如果仅因统一文件布局要修改规则中的文件名描述，记录这项变化，不能顺便改交易标准。Operations 文件不属于本次第一阶段实验。

它要求模型将准备好的披露视作该时点正常可检索、可信的材料，直接信任已提供的历史价格，不因本地文件、无法实时打开页面或缺少网页存档而额外收紧交易门槛；报道中的传闻、预测和附条件承诺仍保持原有商业含义。这个情景假设不是对全球信息完整性或绝对无未来函数的证明。若规则文字与实际上下文发生实质冲突，先说明具体冲突，不能静默改规则后仍称为纯业务提示词对比。

复制后比对源文件和 `AGENTS.md` 的 SHA256；也比对候选之间的内容。曾出现附件导出与文件名不一致，**同名字未必同内容，不同名字也未必不同内容**。若证实只是复测，应如实更正实验说明/身份映射并保留原产物，不改写原决策。

### 5.3 准备示例（PowerShell，只在新的实验目录运行）

以下 `<...>` 均须替换。`$baselinePack` 指经核实的基线提示词快照；不默认使用仓库中正在编辑的生产提示词目录。

```powershell
$repo = 'C:\Users\WEIXUANXIE\Desktop\DoxAgent'
$dataset = Join-Path $repo 'eval/w3_backtest/datasets/w3_reviewed_20261121'
$experimentId = '<新实验名>'
$experiment = Join-Path $repo "eval/w3_backtest/runs/$experimentId"
$promptInput = 'C:\Users\WEIXUANXIE\Downloads\<新统一提示词>.md'
$baselinePack = '<已核实的基线 prompt_snapshot 或 prompts 绝对路径>'

# ErrorAction Stop 防止误把新实验写入已有目录。
New-Item -ItemType Directory -Path $experiment -ErrorAction Stop | Out-Null
$snapshot = New-Item -ItemType Directory -Path "$experiment/dataset_snapshot"
foreach ($name in @('manifest.json','catalog.jsonl','Case Catalog.md',
                    'Case Gold Review.md','Excluded Cases.md','inputs','research')) {
    $source = Join-Path $dataset $name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination $snapshot.FullName -Recurse
    }
}
New-Item -ItemType Directory -Path "$experiment/execution_source" | Out-Null
Copy-Item -LiteralPath "$repo/src" -Destination "$experiment/execution_source" -Recurse
New-Item -ItemType Directory -Path "$experiment/prompts" | Out-Null
Copy-Item -LiteralPath $promptInput -Destination "$experiment/prompts/AGENTS.md"
foreach ($name in @('backtest.md','contract.json')) {
    Copy-Item -LiteralPath (Join-Path $baselinePack $name) -Destination "$experiment/prompts/$name"
}
if ((Get-FileHash -LiteralPath $promptInput).Hash -ne
    (Get-FileHash -LiteralPath "$experiment/prompts/AGENTS.md").Hash) {
    throw '提示词复制后字节不一致'
}
```

双文件任务将 AGENTS 和 Skill 分别原字节复制到规定路径。记录 `experiment.json`，至少含：数据来源及 Catalog 哈希、输入指纹、提示词逐文件哈希、源码摘要、基线位置/复用来源、模型/effort/tier、时间感知、runtime/companion 哈希、镜像 ID、各集合成员、每轮并发及总并发。

## 6. 执行方式与本地运行环境

### 6.1 运行前检查

检查 Catalog 与 manifest 的成员身份一一对应，输入路径存在、时钟带时区，行情通过现有 `validate_market`。按四个集合打印数量和 GOLD 分布。已有准备完成的 Case 直接复用，无需重新采集远端、重做搜索或重跑 W1/W2。

`build --dataset <副本>` 会重建部分证据索引和准备摘要，是构造/修复流程中的写操作；普通提示词重测不要机械地对权威源目录执行。`prepare-curated` 是带历史导入语义的入口，也不是当前正式集的通用“初始化”按钮。

### 6.2 时间感知运行时

Windows 宿主当前常用本地 Docker/Linux 执行；已有时间感知补丁产物是 Linux ELF，不能交给 Windows `.venv` 直接运行。使用与基线一致的应用依赖镜像、Codex 原生可执行文件及同目录 `codex-code-mode-host`。这不是部署生产服务，无须 build 或重启生产。

从所选基线的 `experiment.json / launch.py / run_manifest.json` 核实镜像和运行时的真实位置，再检查本地仍可用。不要仅复制旧 launcher：它可能写死旧成员数量、提示词名、时间原点验证办法和输出路径。

传 `--time-awareness` 并配置 `DOXAGENT_CODEX_RUNTIME_BIN` 会检查平台、可执行性、companion 并记录摘要；**这不独立证明该二进制含原生 Elapsed/软提醒补丁，也不证明每个请求已注入**。保存运行时出处，必要时抽查请求/原生记录。较新官方 SDK/CLI 不自动继承本项目自定义补丁；升级运行时必须披露与基线的差异。

回测私有 Worker 只继承所需身份，关闭生产环境变量、外部应用/插件/记忆/子agent等能力。Docker 挂载只包含评估资产、执行源码、所需身份、原生运行时及独立状态目录；不要挂生产 `.env`、生产数据库或交易凭据。保留既有 launcher 中经验证的系统技能禁用方式，避免额外目录指令影响被测 W3。

### 6.3 可直接采用的 CLI / Docker 模板

下面模板沿用现有镜像的 `/app/.venv/bin/python`，一次运行一个非空集合。`$image`、`$runtime` 从基线证据取得；若换镜像，先验证 Python 路径和依赖。`$state` 独属于本实验，避免与生产或其他实验混用。

```powershell
$image = '<已核实的本地镜像 ID>'
$runtime = '<codex 与 codex-code-mode-host 所在目录>'
$auth = Join-Path $env:USERPROFILE '.codex/auth.json'
$state = "D:/DoxAgentBacktest/w3_backtest_linux/$experimentId"
New-Item -ItemType Directory -Path $state -Force | Out-Null

$dockerBase = @(
    'run','--rm','--init',
    '--mount',"type=bind,source=$experiment,target=/evaluation",
    '--mount',"type=bind,source=$experiment/execution_source,target=/execution,readonly",
    '--mount',"type=bind,source=$runtime,target=/runtime,readonly",
    '--mount',"type=bind,source=$auth,target=/auth/auth.json,readonly",
    '--mount',"type=bind,source=$state,target=/state",
    '--workdir','/execution',
    '--env','PYTHONPATH=/execution/src',
    '--env','CODEX_HOME=/auth',
    '--env','LOCALAPPDATA=/state',
    '--env','DOXAGENT_CODEX_RUNTIME_BIN=/runtime/codex',
    '--entrypoint','/app/.venv/bin/python', $image
)

$suite = 'decision'   # 另一个集合为 timeliness
$split = 'dev'        # 有非空 validation 时独立运行
$lane = "${suite}_${split}_r01"
$runArgs = @(
    '-m','doxagent.w3_backtest','run',
    '--dataset','/evaluation/dataset_snapshot',
    '--run-id',"/evaluation/$lane",
    '--prompt-root','/evaluation/prompts',
    '--suite',$suite,'--split',$split,
    '--concurrency','4','--timeout-seconds','1800',
    '--model','gpt-6-luna','--effort','max',
    '--service-tier','default','--trade-mode','single','--time-awareness'
)
& docker @dockerBase @runArgs
if ($LASTEXITCODE -ne 0) { throw "运行入口失败：$lane；先检查已有逐 Case 结果" }
```

并发来自 `--concurrency`，不是另开执行子agent。可以并行启动不同集合/轮次，使用不同 lane、容器名和日志；计算**所有 lane 的总并发**。单 lane 常用 4，现有 CLI 允许 1–16；不要不经评估把每 lane 16 叠成几十路。耗时对比尽量保持同等负载，并在报告交代实际并发、是否跨批次。

同一环境内原生 CLI 为：

```text
python -m doxagent.w3_backtest run --dataset <冻结数据绝对路径> --run-id <新运行目录> --prompt-root <提示词包> --suite decision --split dev --model gpt-6-luna --effort max --service-tier default --trade-mode single --time-awareness --concurrency 4
python -m doxagent.w3_backtest resume --run-id <原运行目录>
python -m doxagent.w3_backtest report --run-id <原运行目录>
python -m doxagent.w3_backtest compare --before <旧运行目录> --after <新运行目录>
```

上述 `resume/report` 必须在能解析原 manifest 中 Linux 挂载路径的相同环境执行，不要直接拿 `/evaluation/...` 路径在 Windows Python 下重跑。空集合会在模型调用前报错，事先按 Catalog 跳过即可。

### 6.4 有失败时怎样补测

先看入口日志、`cases/<key>/result.json`、`error_traceback.txt` 和 Worker/原生回执。进程退出 0 不等于全部 Case 成功；CLI 可以捕获单 Case 错误后正常输出报告。

- `resume` 从原 manifest 读取数据、提示词、模型、档位、时间感知和成员，跳过 `status=SUCCEEDED`。身份改变时会拒绝复用目录。保存原失败回执后再 resume，避免覆盖唯一诊断证据。
- 更清楚的方式是在新的 `*_retry_01` 目录执行相同配置，只追加失败的 `--case <key>`，可重复该参数。收集所有尝试，为每 Case 选择**最早有效结果**，记录 `attempt_directory`。不因结论不合 GOLD 而选择后一次。
- 若某条虽然 SUCCEEDED，但实际模型错误、资料错位等使其无效，说明证据和裁定，再在新补测目录只测该条；`resume` 会跳过成功状态，不能用它修复这种情况。
- 提示词、模型、输入、时钟或运行时变化不是原样技术补测，按新实验/新输入版本登记。
- 仍有未解决失败时交付已完成部分，报告列出失败数、有效分母与原因；不把它们填成 NO_TRADE 或隐去。

遇到 `model_not_found / unsupported`，先核对请求模型、实际 provider、Python SDK **及原生 Codex** 版本、账号模型清单和支持的 effort。必要时仅升级评估私有依赖，不修改生产；账号已确认可用时，不能凭旧 SDK 报错断言没权限。保留已成功调用，只补失败的，不静默降级模型。

时间感知验收要对应**实际代码版本**：旧运行器可能要求 SQLite 中保存原点；新版已支持冻结/内存态、非阻塞降级，缺一条历史 KV 不能自动证明模型未收到时间上下文。结合本轮 `time_awareness`、Worker 请求及原生实现核对；不要照搬旧脚本把有效付费结果判无效重跑。

## 7. 执行完成后，先验收再评分

每个运行目录通常包含：

| 产物 | 作用 |
|---|---|
| `run_manifest.json`、`prompt_snapshot/`、`catalog_snapshot.jsonl` | 实际冻结身份，不靠口头配置推断。 |
| `runtime.sqlite3`、`worker/`、独立原生 state/sessions | 母子线程、任务、请求、模型选择及时间元数据；state 可能在实验外的私有目录，须记录路径。 |
| `cases/<key>/result.json`、`decision.json` | 原始结果、四字段、模型、时点、线程与阶段回执。 |
| `visible_input.json`、`input_coverage.json` | 模型业务输入及资产覆盖，辅助排查上下文差异。 |
| `native_tool_trace.jsonl` | 实际工具调用及结果；部分 code-mode 调用不会完整计入 SDK 汇总计数。 |
| `results.jsonl`、`summary.json`、`report.md` | 内置宿主评分产物，不代替完整的研判质量分析报告。 |

检查：本轮选定成员每条恰好一个有效结果；没有重复计入技术重试；四字段合法；原 Case ID、cutoff、真实模型/effort/tier正确；母线程和子线程不同、独立轮次不串子线程；Operations 为 NOT_REQUESTED，阶段仅记录；提示词哈希与用户文件一致。

检查模型不能只看 `experiment.json`：抽查 `result.model`、phase 的 `model_selection.json`，必要时原生 session 的 `turn_context`。研究/模拟搜索索引不含 GOLD、评估标签、历史答案或 `web_gaps` 一类构造侧结论；不得将 Catalog/Review 作为 instructions、task、MCP 清单或工具结果给模型。这里是输入及工具范围的约束，不能仅因文件放在宿主目录就宣称做了绝对文件系统隔离。

内置 `report` 会读取运行 manifest 指向的数据目录重新评分，并写出机器报告。它不会自动恢复你想象中的历史 GOLD；因此使用冻结快照。若有意按新 GOLD 重判旧结果，明确记录评分版本和变化，不覆盖原始模型决策、旧报告或原 Catalog 快照。

## 8. 指标怎样计算

### 8.1 必备统计与分母

普通集与时效集分别报告；每个非空 split 分开。普通集至少给出**整体、边缘、非边缘**三组，边缘仅由 `edge_case` 确定。分组计数应相加等于整体。

设 N 为有 GOLD 且有效的决策数；P 为当次预期 LONG/SHORT 数；Q 为预期 NO_TRADE 数；T 为实际输出 LONG/SHORT 数；C 为方向正确的交易数；FP 为预期 NO_TRADE 却交易；FN 为预期交易却 NO_TRADE；WD 为方向反转。

| 指标 | 口径 |
|---|---|
| 准确率 | 所有正确方向和正确 NO_TRADE / N |
| 误交易率 | FP / Q；同时给 FP / N，明确是占全部样本比例 |
| 漏交易率 | FN / P；同时给 FN / N |
| 交易精确率 | C / T，按方向正确计分 |
| 交易召回率 | C / P，按方向正确计分 |
| 错向 | WD 单列；不藏入“未交易式漏判”，精确率和召回率都受影响 |
| 实际交易中的错误比例 | (FP + WD) / T |
| 全部拒绝基准 | Q / N，说明类别不平衡，防止高 NO_TRADE 准确率遮蔽零召回 |
| 技术失败 | 原始次数、涉及 Case、最终未解决数；不计为经济判断错误 |

写成 `3/12（25.00%）`，避免只给百分比。分母为 0 用 `— / N/A`，不能填成 100% 或 0%。未标注/待确认不默认为 NO_TRADE。普通集的两个错误大类与错向应与总错误数对得上。

如果时效集当轮全是过期负例，主要指标为拒绝率/过期放行率；交易召回率无定义。还要看 reason 是否真的识别时效，不能把“因经济分量不足而拒绝”一概算成时效识别成功。存在新鲜正例时，另列它们的保留/误拒情况。

### 8.2 可复用的宿主指标函数

可放入**本实验目录**的分析脚本，不必为报告改业务模块。输入是验收选定后的原始结果和本轮冻结 Catalog，已按一个 suite/split 筛选；不要把不同集合传入同一“总体分数”。

```python
import statistics
from datetime import datetime

def metrics(records, catalog_by_key):
    assert records and all(r['status'] == 'SUCCEEDED' for r in records)
    assert len({r['case_key'] for r in records}) == len(records)
    pairs, times = [], []
    for r in records:
        c = catalog_by_key[r['case_key']]
        expected = 'NO_TRADE' if 'timeliness_nontradable' in c['tags'] else c['gold']
        actual = r['decision']['decision']
        assert actual in {'LONG', 'SHORT', 'NO_TRADE'}
        pairs.append((expected, actual))
        times.append((datetime.fromisoformat(r['finished_at']) -
                      datetime.fromisoformat(r['started_at'])).total_seconds())
    ratio = lambda x, y: x / y if y else None
    n = len(pairs)
    p = sum(e != 'NO_TRADE' for e, a in pairs)
    q = n - p
    t = sum(a != 'NO_TRADE' for e, a in pairs)
    c = sum(e == a and e != 'NO_TRADE' for e, a in pairs)
    fp = sum(e == 'NO_TRADE' and a != e for e, a in pairs)
    fn = sum(e != 'NO_TRADE' and a == 'NO_TRADE' for e, a in pairs)
    wd = sum(e != 'NO_TRADE' and a != 'NO_TRADE' and e != a for e, a in pairs)
    correct = sum(e == a for e, a in pairs)
    return dict(n=n, expected_trade=p, expected_no_trade=q, predicted_trade=t,
                correct=correct, accuracy=ratio(correct, n), correct_trades=c,
                false_trade=fp, missed_trade=fn, wrong_direction=wd,
                false_trade_rate=ratio(fp, q), missed_trade_rate=ratio(fn, p),
                false_trade_share=ratio(fp, n), missed_trade_share=ratio(fn, n),
                trade_precision=ratio(c, t), trade_recall=ratio(c, p),
                always_no_trade_accuracy=ratio(q, n),
                median_seconds=statistics.median(times),
                mean_seconds=statistics.mean(times),
                p95_seconds=(statistics.quantiles(times, n=100, method='inclusive')[94]
                             if len(times) > 1 else times[0]),
                max_seconds=max(times), over180=sum(x > 180 for x in times))
```

多轮 pooled 统计要把每次有效决策当一次观察，唯一身份改为 `(case_id, round)`；上述唯一 Case 断言适用于单轮，不能直接用于 pooled 数据。另保留每 Case 的结果分布，不能仅用 pooled 得分代替稳定性。

### 8.3 耗时

统一使用 `finished_at - started_at`，单位秒；至少中位数、均值、P95，建议补最大值及 >180s 数量。P95 沿用 `statistics.quantiles(..., n=100, method='inclusive')[94]`；样本 1 条时单独处理。

当前 Case 耗时不含公共母线程初始化，但含单 Case 准备、fork、工具和调用等待；它不是纯模型推理时延。母线程初始化、整批墙钟、技术失败消耗若要报告，应单列。

与多轮基线比较时，除全组汇总，还计算每 Case 的新耗时减基线同 Case 多轮中位数，列差值中位数、变慢数量及典型长尾。这样才能区分“某个极慢样本缩短”和“多数普通 Case 普遍变慢”。负载、批次、模型/tier、运行时不同要说明，不能把所有变化归于提示词。

## 9. 怎样选基线、处理增删 Case 与重复实验

1. **同输入优先**：按原 Case ID 对齐，核对 cutoff、消息、研究、可见上下文、搜索、行情及业务输入指纹。只比较同样目录名不够；模型/tier 改变属于模型实验。
2. **新语料有变化**：同时给“新完整测试集成绩”和“新旧共同且输入一致部分的比较”。不能用不同数量和标签的总分声称提示词提升。
3. **扩展基线**：用户要求时，可保留输入不变的旧有效结果，只给新 Case 补基线所需轮次，形成复合基线。写明各 Case 来源与轮次，不称为同一天重新执行的四轮全量。
4. **只改 GOLD**：旧输出可以按新 GOLD 重算，不需要花钱重调模型；保留新旧评分依据。
5. **改时钟、证据、上下文**：这是新输入版本；相关旧结果不能直接充当同输入基线，应仅对受影响 Case 补齐需要的对照。原结果留档。
6. **一轮对四轮**：同时展示基线各轮范围与 pooled 值，明确候选只有一轮。不能把一轮高分宣称为稳定提升。
7. **要求复测**：用独立新 lane、相同冻结配置执行，不触发自动“错了再测”。稳定性列稳定正确、稳定错误、3:1、2:2 等实际分布；多于两个方向时按真实分布记录。多数票若展示，只是辅助指标，不替代每轮原始正确率。

## 10. 分析报告怎样写

报告目标是解释 **W3 的交易研判门槛为何对/错、为何变化**，为下一次提示词优化提供证据。不能只复述 reason 或用“变保守了/更激进了”概括全部错误。

### 10.1 推荐结构

1. **开头给主要发现**：普通集表现、精确率/召回率变化、时效拦截、主要成本及局限。说明单轮/多轮，避免先堆运行细节。
2. **实验口径**：提示词来源和布局、冻结数据、模型/effort/tier、时间感知、运行时、并发、基线来源、技术补测、集合构成。
3. **核心表**：普通集整体/边缘/非边缘，各列 N、正确率、FP/Q、FN/P、Precision、Recall；时效集单列。再列耗时 P50/均值/P95。基线多轮给逐轮或范围和 pooled，避免只展示对候选有利的一轮。
4. **完整 badcase 清单**：误交易、漏交易、错向分别列，覆盖本次全部错误；新增错误、修复旧错误、反复不稳定要可识别。
5. **研判逻辑分析**：读相关 Gold Review 与实际四字段，解释分歧发生在哪个环节。重点详写稳定错误、双方同错、改判及边缘/时效 Case，其他可简写。
6. **耗时和取证行为**：选有代表性的变慢/变快 Case 解释，有必要再查工具轨迹；不是所有 Case 都做完整线程取证。
7. **可用于下一步优化的结论**：针对有证据的判断门槛、重复研究或取证覆盖提出有限建议，不在评估过程中自行改候选提示词。
8. **链接**：逐条矩阵、四字段原文、机器指标、验收和原始运行位置。正文自洽，不能让用户必须先看聊天记录。

### 10.2 每个错误至少回答什么

| 问题 | 应写出的内容 |
|---|---|
| 背景与预期 | ticker、消息讲什么、此前同维度市场/公司已知什么、事件 GOLD 与当次应判为何如此。 |
| W3 如何推导 | 用 reason、Prior、Delta 的具体措辞说明模型认可哪些事实，在哪一步接受或否决交易。 |
| 相对 GOLD 的核心偏差 | 事实读取错、Prior 口径错、新旧重复计算、经济传导跳步、量级归属错、商业状态错，还是时效/价格解释错。 |
| 新旧版本有何差异 | 方向相同也要看理由是否变了；改判是证据读到不同、同事实权重变化还是数据不一致。 |
| 边界 | `edge_case` 单独标明；区分 GOLD 本身留有判断空间与模型明确违背事实，不按模型结果改边缘标签。 |

常见需要检查的交易逻辑：把上游总金额直接算成目标 ticker 的收入；把单家预测当共识上修；把累计规模当本次新增；把风险解除当巨大盈利修订；相反，又对具名客户验证、融资能力兑现、产业链明确扩张一律等待实际订单/利润才承认增量。要看本 Case 的已知商业联系和净增量，不能把这些例子写成永久禁止规则。

尤其关注“文字说有显著 Delta，却用不同标准否决”和“承认无份额证据，实际又按全部规模授予重大权重”的不一致。公开四字段是可审核证据；不要将它们冒称隐藏 reasoning 全文。

### 10.3 时效 Case 与工具轨迹

检查 W3 是否找到原发/近似原发、是否误把转载时间当新披露、是否区分新条款与旧事件、价格重定价判断有无依据。正确 NO_TRADE 的理由可能是经济不够而不是过期，要单列说明。

怀疑未看到关键资料时区分三种原因：**数据集没有准备**、**已准备但模型没搜索/翻页/读取到**、**已读到但解释不同**。可以看 `native_tool_trace.jsonl`、`turn_summary.json`、原生 session 的公开摘要与工具主题，并追到对应 `thread_id`。只看检索结果条目数量无法确认模型实际看到什么。

时间提醒研究只在需要时展开：确认该轮实际支持提醒、是否越过阈值、是否在公开 summary 中回应、后续是否从扩展检索转向收束。没有文字回应不等于未收到；有“尽快结束”也不等于真实停止研究，结合后续调用和时间戳判断。

### 10.4 多轮稳定性报告

逐 Case 列四字段来源和各轮方向。优先分析一致但错误，再看 2:2、3:1，以及新版本修复/损失了哪些稳定能力。说明分歧是研究覆盖不同、对同一事实估值不同、Prior 取法不同或时效识别不同，不用一个“随机性”结束解释。

报告中给每轮准确率、误/漏交易和耗时，另给 pooled 统计与 Case 一致率。若只复测错误子集，它是条件抽样，不能把其准确率当新的全量准确率；也不能把第一次全量与后续仅错误复测凑成四轮全量。

## 11. 仅在增补 Case 时使用的规则

提示词重测直接复用已有资产。若用户另要求新增/补强 Case，先读构造规范顶部修订，主 agent 负责 Case 身份、只读恢复、时钟、研究、行情、Catalog/Review 和最终审计。

用户此前明确限定：只有**新 Case 的模拟 Web Search 构造**可委派给 `gpt-6.1-terra / high` 子agent，每批尽量 3–5 条；它只能做这项任务。不要把框架、GOLD、筛选、评分或报告委派过去，也不要将 GOLD、评估标签、旧决策、底稿交给构造者。新对话还应遵守其当下工具权限和用户授权；本文不触发任何委派。

模拟材料要从“身处 as_of 的 W3 在决策前可能查什么”展开：原发正文与时间、同维度 Prior、具体条款/状态、量级与目标公司联系、行业/替代解释、公司回应及同期反证。多样性指不同研究问题和实质事实，不能给标题凑若干转载；也不把十来条或某个数量当充分性证明。按 Case 相关性扩展，保留真实正文与事实限定。

时效 Case 要自然包含能恢复原发的证据，不标“这是旧闻/应拒绝”。不伪造时间、正文或价格，不加入 `web_gaps`、缺资料就保守等引导。覆盖说明和未取得材料留在 preparation。以约定情景剔除明显未来披露和价格即可，不建设额外 harness 或追求无法兑现的绝对零未来函数。

## 12. 交付前清单与下一位 Codex 的最短工作路径

- [ ] 原始新提示词按指定布局冻结，内容哈希已核对，没有残留旧 Skill。
- [ ] Catalog、Review、manifest、输入与研究已冻结，实际成员和四个集合已核对。
- [ ] 模型、effort、tier、时间感知、runtime/companion、镜像和并发可追溯。
- [ ] 各 Case 有唯一选定有效输出；失败只补失败，所有尝试保留，未解决项明示。
- [ ] 仅第一阶段，没有交易意图、订单、Operations 或生产写操作。
- [ ] 普通集/时效集、DEV/VALIDATION分开，edge/non-edge 定义固定，分母及错向完整。
- [ ] 新旧对照用同输入；复合基线、多轮与样本变化已说明，没有择优选轮。
- [ ] 报告解释所有错误相对 GOLD 的实质偏差，特别检查时效和边缘 Case，以及公开 reason/Prior/Delta 的变化。
- [ ] 耗时含义、P95 算法、普通 Case 配对变化和长尾均有正确说明。
- [ ] `evaluation.md`、逐条矩阵、公开理由、机器结果、验收与原始路径可找到。

最短路径是：**读 Catalog/Review → 确认基线与配置 → 建新实验并冻结资产 → 原字节放入新提示词 → 按非空集合并发执行 → 仅补技术失败 → 验收选定有效结果 → 依冻结 GOLD 重算指标 → 阅读错误背景与四字段/必要工具记录 → 写评估报告并交付链接。**

另有 [本地真实能力 W3 评估入口](C:/Users/WEIXUANXIE/Desktop/DoxAgent/docs/w3-local-live-evaluation.md)，用于刚产生的生产 Case，以本次启动时点调用真实搜索与行情。它与本文历史离线回放分开使用，不自动加入正式集或与静态资料基线混比。

后续若修改重要代码，按仓库要求追加 `changelog`；提示词回测的实验留痕保存在独立运行目录。

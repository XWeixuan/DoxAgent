# W3 回测时间感知能力接入

日期：2026-11-06。按用户要求轻量复用现有能力，本轮没有真实模型调用、没有新增Case回测、没有修改业务提示词或部署生产。

## 接入结果

[时间感知开发报告](w3-time-awareness-harness-implementation-20261106.md)所述的 `--time-awareness`、Worker控制元数据和原生补丁已经存在于当前代码。本轮在这些接入点上补齐回测适配，不复制阈值文案、不新增时间工具、后台计时器、模型阶段或另一套Harness。

| 项目 | 处理 |
|---|---|
| 计时起点 | 公共规则母线程就绪后，在单Case研究准备、绑定及Fork前开始；此前回测在Fork后才开始，会漏算准备耗时 |
| 双时钟 | Elapsed使用生产 `begin_trade_timing/time_context` 的真实UTC起点；业务消息、新闻截止与行情仍使用固定历史as_of |
| 重试与恢复 | 沿用Case KV中的原起点，依赖生产逻辑保持单次起点；不重置历史业务时间，也不覆盖原起点 |
| 阶段范围 | 仍只给trade_decision发送元数据，Template不计入Case的Elapsed；v2不运行Operations |
| 运行时选择 | 复用 `configured_codex`，使Fork辅助进程和决策运行时使用同一明确指定的补丁二进制 |
| 可观测性 | 已启用Case的result.json增加time_awareness起点回执；可选起点写入失败时为null，关闭时不加该字段 |
| 对照身份 | 开关沿用既有run identity；开启时额外冻结二进制路径及SHA256，避免同一run悄悄更换原生提醒实现 |
| 依赖提示 | 显式开启但没有可执行二进制时，在模型派发前报错；Windows直接配置现有Linux ELF时明确提示平台不匹配 |

原生的Elapsed更新、120/180/240秒软提醒、历史过滤与压缩隔离继续由已有补丁实现。本轮没有重写这些规则。运行时摘要表示配置了哪份产物，不单独证明每次原生请求均注入了提醒；实际片段验证仍以原生运行记录为准。

## 使用

默认保持关闭。开启方式为在当前回测进程配置 `DOXAGENT_CODEX_RUNTIME_BIN`，并在v2的run命令加 `--time-awareness`。`resume`从manifest读取原开关，并要求所选二进制与原记录一致；改变开关、二进制或提示词应创建新run-id。

现有补丁产物是Linux x86_64，沿用此前已使用的Linux容器/WSL回测环境即可；其ELF文件不能在Windows Python中直接执行。Windows原生接入接受同补丁的Windows二进制，但本轮未额外构建这一产物。没有为了跨平台增加代理服务、远程生产连接或新的执行框架。

完整命令见[回测README](../eval/w3_backtest/README.md)。默认仍读取测试集冻结提示词；用户正在修改的业务提示词没有被提前复制到数据集或用于真实评测。

## 修改范围

- `src/doxagent/w3_backtest/decision_runner.py`：对齐计时起点，并记录生产返回的起点元数据。
- `src/doxagent/w3_backtest/runner.py`：检查显式运行时依赖、计算摘要，复用生产SDK配置选择，统一Fork与决策二进制。
- `src/doxagent/w3_backtest/cli.py`：把启用时的运行时身份纳入现有冻结检查。
- 两份既有回测测试文件：扩展准备顺序、双时钟、元数据隔离、旧manifest默认关闭、resume继承、运行时变化及平台依赖验证。

以上是对当前已有时间感知接入的补充。保留了其他开发任务在这些文件中的原有未提交改动，没有修改生产时间感知模块、Rust补丁或模型路由。

## 本地验证

```text
python -m pytest tests/test_w3_backtest_curated.py \
  tests/test_w3_backtest_prompt_snapshot.py tests/test_w3_time_awareness.py \
  tests/test_w3_backtest_runner.py -q \
  -k 'not test_replay_uses_production_two_stages_with_isolated_evidence'
```

结果：**38 passed，1 deselected**。被排除的是原开发报告已经在改前独立复现的旧二阶段READY/RUNTIME_DELAY_EXCEEDED用例；本轮没有改其业务时间规则。

涉及的三份运行模块、两份测试文件通过Ruff；CLI帮助已确认入口存在。测试使用模拟Worker/Responses，不调用真实模型或外部行情接口。

本轮验证的是Python回测接入、配置身份与生产计时复用；没有重跑原生编译，也没有追加真实投研质量或提速验收。原生阈值、恢复及历史过滤的既有验证见[原开发报告](w3-time-awareness-harness-implementation-20261106.md)。

# 本地真实能力 W3 评估

入口为 `python -m doxagent.w3_backtest.live_evaluation evaluate`。按生产 Case ID 只读恢复原消息、W1/W2、原研究版本和可恢复的冻结上下文。每轮使用独立本地目录、SQLite、母线程与 Case fork，完成 Trade Decision 后记录结果。

## 勘察及复用

既有 `DecisionReplayRunner` 已复用生产 `CodexW3AgentRunner`、v6输入投影、母线程初始化、原生fork、schema验证和时间感知。其原有运行环境为离线回放，禁用Web Search，并提供模拟搜索及固定行情。

新增模块位于 `w3_backtest/live_evaluation.py`、`live_history.py`、`live_launcher.py`，复用第一阶段调用链。独立的本地Worker启用真实Web Search，恢复版本固定的D1/D2供Research Assets MCP读取，并使用生产W3MarketPreparer提供统一行情。行情数据通过宿主上固定的历史K线接口从远端读取，模型进程不获得SSH身份、IBKR连接参数或账户权限。v6研究范围继续使用现行Research Assets MCP。

生产运行服务没有接入这个模块。它没有Effect Dispatcher、Operations或交易执行调用。Worker在派发前只接受w3_template和trade_decision两种阶段。研究MCP只提供catalog、search、read和market_view。宿主行情桥仅接受固定ticker和4类行情窗口，远端脚本固定调用MarketProvider.history，其他HTTP路径、ticker和操作会被拒绝。原始生产Case、Policy、PIN、WATCH都只作为读取来源。相关边界和已有第一阶段回放共11项针对性检查通过。

## 启动

从仓库根目录执行，指定一个新的输出目录。示例使用本地已验证的Linux镜像和时间感知二进制目录。

```powershell
.venv/Scripts/python.exe -m doxagent.w3_backtest.live_evaluation evaluate --case-id case_0fb1f465d22c4c36b31e9d6bb0094d55 --output eval/w3_live_evaluation/my_trial --rounds 1 --parallel 4 --runtime-dir eval/w3_backtest/runs/production_rev2_reviewed55_4r_20261121/runtime --image sha256:8e34caa20ea57f3dd47c5c9c019c1399a46dc806bc9c18a3b989445b66795ef9 --model gpt-6-luna --effort max --service-tier fast
```

依赖本地Docker、Codex订阅身份以及已有doxagent-sg只读SSH访问方式。镜像提供应用Python依赖，代码从本地src只读挂载。宿主行情接口使用临时随机鉴权与临时端口，评估结束关闭。Docker模型进程只挂载恢复后的原消息/上下文/研究资产、提示词、运行输出和Codex身份文件，原始生产Case完整归档保留在宿主acquisition目录。

省略prompt-root时，从远端active bundle取得当时生效提示词。可用 `--prompt-root <目录>` 指定自有提示词，支持单AGENTS.md、AGENTS.md加skills/skill.md，也接受根目录skill.md。缺少contract.json时补入v6版本标识。支持model、effort和service-tier参数。未指定model/effort时沿用现有Case路由逻辑。

## 时间和产物

每轮创建独立evaluation_id，评估启动时间注入为input.cutoff_at。原消息发布时间、原Case创建时点、原研究版本及启动时间分别保留。真实搜索和行情在本次运行期间读取，行情返回observed_at，模型按本轮cutoff判断消息时效。多轮在队列中陆续启动时，各自时点和可获取的公开资料会变化。

输出含acquisition、source、prompt_source、history_requests.jsonl、rounds和results.json。每轮SQLite只记录本地线程和第一阶段结果。result.json保存decision、reason、prior_expectation、expectation_delta、独立身份、线程、耗时、模型和时间信息。原生工具记录在native_tool_trace.jsonl。

失败Case使用新的输出子目录单独重试，保留已完成结果。一次真实评估的结果不会自动成为Gold或历史回测集成员。

## 本次验证

COHR的“Oracle Becomes First Fully Funded Hyperscaler for 4.5 GW OpenAI Contract”对应 `case_0fb1f465d22c4c36b31e9d6bb0094d55`。原消息发布时间为2026-11-20 21:37:12.622 UTC，Case创建于21:37:34.533 UTC。恢复到的生产提示词bundle为 `58382a2ab9529c14844cc0670b4d507b3ccd04fe1219cb80a9624768d9d2237e`。

本批使用GPT 6 Luna max fast，5路并行，生产提示词10轮，T1-R3V1、T1-R4V1、T1-R5V1各1轮。后两份文件内容一致，保留各自名称。结果与公开研判理由保存在 `eval/w3_live_evaluation/oracle_cohr_20261121`。

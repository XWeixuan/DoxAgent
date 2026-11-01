# W3 回测校准升级：实现与验收回执

日期：2026-10-30。实现范围为本地历史情景的第一阶段决策评估；未部署或改动生产运行状态。

## 交付范围

正式数据集为 `eval/w3_backtest/datasets/w3_curated_20261030`。用户原始59条目录完整保留；原计划44条中，用户后续明确移除BE-02-04，最终43条，Gold为4 LONG、3 SHORT、36 NO_TRADE。选择覆盖存于`preparation/selection_overrides.json`，重新导入也不会恢复该条。随机NOISE不进入新版。其余Gold与用户时效性标签保持原样，未按模型结果改标。

旧版73条数据集、旧运行及生产AGENTS/Skill文件均保留。新的输入格式与执行路径只在v2数据集启用，旧版二阶段入口兼容。

## 编排与输入

- 每轮使用一个共享Worker、Journal与提示词母线程；母线程加载生产active快照的AGENTS、唯一Skill及独立中文backtest.md，不处理业务消息。
- 每Case从母线程独立prepare-fork，使用生产的线程注册与阶段记录；仅运行trade_decision，保存v5四字段决策后结束，不调用W3Execution.trade/operations，不创建交易意图或WATCH/PIN。
- 沿用现有w3-entry-v5与本地w3_research读取、检索和market_view工具。它只承载冻结资料；实时Web、Data MCP、IBKR及子Agent均关闭。
- Agent输入为原消息、固定as_of、必要W1/W2判断及资料入口。D1/D2按历史绑定恢复，D3/Policy、历史交易、WATCH/PIN及使用状态不交付。Gold/Tag、旧W3输出、路由与宿主过期标记、采集困难均留宿主侧。
- Journal和SDK超时使用真实时间；消息新旧与价格只按Case as_of。DATE消息不再向W3暴露16:00等占位时分。

`backtest.md`明确资料及价格可信、无需因离线环境追加审计或解释缺失状态，同时保留真实报道中的传闻、预测与条件性质，不提高或降低业务交易门槛。

## 测试集重做

实际发起3个`gpt-6.1-terra/medium`构造子Agent，按每批4条推进，个别批次额外带1条补充修复；均要求先读构造规范，完成当前批次后再发下一批，禁止读取Gold/旧结果。逐批记录见`preparation/construction_dispatch.json`。

原44条均已重做材料；移除BE-02-04后，正式43条共有380份来源，平均8.84份，最少5份、最多16份。没有为了达到数量硬凑转载：新增材料覆盖原发正文/关键段落、此前披露与预期、经济传导、经营规模、对照和回应。数量低于手册12–20的参考区间的Case，其已有D1/D2及资料覆盖须结合宿主review阅读，不能把数量本身当成完整性证明。

文件格式：`evidence/sources/*.md`为具体事实与忠实摘录，`web_search.jsonl`与`index.json`仅含中性导航；采集限制和搜索轨迹仅在`preparation/web_review`。原网页不可读时，采用可信同稿、可见正文及真实背景，不编造原文。

DATE补时使用原页JSON-LD或同稿精确分发时间加5分钟，代理口径留档。两条TrendForce取得秒级原发时间；NBIS Citi取得Business Wire精确分发时间。BE-02-04因无可靠时分按用户指示移除。对于后来更新的DOE等页面，本轮按用户允许的近似历史情景使用原发核心事实，剔除能明确识别的晚发独有新增内容；版本无法完全回溯的限制只留宿主，未伪称存在历史快照。

## 行情

准备阶段在新加坡远端通过现有只读IBKR历史接口取得真实数据，回放时仅注入本地文件。每Case提供五个完成RTH日线、过去24小时60分钟bar、过去4小时5分钟bar；SMART/OVERNIGHT按生产规则拆分。排除跨越as_of的forming bar，不补造价格。休市空窗保留真实空序列。

已知核心披露早于标准窗口时支持额外事件段；NBIS Béthune已准备对应5分钟事件行情。采集原始回执在`preparation/prices`，Agent只读中性market_view。

## 实际修复的接线问题

1. 直接Fork与Resume分别使用不同SDK owner会触发active writer；仅在Resume改配置也不能保证新子线程拥有研究工具。改用现有prepare-fork，在创建时配置冻结研究入口。
2. 嵌入SDK原来只有一个prepared-thread槽；并发下需按thread ID保存并消费准备结果，仅序列化短暂的创建/启动步骤，不串行模型研判。
3. 隔离CODEX_HOME仍会发现主机与SDK自带Skill；使用私有运行配置逐项关闭，并在真实母线程回执中验证无额外Available skills目录。
4. Windows新建pack目录偶发被短暂占用，回测存储适配器做有限重试；不影响生产存储。
5. 非UTC的DATE补时必须先转UTC再调用远端历史接口，避免把本地时分错误标成UTC。

## 验收

工程小样本按方案选择New Street、CEG、NASA、INTC–OpenAI、Synspective及MU-03-03，固定gpt-6-luna/max。6条全部完成；真实Journal为1母线程、6个独立子线程、6个COMMITTED trade_decision，未出现运营阶段。已确认实际调用本地研究读取及行情工具。

该6条业务评分为2条匹配、3条漏交易、1条误交易。工程通过不等于业务判断全部正确；未为提高分数改Gold或材料。完整结果见`eval/w3_backtest/runs/calibrated_smoke6_20261030/report.md`。

全量运行初始ID为`calibrated_full44_20261030`，因选集修订仍保留原运行ID，最终按43条正式集评分。运行中曾有3条在任何模型输出前超时；失败回执保留，技术重试单独留痕。正式43条现已全部完成；原3条无输出超时在相同输入、冻结提示词与模型profile下各重试一次后全部成功，保留原失败回执。

|指标|结果|
|---|---|
|合法决策/正式Case|43/43|
|Gold匹配|32/43，74.42%|
|误交易|6|
|漏交易|5|
|方向错误|0|
|非边缘|25/31，80.65%|
|边缘|7/12，58.33%|
|时效性标签|7/9，77.78%|
|最终技术失败|0；初次3条超时均已恢复|

最终审计确认：43条输出均匹配当前Case输入指纹和gpt-6-luna/max实际模型选择；只有1个母线程、43个成功Case子线程（另保留3个超时子线程），43个COMMITTED决策阶段，无运营或交易写入。相关79项自动检查全部通过；Ruff和diff空白检查通过。

结果文件：
- [全量43条报告](../../eval/w3_backtest/runs/calibrated_full44_20261030/report.md)
- [11项与Gold分歧](../../eval/w3_backtest/runs/calibrated_full44_20261030/disagreements.md)
- [最终工程审计](../../eval/w3_backtest/runs/calibrated_full44_20261030/final_acceptance.json)
- [技术重试回执](../../eval/w3_backtest/runs/calibrated_full44_20261030/technical_retry_receipt.json)
- [79项测试记录](../../eval/w3_backtest/datasets/w3_curated_20261030/preparation/validation.junit.xml)

这次数据选集、Gold与上下文均有变化，分数不与旧73条结果直接同比；历史W1/W2理由仍是固定上游输入，未重跑当前完整链路。下一轮可以用同一冻结测试集和提示词、显式模型profile进行模型或提示词对照。

## 使用

从仓库根目录使用`python -m doxagent.w3_backtest`：

```powershell
.venv/Scripts/python.exe -m doxagent.w3_backtest build --dataset w3_curated_20261030
.venv/Scripts/python.exe -m doxagent.w3_backtest run --dataset w3_curated_20261030 --run-id experiment-01 --model gpt-6-luna --effort max --concurrency 3
.venv/Scripts/python.exe -m doxagent.w3_backtest report --run-id experiment-01
```

提示词实验可显式`--prompt-root`，目录应含AGENTS.md、skills/skill.md、backtest.md及contract.json。新运行冻结提示词hash与模型profile。prepare-curated用于重新导入并冻结生产快照，不是普通恢复命令；已完成数据用build/run/resume/report。

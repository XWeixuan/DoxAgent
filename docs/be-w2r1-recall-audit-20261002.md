# BE W2R1 近七天召回审计（2026-10-02）

## 范围与口径

- 生产只读检查：`doxagent-sg` / `VM-0-15-ubuntu`，`/var/lib/docker/volumes/doxagent-v2_v2-data/_data/{bus/bus.sqlite3,runtime/runtime.sqlite3,initialization/control.sqlite3}`，SQLite `mode=ro`。未修改生产数据、配置或服务。
- 精确七天窗口：2026-09-24 16:35 至 2026-10-01 16:35 UTC（北京时间 9 月 25 日 00:35 至 10 月 2 日 00:35）。为覆盖 9 月 24 日 Jupiter 首发，事件逐条检查扩展到 2026-09-24 00:00 UTC。采样时钟约为北京时间 10 月 2 日 00:35。
- 精确窗口 BE：Raw Messages 355 条、Standard Messages 351 条、Runtime Cases 354 个；全部 ticker 的 Standard Messages 1,898 条。扩展窗口 BE：Standard Messages 374 条、Runtime Cases 379 个。这些是记录数，含转载、版本和重复叙事，不能当作独立事件数。
- 一条消息“R1 召回”只认 `runtime_v2_cases.payload_json.w2_round1.candidate_policy_ids`。R2 命中与否另列。消息标题用于粗筛，复合文章再查正文中关键词；未对全部 351 条逐字核查正文。
- 版本以每个 Case 的 `version_pin.policy_set_version` 为准，并从生产冻结的 `policy_set.json` 核对。v1 于 9 月 21 日发布；v2 于 9 月 29 日 14:11 UTC；v3 于 9 月 29 日 16:56 UTC 新增 AI capex C1；v5 于 9 月 30 日 06:50 UTC 加入 Anthropic/计算承诺 C2、C3；v6 于 10 月 1 日 06:25 UTC 加入 PJM 对应的替代路线延误 C2。不可拿 v6 反判旧 Case。
- [W2R1 合同](../prompts/persistent_runtime_v2/w2_r1.md)要求“具体事实与 criterion 直接、实质相关，且完整判定后仍有合理命中可能”；仅同主题不足，并设最多 3 个候选。R1 无需先证明最终 HIT，但可明确排除仍在触发条件之前的事实。

## GPT 所列疑似漏召

| 事件 | BE 入站与 Case | 当时规则与判断 |
| --- | --- | --- |
| Ameren 500MW | 标题直接命中 0；Barron's 同一篇报道经 IBKR 两条、Barron's ticker 一条，共 3 条正文提及。`std_95d9448c…` / `case_b9c894ca…`（v2）W1 判 NEW，R1 召制造三条，明确说明 Ameren 未对应具体 criterion；`std_c755b5bd…` / `case_a615312c…`（v3）W1 OLD，R1 召一条制造能力财务 policy；`std_e887d680…` / `case_a37c16d1…`（v3）Case 无 W1/R1 结果。 | v1–v4 的 `非AI关键负载形成第二采用引擎` 关注 Bloom 多站点采购、重复批次和首站验收，C1 要求具名非 AI 客户至少两个站点/批次绑定 Bloom 且首站正式验收。Ameren 的 IRP 是天然气燃料电池建议，既无 Bloom 选择，也无首站验收；按当时 R1 合同可明确排除。因此不是确认的 R1 miss。真正的缺口是第一方 IRP 未作为独立标题进入 BE Bus，只在次日复合报道中出现；如需要跟踪早期 utility 需求，应在监测或 policy 设计层补充，而非强迫现有交易 policy 召回。Ameren [官方 IRP](https://ameren.mediaroom.com/2026-09-28-Ameren-Missouri-updates-20-year-energy-roadmap-to-maintain-and-develop-cost-effective-energy-resources-to-keep-grid-reliable-for-customers)。 |
| AI credit stress / Hyperion | BE Bus 只有一篇正文真正涉及 Meta Hyperion/Beignet 的 Reuters 评论：`std_8152c48…` / `case_fbd4812e…`（v1）。R1 召了 Jupiter 外部阻断、验收后移、互联容量三条；R2 全 NO HIT。另有 Oracle 债券和 Jupiter 融资相关标题，但并非 Meta Hyperion 同一消息。 | `Channel or lender risk-off blocks conditional projects` 的 v1 C1 是 utility channel、infrastructure partner 或 lender 对融资/采购框架实际撤回、推迟、重定价或缩小；match scope 与边界指向 Bloom 相关伙伴/框架。Reuters 文中 Meta 债券价格与 Big Tech CDS 是市场风险指标，未给出 Bloom 相关方改变融资框架。R1 未召该 policy 有依据，不能定为确认漏召。若目标是行业早期融资压力，需另有观测层或更早阶段 policy。 |
| Goldman hyperscaler capex | 9 月 25–26 日 4 条 $1.2T 标题/改写，全部入站、均有 BE Case、R1 均空，均使用 v1。9 月 29 日另有一条 $1.89T/上调 66%（`std_599c09e…`），R1 空，使用 v2。 | `AI资本开支与计算强度上修扩大现场电力机会` 在 v1/v2 不存在；v3 才发布，并把上述高盛数据作为基线。属于 policy evolution，非当时 W2R1 漏召。原报告“可能没进 Message Bus”已被排除。 |
| Anthropic $518B / 80% | 5 条标题明确涉及 $518B：前 4 条 Case 分别用 v1、v1、v3、v4，均无 C2；第五条 TrendForce 用 v5，R1 仍空。另有标题不含 $518B 但正文提到者，未逐条复核。 | C2 在 v5 发布，并把 $518B/80% 纳入 reference state，要求未来对同一口径上修且连接新增数据中心与现场电力需求。v5 的 TrendForce 报道重复既有基线，未证明上修和电力传导，R1 空不能直接判 bug。 |

## GPT 所列成功案例的重复性

| 主题 | 标题粗筛结果与复核 |
| --- | --- |
| Project Jupiter / force majeure / pipeline | 扩展窗口 17 条含相关标题；7 条 R1 有候选、9 条空、1 条 Case `UNAVAILABLE`。正例分布在 Yahoo、Reuters、Huggingnews 等多个来源，并非只偶然成功一次；但也绝非全召。空例包括两个明确声称“没有 source_message”的 Case：`case_d352e02e…`、`case_e4924739…`；`case_3b57302f…` 同样声称缺消息且最终 FAILED。其余空例不少是 W1 OLD、Google News 转载/“false alarm”或 Oracle 重申合同，不能仅按标题定为漏召。 |
| Fremont 158,000 sqft | 一条标题直接命中：`std_7a1001d…` / `case_46b66498…`（v1）W1 NEW、R1 空，理由误称未注入 source_message；这是真实的 R1 输入理解异常，但该条正文只有约 148 字且主要讲分析师评级，不能仅凭它定制造 policy 必须入候选。三条 Ameren 复合报道也含 Fremont 事实：一个召制造三条，一个只召战略能力财务 policy，一个没有 W2 结果。所谓“R1 没问题”缺少重复性支持。 |
| 小型燃机/往复式发动机 | BE Bus 只有一条直接标题：Reuters `std_caded3fd…` / `case_15f7297e…`（v1），R1 召两条替代供电 policy，R2 全 NO HIT。单例不能证明该类稳定召回；R1 甚至可能偏宽，因为消息没有识别 Bloom 原有关联项目。 |
| PJM/FERC backstop | 一条直接标题：`std_b4294ffe…` / `case_4a89a6a4…`（v5），R1 召了两个 SHORT/财务风险方向的 policy，R2 全 NO HIT。v5 的 `替代路线延误转化为Bloom份额` 只有具名项目 Bloom 选择 C1；反映正式采购暂停的 C2 到 v6 才增加。因此这是当时 policy 方向/覆盖缺口及 R1 可疑误召，不是当时既有 LONG C2 的漏召。PJM [官方说明](https://insidelines.pjm.com/ferc-accepts-pjm-reliability-backstop-proposal/)确认原 9 月 30 日采购未启动、暂停五个月；[此前方案](https://insidelines.pjm.com/pjm-reliability-backstop-proposal-outlines-steps-to-secure-new-supply-and-maintain-reliability/)列约 6,831MW 缺口。 |
| SpaceXAI / Colossus | 两条直接相关标题：`std_b7ead5e7…`（GPU 扩张，正文未说自建电源）R1 空，有依据；`std_afe4589d…`（正文明确自建电源路线）R1 召 `Alternative firm-power supply`，R2 NO HIT。这是事实内容差异，不是相同消息的随机漏召；但也只有一条包含供电路线的样本，无法证明稳定性。 |

## 更值得处理的真实异常

精确七天窗口 354 个 BE Case 中，352 个已有 W2R1 结果。按明确声称“未提供/未注入 source_message”“仅有 Policy 参考数据”的 R1 理由检索，**21 个（约 6.0%）**命中，均为 `COMPLETED`，其中 **16 个 W1=NEW**。扩展到 9 月 24 日零点则是 375 个有 R1 结果中的 25 个（24 个 `COMPLETED`、1 个 `FAILED`）。这不是空正文的泛化判断：逐条检查 25 个冻结的 W2R1 `round_inputs`，**25 个均有 source_message.title，23 个有非空 body**。例如 Fremont 的 `case_46b66498…` 冻结输入有标题、约 148 字正文，但 R1 仍声称消息未注入；Jupiter 的 `case_3b57302f…` 也有标题、约 151 字正文。

代码侧 `src/doxagent/persistent_runtime_v2/service.py::_run_w2_hot` 将 `source_message` 与 `runtime_policy_projection` 一起写入冻结输入；`transport.py::_cache_optimized_input` 把 projection 放在可缓存参考前缀，把 source_message 放在后续 Current Case Input。上述异常 Case 的冻结 projection 约 139 条 policy，输入约 17–18K tokens。因而可排除“Message Bus 根本未入站”或“冻结 W2 输入没有 source_message”；当前证据尚不能区分模型注意力失败、提供商会话缓存行为或请求传输中的问题。现有结构化校验只校验 policy ID 与 JSON 形状，错误的“无消息”理由可被当成合法空候选并完成 Case。异常跨 v1–v6 发生，不应归咎于某一版 PolicySet。

**优先建议**：对 W2R1 的“消息不存在/仅有 Policy”理由与冻结输入做窄范围一致性校验；当输入实际有标题或正文时，不接受该空候选为成功，应记录语义异常并有界重试，失败时隔离该 Case、保留证据而不阻塞健康消息。先用上述 Case 做离线复现，并分别比较关闭/启用 session cache 与缩短 projection 的效果，再决定根因修复。避免把所有宏观相关消息强行召成候选；R1 最多 3 条，过宽召回也可能挤掉真正贴近边界的 policy。

## 结论边界

- 这不是全部重要消息的完整收集率评估；它针对用户列举的事件簇，以 BE 标题粗筛和少量复合正文复核。统计单位是 Standard Message/Case，并非独立新闻事实。
- 当时的 W2R1 miss 需要消息事实、当时 policy criterion/match scope、冻结输入和 R1 结果四者共同支持。Ameren 与 AI credit stress 不符合“确认漏召”证据；Goldman 与早期 Anthropic 则属于后来新增 policy。
- 真正的高优先级风险是静默空召回的输入理解异常。该 6% 是异常理由占有 R1 结果 Case 的比例，**不是全部重要事件的漏召率**；其中有多少本应命中特定 policy，需要对每条消息按当时版本再人工 adjudicate。

## 第二轮：21 个“未收到消息”Case 的根因追查

### 已定位到的故障层

**可复现的故障是 Qwen 在 W2R1 任务中没有正确识别、绑定当前消息，而后应用把这个错误当成合法的空召回接收。** 长 Policy 参考前缀与很短的当前消息尾部，是本批异常的明显诱因。模型内部注意力机制、隐式缓存是否放大该问题，尚不能完全分离；没有证据证明提供商实际漏传了消息。

本轮使用生产 Scheduler 内的实际 adapter，从原始冻结输入重建发送参数，捕获 `responses.create` 的参数进行检查，并向当前配置的 `dashscope.aliyuncs.com` / `qwen3.8-flash` 做隔离复现。模型、medium effort、原始业务消息、冻结 Policy projection、R1 指令和输出 schema 保持一致，对照只改变请求组织方式；回读探针另有简化的诊断指令和 schema。

- 21 个 Case 的冻结输入和重建请求均包含 `source_message.title`；19 个有非空 body，2 个 body 为空。全部原始输入都能在重建 wire 中找到，未发现应用侧截断。
- 139 条 Policy 的 projection 先序列化为 `Read-Only Business Reference Data`；随后才放 `Current Case Input` 和 `source_message`。21 条请求的 input 字符串约 47,854–48,654 字符，加上约 3,994 字符的 instructions；历史模型回执约 17,565–17,919 input tokens。
- `transport.py:328–347` 的这一组织逻辑由 2026-09-07 的 `e00a3cef` 引入；该段在本次历史窗口前后没有修改。当前实际 Scheduler 中检查到的实现一致。这里的 wire 是依照冻结输入和实际 adapter 重建的，并非当年的 HTTP 抓包。
- 当前生产 `persistent_runtime_v2_session_cache_enabled=false`，请求不发送 session-cache enable header，也不发送 `previous_response_id`。这不等于关闭全部隐式缓存；百炼文档将两者区分，见[上下文缓存说明](https://help.aliyun.com/zh/model-studio/context-cache)。21 个历史异常中还有 2 个 `cached_input_tokens=0`，所以高缓存复用并非必要条件。

### 异常集中在短消息

此前的 352 个 `w2_round1` 结果对象，包含 **24 个 `w2_skipped=true` 的占位空结果**；这些没有冻结的 W2R1 输入或模型 turn。实际有冻结输入和成功 W2R1 turn 的是 **328 个**。以下长度统计来自这 328 个实际调用 Case，长度为 Python 字符数，350 是本批样本的分组界限，不是建议的运行时阈值。

| body 长度 | 实际调用 Case | 历史理由误称缺消息 |
| --- | ---: | ---: |
| 0 | 10 | 2 |
| 1–350 | 49 | 19 |
| 351–1000 | 44 | 0 |
| >1000 | 225 | 0 |

因此全部 21 个异常都落在短消息组：**21/59 = 35.6%**；较长消息是 **0/269**。按全部结果对象是 21/352≈6.0%，按实际调用是 21/328≈6.4%。21 个原始异常分布在 v1（16 个）、v4（4 个）、v6（1 个），均为 COMPLETED，16 个 W1=NEW。

### 真实模型对照结果

完整对照首先调用 21×3=63 次。首次原始格式有 2 次评估脚本直接 JSON 解析失败，末尾提示格式有 3 次同类失败，消息前置有 1 次 API 超时。仅对这 6 个失败请求补跑一次，补跑使用生产相同的窄范围字符串引号修复和输出模型校验，全部得到有效结果。**补跑是新的模型采样，不能反推原失败返回的内容。**

| 请求组织方式 | 首次有效返回中的缺消息错误 | 仅补跑失败请求后，每 Case 一个有效结果 |
| --- | ---: | ---: |
| 原始：长 Policy 前缀，短消息尾部 | 10/19 | **12/21** |
| 同样顺序，末尾加一句明确要求评估 `source_message.title/body` | 4/18 | **4/21** |
| `Current Case Input` 前置，随后原样放全部 Policy 参考数据 | 0/20 | **0/21** |

分类经过理由原文核对；“收到消息但它没有 Policy 相关事实”不计为缺消息。例如投资推荐标题的正常空召回明确提到了实际标题，不能被“未包含具体业务事实”的关键词误算为输入缺失。

另外三个诊断对照：

- **回读探针 6 条：6/6 确认存在 `source_message`，并能回读对应标题及 body。** 4 条标题字符完全一致，2 条仅将弯引号规范化为直引号。探针与 R1 使用相同 input 字符串，只改诊断指令/schema。这证明该格式中的消息可以被模型读取；它不证明真实 R1 已正确使用消息。
- **只拆成两个 user 消息块，保持 Policy 在前：3/6 仍声称没消息。** 单纯拆消息不能作为已验证的修复。
- **原始顺序前加唯一请求 ID，减少前缀复用：1/6 仍声称没消息。** 六条回执的缓存均为 1,024 tokens，约 5.8%，原始完整对照的缓存比例中位数约 98.9%。冷前缀下仍能复现；但加前缀标记也改变了输入文本，不能据此精确估计隐式缓存的因果贡献。消息前置同样损失了大部分缓存复用，因此其成本变化需要纳入后续修复选择。

本轮共 **93 次隔离 provider 调用**：18 次小样本诊断、63 次完整对照、6 次低缓存对照、6 次失败补跑。未把这些结果写回生产 Case、维护或交易链路。

### 为什么会静默完成

`service.py::_run_w2_hot` 的 `validate_r1` 只检查候选 Policy ID 是否属于冻结 projection。`candidate_policy_ids=[]` 始终满足该检查，结构化 schema 也允许自由文本理由。随后空候选直接生成 NORMAL 的 W2 final，并跳过 R2。

所以当前链路没有拦住这个矛盾：**实际输入有标题/正文，模型理由却声称没有输入。** 这解释了为什么 21 个异常都成为 COMPLETED，也解释了为何正常 JSON/HTTP 成功不能视作业务判定成功。

本批确有实质消息。例如 `case_c86c75f6556a499a8960974922aae41b` 的 137 字符正文明确说 Oracle 对 Project Jupiter 发出 force majeure notice；`case_09630c2be55a43c1bbad25d48348bb39` 的 299 字符正文也明确提到该通知。前者历史理由说只有 Policy，后者原始格式复现仍误称无正文；消息前置后能识别实际通知并提出项目/客户承诺候选。它们值得继续按当时完整 Policy 边界判定，而不能因输入不存在而跳过。

还发现另一种事实绑定错误：`case_a255361c827741e29a70c21436764f6a` 的消息讲 Caterpillar 创纪录 backlog，但末尾提示对照却把 Green Chile 管线拒绝/改线/延迟的 Policy 条件叙述成已经发生的新闻，并召回三个相关 Policy。**不再声称缺消息，不代表召回已正确；强制返回非空候选会掩盖这类错误。**

### 修复方向和结论边界

1. 优先修 W2R1 当前消息与参考数据的绑定：将当前消息作为明确的评估对象前置，是这批样本中效果最好的对照。落地时需要兼顾缓存成本；仅加末尾提醒或拆成两个 user 块还不够。
2. 给“输入确实有标题/正文，但空候选理由明确声称消息不存在”增加窄范围语义校验，沿已有有界重试/失败隔离路径处理，避免继续静默完成。不要按 body≤350 一律重试，也不要要求所有消息都召回 Policy。
3. 后续验收同时检查消息事实引用、候选合法性与无关事实幻觉；消息前置 0/21 是一次有限样本中的缺消息错误结果，尚非稳定性或候选正确率验收。

本轮已确认输入对象识别/事实绑定失效及应用校验缺口，已找到有效的请求组织对照。模型内部为何忽略尾部或误用 Policy 条件，仍缺提供商内部证据；不能把根因定为“缓存吞掉 source_message”或纯粹的注意力算法缺陷。本轮交付诊断与复现材料，没有修改生产业务逻辑或配置。

### 可复核材料

- [汇总统计](../eval/persistent_runtime_v2/be_w2r1_missing_source_20261002/summary.json)、[21 个异常与 6 个短消息对照](../eval/persistent_runtime_v2/be_w2r1_missing_source_20261002/cohort.json)、[去重的冻结指令/Policy/wire schema](../eval/persistent_runtime_v2/be_w2r1_missing_source_20261002/frozen-contexts.json)。每条原始 wire 保留 SHA-256；Case 引用三个去重 context。
- [首次完整对照原始返回](../eval/persistent_runtime_v2/be_w2r1_missing_source_20261002/replays-full.jsonl)、[失败请求补跑](../eval/persistent_runtime_v2/be_w2r1_missing_source_20261002/replays-retry.jsonl)、[回读与拆消息探针](../eval/persistent_runtime_v2/be_w2r1_missing_source_20261002/replays-pilot.jsonl)、[低缓存对照](../eval/persistent_runtime_v2/be_w2r1_missing_source_20261002/replays-cold.jsonl)。初版 runner 的 `missing_source` 布尔值是初筛，最终统计以理由核对和 `summary.json` 为准。
- [生产只读抽取/隔离复现脚本](../eval/persistent_runtime_v2/be_w2r1_missing_source_20261002/production_probe.py)：默认 `--mode extract` 只读 DB、捕获 wire，不调用模型；`pilot/full` 才真实调用当前配置 provider。固定历史窗口与模型输入，不用于恢复生产 Case。
- [文件校验清单](../eval/persistent_runtime_v2/be_w2r1_missing_source_20261002/manifest.json)、[352/328 分母追查](../eval/persistent_runtime_v2/be_w2r1_missing_source_20261002/denominator.json)。数据库访问使用 SQLite `mode=ro`，材料中不含凭据。

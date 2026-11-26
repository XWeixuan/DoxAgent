# W3 长尾复盘：INTC 两个精查Case与近两语义日Top10扩样

审计日期：2026-11-19。历史事件均发生于2026-11-18；本文时间统一为UTC，北京时间加8小时。

扩样补充：第10节增加近两语义日10个耗时样本、20条原thread核查。扩样聚焦研判过程与单轮生成长尾；前文结论的适用范围仍是原两个精查样本。

## 1. 核心结论

这两案的长尾包含三个层次：**来源追查缺少停止条件，证据收集结束后仍有52—83秒无新增工具证据的尾部；双分支共识把较慢一支放在最终释放的关键路径；原本用于促进收敛的elapsed提醒在两案中均未实际生效。**

时间提醒缺失有完整因果证据：Scheduler尝试登记Case计时起点时，`w3.time_origin`采用`timeout=0`，两次均未取得写入门锁，抛出`database is locked`；异常被捕获并返回None，业务继续。两案均未留下计时原点，四个模型分支的原生轨迹中均无实际`<w3_time_awareness>Elapsed: …</w3_time_awareness>`片段。因此，**这不是“收到120/180/240秒提醒后拒不收敛”的样本，而是没有收到动态提醒的样本。**

四个决策分支均为`gpt-6-luna / max`，全部正常返回LONG。它们的SDK累计时间约1,093.9秒，外层工具调用至返回的区间合计44.1秒，约占4.0%。其余时间不能全部命名为纯思考，但可以确定：长尾主要不在外部接口响应等待，而在模型生成、服务/传输及回合衔接区间。

另有一个局部技术问题：午间摘要A分支对分页行情的JSON解析与参数使用出错，最终恢复。该分支早于B完成，**它确实浪费了本分支时间，但没有决定本案349.5秒的最终关键路径**。

业务口径上，这两条并不是两个独立的Intel催化：16:40的合作详细稿与16:58的午间综述，最终都围绕微软Azure采用Intel EMIB及多年Xeon供给安排形成LONG。后者重新消费了已经作为PIN注入的详细稿。

本轮只读排查，未恢复/续写原thread、未重跑模型、未修改应用代码、提示词、配置或交易状态。下文的“充分/重复/低增量”是对当时可见证据与最终结果的审计评价，不冒充不可见的内部心理记录。

## 2. 样本、原thread与真实耗时

### 2.1 两个Case

| 消息 | 来源 | Case ID | 最终结果 |
|---|---|---|---|
| Microsoft and Intel expand Azure infrastructure partnership | investing.com，正文标有© Reuters | `case_f05f1f3d6cd54077ae472e6e03757b88` | A/B均LONG，选择B；LIVE意图未执行，`PORTFOLIO_CAPACITY_EXHAUSTED` |
| Midday Fly By: Nvidia reports Q3 beat, Target posts 'beat and raise' | tfb.thefly.com | `case_51c137229f77416eb8699df707cecee7` | A/B均LONG，选择B；LIVE意图未执行，`ENTRY_FAILURE_BUDGET_EXHAUSTED` |

两案均为`dual_consensus`，结果记录的选择原因为`LONGER_TASK`。任务状态均为succeeded/COMPLETED，没有触发600秒SDK硬超时。“严重超时”在本文指业务研判明显偏长；不能把它写成任务因TIMEOUT技术失败，也不能把零成交直接归因为研判慢，导出中的实际未执行原因如上。

### 2.2 四条原thread

| Case / 分支 | 原thread | 原生turn |
|---|---|---|
| 合作详细稿 A | `01a1ef87-cb35-79d2-8627-8f57b8777419` | `01a1ef88-8a56-7c50-821e-bf83c945157a` |
| 合作详细稿 B | `01a1ef87-d51a-7db1-86d9-87c77ca47959` | `01a1ef88-8afa-7aa0-8af0-126e89bf2161` |
| 午间摘要 A | `01a1ef9a-594a-72c0-8085-504997289ac8` | `01a1ef9a-b137-7473-b96a-b530529c1814` |
| 午间摘要 B | `01a1ef9a-6121-7101-8518-01b3c2e9705e` | `01a1ef9a-b17d-7641-b7fb-7138721fa618` |

四条均从公共母thread `01a1e1f9-3799-7531-8336-e3eb630d8de5` Fork。它们是分别启动的Case分支，后一个Case没有继承前一个Case的完整调查过程。四个目标决策回合均未发生上下文压缩，故本轮没有“中途压缩遗忘导致重查”的证据。

### 2.3 时间拆分

| 区间 | 合作详细稿 | 午间摘要 |
|---|---:|---:|
| 接收 | 16:40:42.924 | 17:00:34.255 |
| 最早SDK派发 | 16:41:05.357 | 17:00:54.961 |
| 接收到SDK派发 | 22.43秒 | 20.71秒 |
| 两支并行SDK覆盖区间 | 231.60秒 | 349.64秒 |
| 最后一支SDK返回到决策持久化 | 0.71秒 | 0.63秒 |
| 决策持久化 | 16:44:57.663 | 17:06:45.231 |
| 接收到决策持久化 | 254.74秒 | 370.98秒 |
| 快分支完成后，等待慢分支 | 5.59秒 | 62.56秒 |

SDK前的约21—22秒包含W1/W2及路由准备，不能统称为排队。四支Worker记录的queue_wait为246—361毫秒，cold_start为193—283毫秒；这些不是本次几分钟长尾的主要来源。导出里的“非模型调用延时约3秒”与“接收到W3 SDK派发”也不是同一口径，因为后者包含一轮模型处理。

合作详细稿的后置Operations在16:46:05.139完成，午间摘要在17:06:56.274完成；均在决策提交之后。本文不把Operations耗时塞回Trade Decision，也不将A/B并行时间相加作为单案延迟。

| 分支 | SDK秒 | 外层工具区间秒 | 最后工具返回到最终输出秒 | reasoning tokens |
|---|---:|---:|---:|---:|
| 合作详细稿 A | 225.97 | 21.76 | 62.62 | 9,837 |
| 合作详细稿 B | 231.35 | 5.40 | 82.59 | 11,464 |
| 午间摘要 A | 287.06 | 3.50 | 73.59 | 13,025 |
| 午间摘要 B | 349.54 | 13.49 | 52.23 | 16,856 |

“工具区间”按原生custom_tool_call到对应output的时间计算，包含Code Mode包装。最后一列是模型使用回执，**不代表其内部推理内容可以读取**；也不能据此将全部剩余耗时量化为纯推理。没有做同输入、不同effort/tier的受控对照，本轮不声称改低effort能节省某个确定比例。

## 3. 原始summary能提供什么

已检查四支的原生`turn_context`、reasoning条目、Worker `audit/turn_summary.json`、`sdk_loop.jsonl`和最终四字段决策。

- 原生配置均为`summary=none`，可读reasoning summary均为空；合作A/B分别有22/24条reasoning记录，午间A/B为31/37条。条目数量不是可读思维过程。
- Worker的`turn_summary.json`包含模型、最终状态、usage、工具状态及耗时等技术摘要，不是业务隐性推理摘要。
- 最终`prior_expectation / expectation_delta / reason`、已注入材料及工具轨迹，可以支持下面的决策逻辑重建；无法确定每段无工具时间内模型逐句想了什么。

## 4. Case一：16:40合作详细稿的研判与长尾

### 4.1 它接到的事实和缺口

正文已完整提供：微软首次宣布在Azure第一方硅中使用Intel Foundry的EMIB先进封装；多年Xeon供给能力涵盖Diamond Rapids；CXL、QAT和机密计算有具体Azure应用及性能数据。没有采购最低量、价格、EMIB规模、爬坡日期或财务贡献。

W1说明此前只知一般Azure/Xeon合作扩展，本稿补出具体合作条款；W2为RELATED，并未HIT。两支最后都将“具名大型云客户具体采用”赋权为Intel外部封装商业化的重要验证，认为尚未量化的经济规模不阻止概率层面的正向修订。

它们实际要处理的是三类问题：新EMIB采用与既有合作的差别；封装采用是否被误写为晶圆代工或最低采购保证；联合公告/转载的时间关系及行情是否提供完成态否决。这些问题有合理业务目的。但本稿已给足绝大部分条款，补充研究应以缺少哪项会改变选择的信息为边界。

### 4.2 A分支：从补首发走向重复查同一条公告

A约22秒开始Web研究，主题先是Microsoft/Intel联合公告与EMIB采用；随后改用“first company”“Azure first-party silicon”“2.5x SQL backups”等精确句，尝试取得原始公告、区分技术功能与新合作；再打开已有Investing链接，因网页不可访问未补出新正文。

搜索主要返回Ignite预告、已有CXL/Flat Memory Mode资料、旧Azure说明和泛化Intel材料。这些内容可帮助识别某些功能不全是本次首次出现，但没有取得新的合作规模、原始精确时分或改变最终方向的反证。

约101秒已调用行情，取得本次截至16:35的完成bar及16:40形成中bar，事件补充为PREPARING。行情未知被保留，没有等待后续bar完成，这是正确的非阻塞处理。

随后仍在约157秒再次搜索Reuters/Investing精确标题及16:40时间，返回泛化聚合页和无关材料。最后约63秒没有新增工具证据才提交。最终理由仍引用输入稿件的时间和条款，未采用新查到的独立事实。

**延迟的性质：**开始核对首发有必要；多轮同义改写后仍未得到目标信息，继续搜索未改变依据，却没有稳定的停止条件。它最后接受的证据边界，早在本轮之前已经可以明确表达。

### 4.3 B分支：核验较早完成，后续仍持续评估

B约51秒就查询行情。约106秒才开始搜合作标题与官方新闻稿，约146秒继续用M-series v4、CXL、2.3x、QAT和EMIB精确组合搜Microsoft/Intel站点；后一轮为空结果。

最终业务判断与A一致，且没有依赖这些后续搜索带来的新条款。最后一次工具约149秒返回，约231秒才输出，中间约83秒没有新证据。

**关键区别：**B工具响应合计仅约5.4秒，比A少得多，整体却稍慢。因此不能用“多调用几次Web接口”解释全部耗时；较长的模型回合与最终输出前尾部同样重要。可以确认它较早取得拟交易所需的行情局限，但不能因summary为空便断言它在那个时点已经内部完成LONG认知。

### 4.4 这一案的额外时间有没有买到更好的结论

从可见证据看，两支没有拿到新的量化商业条款或独立首发时间，最后仍依赖输入的客户采用事实与16:40标记。合理的工作包括辨明EMIB与晶圆代工、承诺容量与最低采购、当前行情未知；低增量部分是继续追搜已经完整注入的公告和其同义表达。

因此，本案不支持“为了必要的一手核验，花四分钟是不可避免”的解释；也不能仅凭最终LONG就认定所有搜索无意义。较有把握的判断是：研究收益已经递减，剩余缺口并未按其对决定的价值管理。

## 5. Case二：17:00午间摘要的研判与长尾

### 5.1 它实际交易的是哪条消息

标题和W1的新增重点涉及Nvidia业绩与指引，但当前正文关于Intel只有一般合作扩展的一句。W3最终没有用Nvidia财报机械读穿Intel，而是从注入PIN中取出16:40的完整Azure合作稿，以EMIB和Diamond Rapids条款形成LONG。

两支均识别16:58综述未新增Intel核心事实，最终按16:40详细稿计算披露时间。这一点好于将16:58当作全新起点。但它们仍重新调查同一合作事件，而不是继承首案已经完成的研究结论。

两个Case的`recent_trade`均为空；当时入口定义该字段只包含截点前48h实际ENTRY成交。首案是未成交意图，因此不会进入该历史。第二案拿到了原详细稿PIN，却没有拿到第一案A/B已完成的调查链路。这里描述的是当前输入和机制，不主张把未成交意图重新混入实际成交历史，也不把PIN本身等同于已交易。

### 5.2 A分支：来源没补出信息，行情分页反而引入修复循环

A先在内部Research中搜`Intel Foundry Azure Xeon EMIB`，得到空结果。该搜索是整串字面匹配，不是语义搜索；空结果不证明没有这份研究。它没有转为按已知对象读取，随后用Web查联合公告和Reuters归属，仍未取得新合作事实。

约138秒进入行情核验。接下来出现了一个真实技术弯路：

1. 行情正文通过`body`字符串分页返回；A在单页尚未完整时直接`JSON.parse(body)`，约161秒报`Unterminated string`。
2. 后续尝试逐页解析并吞掉异常，输出`bars: []`。这不是市场没有bar，而是解析方案没有得到完整JSON。
3. 又请求`max_bytes=1000`，低于工具1024下限，被拒绝；再读取小片段检查响应。
4. 约213秒终于先拼接各页`body`、再解析整份JSON，成功取得完成态所需行情。

首次行情调用到正确拼页结果跨度约76秒，包含脚本修复、模型生成和工具交互；不能全算外部API等待。成功后仍过约74秒才输出LONG。

这是应单独修复的协议使用问题：工具的快响应并没有阻止模型在错误的数据组织方式上消耗多轮。它最终恢复，且输出没有把暂时的空bars当成业务事实。

### 5.3 B分支：反复找原始披露，295秒仍新增搜索

B的主题始终围绕原合作披露：先查发布时间和Intel/Microsoft联合稿，再查Microsoft Source精确标题。早轮搜索确实从Reddit转述中取得了Microsoft原公告URL，随后打开该URL失败；之后继续查Reuters、Diamond Rapids、EMIB及Investing同名稿。这不是盲猜网址，找到原始出处也是实际进展，但没有取得原页面的精确发布时间或新的商业条款。

约74秒内部搜索`Intel Azure EMIB Microsoft foundry`也为空；约143秒再搜Reuters及精确时间，仍主要返回Ignite、其他公司及泛化材料。它没有转向研究Nvidia或市场全景，问题是同一个来源/时间问题缺少结束边界。

约204秒才完成行情核验。此后约295秒又进行一轮Web搜索，主题仍是Investing原题、Reuters原题和“Azure first-party silicon”。结果没有补出改变最终选择的新信息。最后一轮返回后又过约52秒，才在349.5秒提交。

**这支是本案实际关键路径。**A的行情修复尚未让全案结束，因为B到295秒仍在调查；A完成后，全案又等B约62.6秒。不能把A的技术错误当作本案全部根因。

### 5.4 结果是否因长研究而改变

两支最终都选择与首案一致的LONG，依据仍是PIN内微软EMIB采用和多年Xeon安排；都承认规模未披露，并称无明确行情完成态否决。长研究没有带来新的商业量级、不同的首发时间锚或方向修正。

按实际提交时刻计算，原16:40:02披露到第二次意图已经约26分43秒；最终reason仍主要用冻结截点时的“约20分钟”陈述。不是说超过某固定分钟数必然不交易，而是模型新增的近六分钟耗时也应进入当前机会判断，不能仅复述启动时的新闻年龄。

## 6. elapsed及短提醒：核验到哪一层，失效发生在哪里

### 6.1 静态规则确实存在，动态提醒确实缺失

本轮读取了四支共同母thread的原生记录。母thread已在2026-11-16注入公共AGENTS及Skill，其中明确说明Elapsed是Case实际研判总耗时、含准备和等待，重试不重置；出现短提醒时应收敛，要求停止调查时仅完成必需核验并提交。该规则与两案冻结资产相符。

因此，不是Skill没告诉模型如何理解Elapsed。**静态的“将会收到提醒”说明，与实际收到一条带当前秒数的动态提醒，是两回事。**

| 层级 | 两案实际证据 | 判断 |
|---|---|---|
| Scheduler开始计时 | 两案均尝试`w3.time_origin` | 功能路径被调用，不能归因总开关未打开 |
| 持久化起点 | 两案均报错；`runtime_values/w3_time_awareness`无这两个Case | 原点登记失败 |
| 请求构造 | 实现只读取已保存原点；失败返回None，没有本次成功起点可传 | 动态计时链路在上游已断开 |
| Worker开关/运行时 | 当前核查均开启，指向定制Codex runtime | 不能仅凭当前开关声称历史请求已携带计时 |
| 四个原生decision回合 | 实际Elapsed片段均为0 | 动态提醒未送到模型上下文 |
| 注入后行为变化 | 没有注入前后样本 | 本次不具备评价提醒行为效果的条件 |

### 6.2 两次原点失败的精确证据

合作详细稿：

```text
2026-11-18 16:41:04,690 WARNING ... W3 time origin unavailable for case_f05f1f3d6cd54077ae472e6e03757b88
w3_time_awareness.py:22  journal.transaction(operation="w3.time_origin", timeout=0)
journal.py:223          raise sqlite3.OperationalError("database is locked")
```

午间摘要：

```text
2026-11-18 17:00:53,949 WARNING ... W3 time origin unavailable for case_51c137229f77416eb8699df707cecee7
w3_time_awareness.py:22  journal.transaction(operation="w3.time_origin", timeout=0)
journal.py:223          raise sqlite3.OperationalError("database is locked")
```

`journal.py:223`是写入门锁尝试失败、达到deadline后的主动抛错位置；两条栈不是在`BEGIN IMMEDIATE`执行处报错。与当时boundary trace中只有`journal.gate`、没有对应`journal.sqlite_begin`相互印证。故这里能定位到**零等待的写入门锁竞争**，不能把异常名字扩写成“SQLite事务把模型阻塞了数分钟”。本轮未进一步确认哪一个其他写入者占用了门锁。

函数捕获`sqlite3.Error`后返回None，以避免可选计时影响业务结果；随后请求构造只是读取原点，不会在模型循环中自动补出已丢失的Case起点。结果是数毫秒级的登记失败，撤掉了这两个Case全部四支的软性时间约束。

### 6.3 120/180/240秒之后的行为应如何解释

仓库runtime补丁定义120秒提醒检查充分性、180秒提醒仅继续高价值缺口、240秒要求停止新增调查并完成必要核验。它在新模型请求前注入，不是在一次长生成流进行中每秒抢占。

| 分支 | 可见后续行为 | 本轮允许得出的结论 |
|---|---|---|
| 合作A | 157秒继续同标题/时间搜索；最后工具后仍63秒输出 | 有超120秒后的低增量研究与输出尾部；没有收到提醒，不能定性为无视提醒 |
| 合作B | 146秒继续精确关键词搜索，返回空；随后83秒输出 | 研究已低增量但仍持续；不构成“注入后行为未变”的实验 |
| 午间A | 180秒后主要在修复行情分页，213秒才取全；随后74秒输出 | 存在必要核验的技术修复，也有修复后尾部 |
| 午间B | 295秒仍新增同一合作来源搜索 | 若有240秒停止调查提示，这应是重点检查的行为；本案提示缺失，不能认定违反已收到的指令 |

四个分支没有任何可用于计算Elapsed偏差的真实数值，因此不能报告“秒数准确/漂移多少”；也不能凭本样本估算修好注入后能减少多少秒。需要后续受控复现或自然样本验证，不能用本轮的事后推断冒充效果测试。

## 7. 共性延迟机制

### 7.1 来源追查的目标合理，研究停止条件不稳定

本轮与此前完全不查首发的badcase不同：四支实际都在查联合公告和传播时间。问题是未找到目标原件后，反复用标题、日期、EMIB、Diamond Rapids、CXL/QAT和官方域名组合搜索；最终却仍接受输入正文及其时间作为交付依据。

需要解决的不是“禁止核查时间”，而是明确哪项未解时间关系会改变当前选择。如果最终只能保留输入所给时间范围，应在适量、定向补查后交付这个边界；不能无止境追查，同时又不让没有查到的信息影响决定。

### 7.2 最后工具之后的收敛成本，与工具数量同样重要

四支最后工具返回到输出分别约63、83、74、52秒，累计约271秒。四支reasoning tokens为9,837—16,856，且均为max。可见这一实现允许较长的评估和输出生成区间，即使没有任何新资料进入。

无法从空summary判断它们是否在反复权衡同一论点，也无法将服务排队和传输完全拆掉。因此本报告以“无新增工具证据的尾部”定位，不声称全部是模型纯思考；但单纯优化Web接口速度显然不能覆盖大部分延迟。

### 7.3 双分支保留了慢支风险，不会自动促使收敛

两支并发，现有实现等待两票。方向一致后选用较长任务的完整结果，不增加第三次综合模型调用。因此真实Case耗时接近`max(A,B)`，不是`A+B`。

合作稿A/B都慢，双票额外等待只有约5.6秒；午间摘要B明显更慢，A完成后又等约62.6秒。**仅将最终文案改选快支不会消除必须等第二票的时间。**若要改变提前释放或共识规则，需要另行明确业务授权，本轮没有实施，也不把它作为可无风险删除的等待。

### 7.4 同一催化跨Case重复恢复认知

首案两支从公共模板出发，次案又两支从公共模板出发。第二案拥有详细稿PIN，却没有首案完整的调查结论与时间边界；未成交意图又按当前合同不进入recent_trade。于是同一商业验证被重新做了四遍来源、时效和行情检查。

这不证明应该跳过独立时效或行情核验，也不证明应自动重试首案订单。更合适的优化对象是复用带有时间/来源身份的事实调查结果，同时为新Case独立评估剩余增量与当前机会，避免重复下载和重建同一披露关系。

### 7.5 软件故障影响了收敛保障，而非直接制造分钟级阻塞

时间原点的零等待失败取消了全部提醒；行情分页契约使用错误引出模型修复循环；内部搜索使用整串词导致空匹配。它们共同增加了模型在不完整反馈下自行重构流程的负担。

其中只有午间A的行情错误直接落在一个分支的调查过程；计时失败则是两案共同且已证实的保障缺口。不能把三者混成“后台慢”，也不能说只有模型过度保守而忽略软件链路。

## 8. 建议的后续动作与验收边界（本轮未实施）

1. **优先恢复计时原点的可靠传递。** 保留不因辅助计时导致业务失败的原则，但避免一次零等待登记失败就让整案失去提醒；可评估利用已有Case事务/持久化执行快照记录原点，或有界降级传递。需保持两支共享Case原点、重试不重置、Operations不注入。
2. **验收真实模型输入，而非只验开关。** 使用这两案做只读记录级回归：原点存在、请求包含上下文、每支native轨迹实际出现Elapsed及应有文案。故意制造写锁竞争，证明降级不会让提醒静默消失；这是修复后的验收建议，本轮未制造生产锁。
3. **将来源研究按“是否改变选择”收敛。** 针对同标题原稿、精确时间、已有全文重复搜索验证停止行为；允许必要的首发追查，避免把来源不可得等同还应无限继续。评估无工具尾部，不能只计搜索次数。
4. **固定行情分页的正确消费方式。** 先拼接全部`body`片段，再解析JSON；遵守1024字节下限。可评估提供更易直接消费的完整小视图，减少让模型临时编写解析器；不要把技术空数组写成行情不存在。
5. **保留共识规则下优化每支。** 先让两支都有可靠提醒、停止重复来源研究，再量化还剩多少慢支等待；不在本轮擅自改变双票语义。
6. **另行验证提醒的行为效果。** 修复后观察120/180秒后是否还新增低价值调查、240秒后是否停止新增调查，以及最后工具到提交的尾部是否缩短。若做受控模型重放，应冻结当时输入、证据及可用工具，区分提示效果与实时Web波动。

本轮没有证明LONG方向本身正确，也没有证明所有延迟都使交易失去价值；没有进行历史价格/P&L反事实。已完成的结论是：长尾发生在哪些实际阶段、补查有没有改变证据、时间提醒为何缺失，以及双分支如何将慢支带到业务提交端。

## 9. 证据入口

- [用户原导出快照](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/doxagent-INTC-runtime-cases-20261118T222837Z.json)。
- [生产Case、四支Worker回执、W3阶段与thread登记](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/remote_records.json)；[分阶段时间与阈值后行为统计](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/metrics.json)。
- [原生回合、模型、summary、工具时间和提醒计数](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/thread_analysis.json)。
- 合作详细稿A：[搜索主题及最终决定](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/reviews/partnership_A_research_and_decision.txt)，[对应工具原始返回](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/reviews/partnership_A_outputs.json)，[完整原thread快照](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/threads/01a1ef87-cb35-79d2-8627-8f57b8777419.jsonl)。
- 合作详细稿B：[搜索主题及最终决定](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/reviews/partnership_B_research_and_decision.txt)，[对应工具原始返回](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/reviews/partnership_B_outputs.json)，[完整原thread快照](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/threads/01a1ef87-d51a-7db1-86d9-87c77ca47959.jsonl)。
- 午间摘要A：[搜索主题及最终决定](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/reviews/midday_A_research_and_decision.txt)，[对应工具原始返回](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/reviews/midday_A_outputs.json)，[完整原thread快照](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/threads/01a1ef9a-594a-72c0-8085-504997289ac8.jsonl)。
- 午间摘要B：[搜索主题及最终决定](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/reviews/midday_B_research_and_decision.txt)，[对应工具原始返回](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/reviews/midday_B_outputs.json)，[完整原thread快照](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/threads/01a1ef9a-6121-7101-8518-01b3c2e9705e.jsonl)。
- [合作稿计时起点失败栈](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/partnership_origin_failure_exact.txt)；[午间稿计时起点失败栈](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/midday_origin_failure_exact.txt)；[生产Scheduler计时与门锁源码定位](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/scheduler_code_evidence.json)。
- [公共母thread：静态Skill实际注入记录](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/threads/01a1e1f9-3799-7531-8336-e3eb630d8de5.jsonl)；[Worker审计summary、sdk_loop及读取时部署源码](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/worker_audits_and_code.json)。
- 实现定位：[Case计时原点](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/w3_time_awareness.py:15)；[写入门锁与零等待](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/journal.py:205)；[请求读取时间上下文](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/w3.py:1723)；[双分支结果选择](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/w3_consensus.py:4)。这些是本地定位入口，生产对应源码及hash另保存在上述证据文件中。

## 10. 扩样：近两个语义日中W3决策耗时最长的10案

### 10.1 抽样口径与结论修正

保留前文两案为精查样本；本节额外逐案检查10个Case、20条原生决策轨迹，深度以识别共同机制为主。各案需结合原生轨迹判断研究内容和实际形成结论的时间。

- 窗口：DoxAgent语义日 **2026-11-17、2026-11-18**，语义日从美东02:00起算；对应UTC 11月17日07:00起。截面为 **2026-11-18 22:50:52.634 UTC**，11月18日尚未结束，不能称为两个完整交易日。
- 母体：2,286个有Trade Decision阶段记录的Case；其中2,240个具备当前阶段关联的实际SDK耗时，可排名。另46个FAILED Case未关联Worker job，不能把它们当作零耗时，也不能凭状态判成600秒模型超时。
- 排名：每Case取当前保留的A/B决策job中最大的`telemetry.sdk_duration_ms`。不是A+B累加，不包含Operations，也不是新闻进入到交易执行的全链路时间。原精查两案没有占用本次额外10个名额。
- 排名依据当前阶段保留的决策job，不覆盖已替换的历史尝试。
- 20支均为`gpt-6-luna / max / summary=none`；本轮决策段均无可读reasoning summary，也无compaction。以下通过实际输入、公开工具请求/返回、提醒与最终结构化结论重建逻辑；token数不能证明不可见推理的内容。

**10案最终均为NO_TRADE。** 因而“W3只有在考虑交易时才会变慢”不能作为普遍解释。简单的行业评论、不相关政策消息，同样会因运行层和双分支机制挂在长尾里。

### 10.2 十案总览

下表来源是消息记录中的`source_name`；Google News为入口，标题中的Tech Critter、Crypto Briefing为发布方。耗时均为秒，A/B不是承诺同时开始。全部标题保留，便于前端直接检索。

| 排名 / 日 / ticker | 新闻title与消息源 | A / B SDK秒 | 主要机制 |
|---|---|---|---|
| 1 / 11-17 / INTC | Taiwan urged to pursue affordable AI, as Jensen Huang's Nvidia exit adds uncertainty to AI hardware standards<br>来源：digitimes.com | 538.3 / 589.7 | 未发现反复研究；A约29秒已形成结论 |
| 2 / 11-17 / COHR | Taiwan urged to pursue affordable AI, as Jensen Huang's Nvidia exit adds uncertainty to AI hardware standards<br>来源：digitimes.com | 585.2 / 26.6 | 核验合作基线；A约76秒已形成结论 |
| 3 / 11-17 / BE | SK Hynix links partner park, Trinity Fab to accelerate supplier commercialization<br>来源：digitimes.com | 583.5 / 577.5 | 核验BE与厂区供电联系；收到提醒后未再使用工具 |
| 4 / 11-17 / INTC | HBMs beyond Nvidia: Why Samsung has the most to gain from Jensen's exit<br>来源：digitimes.com | 83.4 / 582.2 | 核验Intel AI基线；大量背景读取未改变结论 |
| 5 / 11-17 / INTC | RM9,999 nets you the new ASUS TUF Gaming T700TL prebuilt with Intel Core Ultra 9 + RTX 5060 Ti - Tech Critter<br>来源：news.google.com | 566.6 / 536.7 | 核验单款产品配置；未形成公司级预期差 |
| 6 / 11-17 / INTC | 中 이구환신, 가전 넘어 스마트홈으로…보조금·강제표준 동시 추진<br>来源：etnews.com | 547.0 / 8.4 | 无工具，政策与Intel缺少具体传导 |
| 7 / 11-17 / BE | 美國超大規模資料中心耗能與碳排驚人，一年排放量直逼丹麥<br>来源：technews.tw | 28.0 / 538.7 | 无工具，历史耗电数据缺少BE订单传导 |
| 8 / 11-17 / MU | Sandisk's Earnings Face a New Risk in a Key Chip Market<br>来源：yahoo | 22.6 / 526.0 | 首发/分市场验证不断扩张，末工具后104秒 |
| 9 / 11-18 / INTC | Intel signs first outside customer for 18A process as yields reach 70% - Crypto Briefing<br>来源：news.google.com | 448.9 / 343.4 | 来源与首次披露追查反复，末工具后181/82秒 |
| 10 / 11-18 / MU | Micron at TomorrowX Summit: ai memory demand reshapes outlook<br>来源：investing.com | 150.2 / 351.1 | 141秒提醒后单轮生成约212秒，无新增工具 |


### 10.4 逐案定性拆解

#### 1. INTC｜Taiwan urged to pursue affordable AI…

业务问题是“台湾官员对Nvidia领导层及硬件标准的不确定性评论，是否改变Intel联合产品、需求或份额”。A不查工具即否定具体传导；B查询黄仁勋离任时间、官方Nvidia/Intel合作基线，最终认为离任此前已披露，报道没有新增Intel订单或条款。核查基线的目的合理，未形成不断扩大的研究树。

当前数据库的timer origin晚于两支本次执行开始；两支原生提醒均为0。与第2案对比，两者均因缺少公司级新传导而NO_TRADE。

#### 2. COHR｜同一篇Taiwan urged to pursue affordable AI…

判断链是“行业评论→能否改变Nvidia对COHR的光学采购/投资承诺→没有具体新增变化”。A查询官方Coherent合作与Nvidia CEO交接公告，B直接使用已提供背景；两支一致认为，报道新发布不等于公司事实新出现。研究内容聚焦，有明确不交易出口。

A约76秒形成原生结论，工具区间合计约10秒。A收到9/39/53秒提醒，B收到9秒提醒；模型结论在180秒前已经输出，不能据此评价其对180秒提醒的反应。

#### 3. BE｜SK Hynix links partner park, Trinity Fab…

它要确认半导体供应商园区/测试平台是否意味着Bloom现场供电订单。Web搜索针对Bloom与SK hynix/Yongin的电力联系，最终材料只有供应商商业化、氖气回收和工艺测试，未建立Bloom参与。搜索的理由是排除潜在的厂区供电传导，但一轮未发现具体联系后即可结束，没有必要把半导体建设自动映射成BE需求。

A/B分别收到577/571秒停止调查提醒，此后没有再开工具，约36/12秒后输出NO_TRADE；观察结果与收敛方向一致，但不能据此证明提醒造成了收敛。

#### 4. INTC｜HBMs beyond Nvidia: Why Samsung has the most to gain…

B搜索HBM需求转移与Samsung受益，A在后续保留的尝试中读取D2 Gaudi/Intel AI基线。A先尝试不存在的已存分页，再遍历多页，输出达到截断级别；其目的在于验证非Nvidia需求是否流向Intel，但最终报道明确提到的是Samsung、Google、AMD，缺少Intel订单或份额变化。

A额外读取大量背景仍没有改变结论；两支均无实际提醒。当前保留的A/B启动时间不同，不能直接将SDK最大值当作Case总耗时。

#### 5. INTC｜RM9,999 nets you the new ASUS TUF Gaming T700TL…

业务上是“一款马来西亚游戏整机上市，能否推翻Intel客户端供需基线”。A核查ASUS官网配置和价格，B没有工具，最后都认为单SKU没有销量、订单、交期或广泛OEM配置变化，不能推导Intel整体需求上修。此处核查产品真实性有小幅增益，交易性判断并不需要广泛研究。

A核验官网后收到551秒提醒，此后没有再查工具，约27秒输出结论；B没有调用工具。

#### 6. INTC｜中 이구환신, 가전 넘어 스마트홈으로…

消息新增中国智能家居补贴和强制互联标准，电脑仍在既有补贴范围；没有Intel订单、份额或产品要求变化。两支不调用工具即NO_TRADE，说明输入足以排除ticker传导。B约8秒完成，A547秒；A只有初始30秒提醒，期间没有下一次模型请求触发新提醒。此样本直接反驳“工具很多才慢”以及“无意交易就一定很快”。

#### 7. BE｜美國超大規模資料中心耗能與碳排驚人…

两支都从输入判断：68—99 TWh是历史行业用电/碳排估算，不是新增前瞻投资、电力采购或Bloom合同。无工具，也没有靠长研究排除什么重大矛盾。两支均未收到动态提醒。

#### 8. MU｜Sandisk's Earnings Face a New Risk in a Key Chip Market

这是有实质研究长尾的样本。核心本来是：**NAND现货跌约20%、GF下调2027价格预测，是否构成新鲜且足够大的MU盈利下修**。B先查TrendForce/GF来源，区分现货与合同、消费NAND与企业SSD，再扩展到中国CSP削单与北美需求，追原帖发布时间，并在较晚阶段查看行情和分页，最后还回头检索旧预测的+6%/+3%基线。

调查动机有业务必要性：单凭Sandisk标题不能直接对MU下结论，NAND只占其部分业务，DRAM/HBM可能抵消。但最终NO_TRADE的强约束其实是**同一GF预测修订已在PIN #2于15:37Z报道，TrendForce帖子也更早公开；当前稿没有新MU订单、合同或盈利事实**。现货/合同及分业务核查完善了风险解释，却没有推翻已知重复披露这一出口。

A不调用工具，22.6秒就给出NO_TRADE；B526秒、38,540 reasoning tokens，工具外层合计26.9秒，末工具后仍104秒。B在约122—189秒扩展企业SSD/CSP分支，约324—367秒才看行情/分页，约420秒又回查旧预测。**价值较高的是早期辨别NAND与MU整体、核实重复披露；后半段在不交易出口已经很强的情况下继续补全行业图景，增量显著递减。** 两支timer origin均缺失、提醒为0；本案缺失的确切登记异常未逐条补齐，不能直接把原两案的写门锁栈套上来。

#### 9. INTC｜Intel signs first outside customer for 18A process as yields reach 70%…

研究起点合理且重要：首次外部客户可能是商业化里程碑，需要分清18A/18A-P、Socionext已知开发合作、真实生产承诺与70%良率传闻。A/B都尝试找Crypto Briefing原文、Google包装链接、Reddit转述及Intel CTO社媒，反复变换“signed first external customer”查询，并用社媒ID解码时间。

最后承认CEO/CTO签约表态偏利好，但**最迟16:48 UTC已公开，20:10标题没有新增客户、规模、量产窗口或合同信息**，至研判截点约3小时26分，故NO_TRADE。70%良率没有Intel正式披露背书。这里的来源与时效核查不是无意义：它决定是否把一个新标题误当首次商业信号。

过度之处在于相同事实的找原文循环迟迟不停止。A约204秒完成社媒ID时间解码，后面又打开来源、点击原帖，到约265秒停止工具，随后还需181秒才交卷；B也反复追同一引文和原帖，最终末工具后82秒。外层工具各仅34/29秒。**一旦已确认“至少数小时前公开、当前稿无新事实”，继续追求更完美原文并未改变不交易约束。** 两支均无timer origin和实际提醒，不能评价为“收到180秒提醒仍故意继续”。

#### 10. MU｜Micron at TomorrowX Summit: ai memory demand reshapes outlook

本案不是主要追新闻时间，而是拆“旧数字重申”和“新长期技术展望”。A搜索75%产量售出、26份SCA/320亿美元承诺是否早已披露；B聚焦3D DRAM路线图、主要客户共同开发意愿和FY2027增长基线。最后两支都认为：已售出产量与SCA多是旧事实，3D DRAM的2028—2029进入市场、2030—2031扩大采用是定性远期展望，没有新订单或量化经济影响。

这些核查有助于避免把峰会报道的44秒新发布时间误当新需求。主要长尾发生在**证据取齐以后**：B最后工具约在开始139秒前后完成，141秒提醒随后注入，此后没有新工具，直到351秒才输出。外层工具合计9.7秒，最后工具至final211.6秒；A只用一次Web，150秒就得出同一结论。

这提供了比“提醒没注入”更进一步的机制证据：**提醒确实注入并且数值正确，仍不能保证输出及时。它只约束下一轮请求的上下文，不能中断已经开始的单轮生成。** B收到141秒的收敛提示，并没有收到180/240秒提示，因为之后一直没有新的模型请求边界；不能把“没有240秒提示”直接归为注入组件漏发。

### 10.5 elapsed注入：覆盖、数值与行为必须分别判断

20支中，**10支**原生轨迹有动态提醒，分布在第2、3、5、6、10案；另10支没有。提醒的整数elapsed与现存有效origin对比误差最大0.679秒，处于整数截断及记录时差范围，未发现分钟级算错或倒退。

无提醒还需要再分：第8、9案当前就没有timer origin；第1、4、7案现在能查到origin，但其登记时间晚于部分或全部目标job的执行开始，原生轨迹仍为0条。**“事后有origin”不能证明目标回合当时收到了提醒。** 第4案A虽晚于现存origin启动，也未收到提醒，原因需进一步查该次请求物化及上下文传递，不能仅用origin的先后关系补写因果。

收到提醒的行为也不相同：第3案两支和第5案A在571/577/551秒的停止调查提示后均没有新工具，随后十余至数十秒交卷，符合收敛方向；但不能由此证明提醒造成了收敛。第6案A仅收到起始30秒提示就进入长段生成，第10案B收到141秒提示后又持续约212秒无工具生成，表明**动态提醒是请求边界上的软约束，不是独立墙钟定时器，更不是180秒时能强制拿到结论的执行保障**。

### 10.6 对原方案的补充：研判与时间提醒的后续方案

| 机制 | 本轮证据 | 后续排查/改进方向 | 验收要看什么 |
|---|---|---|---|
| timer origin及传递缺失/过晚 | 原精查两案有精确写门锁失败栈；扩样第8/9缺origin，第1/4/7无原生提醒 | 区分登记可靠性、请求物化时刻、继承传递；确保业务成功不能掩盖时间约束失效 | 冻结输入与原生轨迹均能证实提醒存在，起点正确；不只检查数据库有记录 |
| 低增量研究循环 | 原精查的来源追查；扩样第8的行业分支、第9的重复首发查询、第4A的大量基线读取 | 以会改变LONG/SHORT/NO_TRADE的未解问题为停止条件；已确认重复披露/无公司级新传导时收敛 | 同样证据下更早结束，保留必要时效核验；不是简单限制搜索次数 |
| 单轮生成与双分支释放长尾 | 第10B收到141秒提醒后仍212秒；第6A无工具547秒；第8A22.6秒但仍等B526秒 | 先把单轮请求耗时、模型服务/传输、最终结构化输出与双支等待单列；评估可实现的外部截止/降级策略，另行决定是否改变共识要求 | 180秒实际能获得可提交结果；不能只证明180秒提示文本已写；质量与一致性需另做受控验证 |

以上仍是后续方案，**本轮没有实施修复、改共识规则、强制重跑或产生交易**。本次扩样的目标是解释共性机制，不据20条观察数据宣称120/180秒强制输出必然保持正确。SDK耗时不能直接用于推算模型在180秒时的认知成熟度。

### 10.7 原thread与可复核证据

以下均精确匹配本案标题、对应job dispatch与turn_id；部分thread包含后续复用回合，未把文件中第一条Trade Decision误当本次目标。原始thread文件及抽取的可读输入/工具/最终结果保存在本地审计目录，不需在前端靠ID检索新闻。

| 排名 | Case ID | A 原thread | B 原thread |
|---|---|---|---|
| 1 | `case_66c628e8f3bb400c97857613a6c4b39f` | `01a1e854-7fee-7af3-8592-cb716e867925` | `01a1e854-6ff7-7ec0-8969-3fffae627372` |
| 2 | `case_2a4cb3f385bb430680df292ec48e8423` | `01a1e853-da4d-7852-8802-d1469e4fe22b` | `01a1e853-daab-77c3-9180-ccfe844a5484` |
| 3 | `case_9f9acd13af064a67a42006f1a998b9ef` | `01a1e854-726f-7820-9ee3-5c7ce136c576` | `01a1e854-6896-7a30-8eb0-d58ad9371952` |
| 4 | `case_afd438269b1c4c129bd8e8060194b8ed` | `01a1e854-5f57-78b3-bad2-bd403311c815` | `01a1e854-51b9-7f93-8a42-1ec09a774bdc` |
| 5 | `case_70e54956953549ccac606bf7c2651605` | `01a1e854-b0c6-7cc1-bbc1-2823e374c15c` | `01a1e854-aec4-76c2-9597-ed505b1624cb` |
| 6 | `case_e7e699358ceb45db80404e74f92bdff4` | `01a1e854-7c13-7780-b1e5-9ca7b65e40af` | `01a1e854-8b65-7363-a667-6cb3df56561b` |
| 7 | `case_5541994796fe41f790ffd80778fedb9b` | `01a1e854-86d6-7303-a438-d4b1c9d26559` | `01a1e854-738c-7241-8dec-42a650a161b5` |
| 8 | `case_70e71f8d5e924f578d67f3d56828a6ab` | `01a1eaa8-15e8-7331-b5cf-68563c7d9969` | `01a1eaa8-173d-7421-8ded-fb9832d488c8` |
| 9 | `case_a5efefc1f2644ad3a88075012a178ce7` | `01a1f048-8a1f-7f33-a714-bd95d41ae5cb` | `01a1f048-90a7-7552-87ec-1e31e6f57284` |
| 10 | `case_e27e998a44d2412aa68c39a1ff62d220` | `01a1f00d-fbb6-7452-a398-b6dc36e716e9` | `01a1f008-26d6-7280-9661-1d2bb4e1ae65` |


- [排名母体及快照](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/top10_population.json)
- [十案阶段、job、来源、最终决策和计时原点](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/top10_details.json)
- [20条目标回合的工具主题、提醒、final及行号](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/top10_analysis.json)
- [原生final与SDK返回的边界指标](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/top10_boundary_metrics.json)
- [逐分支可读摘录目录](C:/Users/WEIXUANXIE/Desktop/DoxAgent/artifacts/audits/w3_intc_latency_20261119/top10_reviews/)，文件前缀为排名及A/B，例如`08_B_overview.txt`。

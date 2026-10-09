# MU Document2：prompt/skill 问题 1、2、3、7 的坏例与形成机制

日期：2026-10-07。用途：给 ChatGPT 结合具体 agent prompt/internal skill 分析的背景材料。本文件追溯已经发生的判断与交付，不是新的编排开发指令，也不直接给出 skill 改写全文。

## 0. 证据口径与先纠正的两点

本次重新核读指定 MU SDK Pilot 的节点 completion、冻结 input/skill.md、context、研究期笔记、复盘及公开过程事件。实验是 `mu-d2-o0-sdkpilot-20261006-01`，不混用同目录中的旧 `mu-d2-pilot-001`。O0 有八个节点，O1 只实跑数据中心 Shell 的五阶段。

过程日志包含 commentary、公开 reasoning summary 标题和工具事件摘要，不能恢复隐藏思维链或未记录的完整工具输出。下文的“发生机制”分为直接可见的决定、由输入输出支持的解释；不会把推断写成 Agent 内部动机。原报告的行情及财务数字只作为本次 Agent 使用的历史材料，不在这里重新认证其外部真实性。

两项原问题需要收紧表述，避免给下一轮分析一个错误前提：

- **C5 的正式 Candidate 没有产出股价、技术分析或期权对象。** 发生了定价报告向持久对象转写的困难，但本次转写本身成功。不能将“潜在污染入口”写成已经发生的证券交易研究输出。
- **未发现“一项财务分项不齐就必须取消独立对象”的明文规则，也没有证实 O0 一律这么做。** 已发生的是汽车独立身份被宽 AEBU 对象暂时遮蔽；行业/公司两层虽被认为难判断，实际仍然保留。需要分析的是证据不足如何被错误用于身份判断，而非假定存在一个硬规则。

依据总报告：[O0 详细分析](D:/DoxAgentPilot/cases/document2/_coordinators/mu-d2-o0-sdkpilot-20261006-01/PILOT_ANALYSIS.md)、[O1 详细分析](D:/DoxAgentPilot/cases/document2/_coordinators/mu-d2-o0-sdkpilot-20261006-01/PILOT_ANALYSIS_O1.md)。本次进一步核对了下面列出的原件；结论不只来自总报告复述。

## 1. Synthesis 去重：候选全部有去向，两个经济维度仍然丢失

### 1.1 当时的任务和实际处理方式

Synthesis 收到 C1/C3/C5 共 30 条候选，以及可直接读取的完整报告。完整报告可见，因此不能归因于“编排没给原文”。它按候选的经济身份选代表、将重叠或过宽候选列为 unassigned，再组织共同研究背景及 Shell boundary。

公开过程在北京时间 03:15:04 表示正在处理“终端平台需求与 DRAM/NAND 跨终端周期，以及制造产能、封装和资金安排之间的研究归属”；03:16:36 表示七个研究语境已收敛，下一步检查“全部 30 个候选各出现一次、原字段逐字保留、JSON 符合 schema”。最终 19 条进入 provisional，11 条进入 unassigned。计数、原文保全及 Schema 都没有发现下面的语义遗漏。

冻结 Synthesis skill 第 72–80 行要求选最强代表，把重叠记录放入 unassigned 并解释，Finalization 再整合措辞和来源；同时要求候选五字段原样保留。第 149–159 行的收尾检查包含候选记账、Unit 质量、Shell 一致性和边界质量。它**不是只要求计数**，但没有具体展开“丢弃代表后，每个重要经济维度仍由谁承担”的核对方法。

这个约束有一个实际后果：Synthesis 不能偷偷把 C5 的内容加进保留的 C3 candidate.scope。它可以通过代表选择、Shell scope/boundary、unassigned reason/warning 暴露尚未承接的维度，但本次主要完成了代表去重和研究分工，将措辞整合留给后续。

### 1.2 坏例 A：扩产投资回报被“产能、效率、融资已经覆盖”替代

**进入 Synthesis 前，维度已存在。** C5 的 `制造产能与资本投资` scope 明确包含投资规模、建设和爬坡、良率、可售产出、利用率、折旧及“产能的长期经济回报”；why_material 也明确说项目进度和回报可以独立于当期价格/需求改变长期盈利与资本需求。C5 并没有在 Candidate 时漏掉回报。

**去重决定。** Synthesis 将它列为 unassigned，理由是把晶圆、封装、良率、折旧和资本回报合成了过宽的制造及资金主题，保留的四项会分别承接：

| 被指定的承接对象 | 实际范围 | 为什么不能自动等同于投资回报 |
| --- | --- | --- |
| 晶圆制造产能 | 建设、投产、爬坡、可供位数 | 形成产出不保证新增资产挣回投入 |
| HBM 先进封装产能 | 封装设施、爬坡、可交付能力 | 能交付不等于项目现金收益足够 |
| 制造运营效率 | 良率、利用率、单位成本及产品转换 | 改善效率不自动覆盖折旧、资本占用及整个项目周期 |
| 扩产资本与融资能力 | CAPEX、资金来源、激励、客户资金、现金和债务 | 能支付、能融资不等于投资值得做或能够回收 |

**边界继续固化遗漏。** S6 定义制造可交付能力；S7 scope 是现金转换、投资支付和资本来源，boundary 是现金生成与资金承受能力；产品盈利归 S2。S2 的产品利润、S6 的物理供给、S7 的资金能力都存在，但没有对象明确持续判断“这些新增资产占用多少资本，投产后产生什么增量现金收益，折旧和长期回收风险怎样变化”。

**Review 找出的具体错误。** C5 Review F2 将目标写为 `Missing Subject: 扩产投资回报`，指出即使项目按期投产、融资充足，投资回报仍会因利用率、价格和成本独立恶化；原拓扑容易让 O1 把“能建、能融资”当成“有回报”。建议新增回报对象，并明确不以缺少同口径 ROIC 点估计作为准入条件。

**Finalization 的实际处理。** S7 改为“经营现金、扩产与资本回报”，新增 `扩产投资回报` Unit，scope 明确资本占用、投产后增量现金收益、折旧、长期资本回收，并连接产量、利用率、价格、成本。物理项目和融资对象继续存在，没有靠把全部主题重新合成一个宽 Unit 解决。

**机制判断。** 这是将宽候选拆分后漏掉一个维度，并用邻近对象的存在误证“已经全覆盖”。资本回报依赖产能、效率、资金和产品盈利，但依赖不等于这些对象已经拥有回报判断。Synthesis 的 reason 声称完成了拆分，实际没有逐一对照承接对象的 scope。这是可由输入输出直接确认的漏洞，不需要猜测它是否“没认真读”。

### 1.3 坏例 B：HBM 身份保住了，产品盈利责任却被边界稀释

**三域描述同一产品，深度不同。** C1 的 HBM 候选包含平台采用、产品代际、商业规模和交付；C3 的 `HBM业务` 包含产品、客户应用、竞争位置、商业供给；C5 的 `HBM产品业务` 进一步明确出货和业务经济性，并在重要性说明中写到订单、组合与资本回报。

**代表选择。** Synthesis 保留 C3，把 C1/C5 放到 unassigned。C5 的 reason 甚至明确承认“技术、认证、出货及经济性是该对象的研究维度”，封装另由制造对象承担。因此不能说它根本不知道 HBM 有经济性。

**矛盾出现的位置。** S1 boundary 说该域研究数据中心采用和业务暴露，跨终端 DRAM/NAND 供需与“产品整体经济性”由 S2 维护；S7 也把产品业务盈利交给 S2。保留的 C3 scope 没有明确 HBM 实现价格、组合和利润贡献。于是出现三个不同表述：unassigned reason 说经济性属于保留 HBM；保留候选只是较宽泛的商业供给；Shell boundary 又把整体经济性交给汇总 DRAM/NAND。

**风险是责任模糊，不是 HBM Unit 被删。** O1 如果顺着这套边界研究，可能在 HBM 中只做资格、规格、采用和交付，在 S2 做合计 DRAM 盈利，却没有人独立修订 HBM 商业贡献。总 DRAM ASP相近时，HBM 的客户份额、净价、产品结构、合格交付成本仍能改变其自身盈利；合计层可能掩盖这种变化。

**后续纠偏。** C5 Review F1 要求在现有 HBM Unit 内明确商业规模、实现价格/组合和利润贡献，不另增“HBM 盈利”Unit。Finalization 把 HBM scope 扩到商业规模、盈利贡献，同时保留汇总 DRAM 接口与制造约束分工。数据中心 O1 后续确实形成“合同定价、竞争及合格交付成本决定HBM利润贡献”等 Factor，但独立毛利数值仍缺。结构修正已经有后续行为支持，不能说量化也已完成。

**机制判断。** 与回报案例不同，这是“较窄代表替代较宽描述”叠加“汇总经济性拥有盈利”的边界写法，造成同一身份内部研究责任丢失。它需要复核代表范围、Shell boundary 与 unassigned reason 是否一致。保持原始 C5 scope 在 unassigned 可追溯，并不能保证 O1 会把它当作自身维护职责。

### 1.4 供结合 skill 分析的焦点

两个坏例共用一个漏洞：候选记录的处置被当成经济维度的处置。前者丢了独立判断，后者丢了现有对象的一项责任；因此不能机械地用“每个遗漏都新建 Unit”修复。应讨论如何让 Agent 对照实际承接 scope 验证语义保全，以及如何区分共享证据、因果依赖、汇总接口与维护责任。这里不建议新增程序门禁或强制维度账本。

原件：[Synthesis completion](D:/DoxAgentPilot/cases/document2/d2_o0_synthesis/mu-d2-o0-sdkpilot-20261006-01-05-o0_synthesis/attempts/d2-pilot-o0_synthesis-a24ed3fc14/output/completion.json)、[当时 skill](D:/DoxAgentPilot/cases/document2/d2_o0_synthesis/mu-d2-o0-sdkpilot-20261006-01-05-o0_synthesis/attempts/d2-pilot-o0_synthesis-a24ed3fc14/input/skill.md)、[公开过程](D:/DoxAgentPilot/cases/document2/d2_o0_synthesis/mu-d2-o0-sdkpilot-20261006-01-05-o0_synthesis/attempts/d2-pilot-o0_synthesis-a24ed3fc14/audit/pilot_process.jsonl)、[C5 Review F1/F2](D:/DoxAgentPilot/cases/document2/d2_o0_review_c5/mu-d2-o0-sdkpilot-20261006-01-08-o0_review_c5/attempts/d2-pilot-o0_review_c5-8cb0ec5c1b/output/completion.json)、[Finalization](D:/DoxAgentPilot/cases/document2/d2_o0_finalization/mu-d2-o0-sdkpilot-20261006-01-09-o0_finalization/attempts/d2-pilot-o0_finalization-b3b2e61d75/output/completion.json)。

## 2. C5 转写困难：证券定价框架是输入，不是本次正式对象

### 2.1 困难由什么输入形成

C5 primary_source 的五部分是“当前市场定价基线”“近期重定价与主要定价驱动”“市场隐含的业务、财务与持续期条件”“市场锚点与定价问题”“关键未知项与识别边界”。它用股价、相对收益、前瞻倍数、财报后反应、Q4/FY27公开盈利基线来组织业务。

该完整正文中“股价”出现 5 次、“52周”4 次、“前瞻市盈率”5 次；“期权”“技术面”“RSI”“MACD”“衍生品”“隐含波动”均未出现。它确实包含证券价格表现与估值叙述，但**不是一份含期权链或技术指标分析的报告**。这组事实也限制了本次验收：没有期权输入，不能凭没有期权输出证明 Agent 已具备排除期权材料的稳定能力。

危险的转换路径是直接把报告标题或结论当身份，如“FY27 高盈利持续期”“当前价格能否维持”“合同能否兑现”。前两者依赖当前价格与期间，第三个是某一研究问题；都不能替代持久的业务、关系或资本活动对象。

### 2.2 Agent 实际怎样转换，产出程度如何

03:01:33 的 commentary 明确说定价条件作为发现长期经济对象的线索，不直接成为候选名称；之后公开摘要出现 `Refining economic subjects`、`Refining AI memory categories` 等。03:03:05 的 commentary 把候选范围明确为需求、DRAM/NAND 经济性、HBM、长期客户关系、产能投资和现金循环。研究笔记及复盘将这一点报告为 writing_difficulty，处理后 resolved=true。

最终七个候选全部可以在正式 JSON 中定位：

| 正式候选 | 从定价命题取回的业务主体 | 是否证券交易/技术分析对象 |
| --- | --- | --- |
| AI数据中心内存与存储需求 | 采购规模、配置、节奏、平台暴露 | 否 |
| DRAM业务供需与经济性 | 位量、产品售价、组合、成本、盈利 | 否 |
| NAND业务供需与经济性 | NAND 的独立供需、产品售价与成本路径 | 否 |
| HBM产品业务 | 产品、客户资格、供给、出货、业务经济性 | 否 |
| 战略客户长期供货关系 | 合同量、价格条款、履约、存款和交付 | 否 |
| 制造产能与资本投资 | 制造资产、建设、效率、折旧及回报 | 否 |
| 经营现金生成与扩产资金 | 回款、现金、支付和融资循环 | 否 |

核读七条完整 `name/scope/why_material`，没有股价走势、目标价、技术指标、期权、交易位置、证券波动或收益率对象；正式 warnings=[]。关键词扫描中唯一“定价”命中是战略客户供货关系中的“合同数量和定价条款”，这是商品合同价格，不是证券定价。DRAM/NAND 行业报价、实现 ASP，以及 HBM 工艺技术也不属于用户所排除的股票技术分析；不能因为“价格/技术”两个字就一并剔除经营机制。

C5 Domain Review 仍在 overall_assessment 说明 Q4/FY27 公开盈利基线是研究锚点，不另立“已被定价”Unit。两条正式反馈完全针对 HBM 商业盈利归属和投资回报，没有新增证券技术面/衍生品研究。Finalization 也未设置证券价格对象。

### 2.3 已经解决什么，仍有什么风险

**已发生并解决的困难：对象身份转写。** Agent 没有照抄定价问题，而是找回业务主体。当前证据不支持把它列为“已产生禁止对象”的内容事故。

**仍存在的风险：先验框架和重要性叙述受当前定价支配。** 冻结 Candidate skill 第 92–94 行写明：C5 应将 market、price、valuation、implied-expectation 证据追到被定价的经济对象；“是否已被定价”是研究任务，不是 Candidate 身份。这能制止另设定价 Unit，但不等于明确排除了证券技术面/衍生品论点成为业务推断依据。本次 agent/o0.md 也没有这类明确排除边界。若以后输入含技术指标/期权流，该材料是否会进入 why_material、scope 的因果解释，目前没有坏例或验证支持。

另一个已确认的限制是“公开分析师基线不等于真实市场隐含预期”。C5 将这个限制写在 audit，没有放入正式 warnings；这不是七个对象身份错误，但不能把 resolved=true 解读为市场预期口径也已测量清楚。后续应结合具体 skill 判断何种重要限制应沿正式交付承接，而不是让 O0 回去开展证券交易研究。

**供下一轮分析的边界：** 本轮用户要求排除股票价格走势、技术面和期权/衍生品等证券市场对象及相关推断；应让 ChatGPT 在此边界下检查 C5 的信息取舍与对象抽象。不要把本次的正确输出改写成失败；不要扩展为禁止商品价格、合同定价、产品技术和业务盈利研究。

原件：[C5 context/完整报告](D:/DoxAgentPilot/cases/document2/d2_o0_candidate_c5/mu-d2-o0-sdkpilot-20261006-01-03-o0_candidate_c5/attempts/d2-pilot-o0_candidate_c5-fa08c0ebaa/input/context.json)、[七候选](D:/DoxAgentPilot/cases/document2/d2_o0_candidate_c5/mu-d2-o0-sdkpilot-20261006-01-03-o0_candidate_c5/attempts/d2-pilot-o0_candidate_c5-fa08c0ebaa/output/completion.json)、[当时 skill](D:/DoxAgentPilot/cases/document2/d2_o0_candidate_c5/mu-d2-o0-sdkpilot-20261006-01-03-o0_candidate_c5/attempts/d2-pilot-o0_candidate_c5-fa08c0ebaa/input/skill.md)、[研究笔记及复盘](D:/DoxAgentPilot/cases/document2/d2_o0_candidate_c5/mu-d2-o0-sdkpilot-20261006-01-03-o0_candidate_c5/attempts/d2-pilot-o0_candidate_c5-fa08c0ebaa/audit/pilot_issues.md)。

## 3. 独立身份与财务分项：真正发生的是证据门槛的错误外推

### 3.1 汽车：非汽车材料不足，为什么拖住已有依据的汽车身份

1. **Candidate 颗粒度不同。** C1 给出“汽车与嵌入式内存存储需求”，以 AEBU 总体暴露说明重要性，覆盖汽车、工业及其他嵌入式；C3 给出“汽车内存与存储业务”，依据 OEM/Tier 1、车载产品、资格和车型周期单独界定汽车。候选区别不是简单重名，而是宽范围与窄范围。
2. **Synthesis 选宽代表。** 它保留 C1，未保留 C3 汽车；reason 说汽车部分重合，暂用宽候选承接 AEBU 长周期终端需求，汽车能否拆出留待复核。
3. **疑虑被绑定到错误的判断对象。** 正式 warning 和 audit 说汽车客户/资格证据较充分，非汽车嵌入式业务细节薄，因此“无法稳妥判断汽车是否应从 AEBU 范围拆成独立 Unit”。缺的是另一部分材料，却让已经有独特研究线索的汽车身份进入不确定状态。
4. **C3 Review 指出反例。** F1 明确汽车有 GM、Ford、Tier 1 协作、LPDDR5/DDR5 样品及 UFS 出货；车型资格、量产分配和多年供货可以独立于工业/消费设备修订。AEBU 收入不能当汽车收入，但不妨碍汽车成为独立经济对象。
5. **Finalization 拆开两个判断。** 恢复汽车 Unit；工业/其他嵌入式保留为 Shell 背景，未凭空设工业 Unit，也未用 AEBU 总额代替汽车规模。

直接可见的机制是：**会计/业务单元总额及宽候选被当作稳妥身份锚点 → 拆分似乎要求同时证明宽范围的所有子块 → 非汽车证据不足被外推为汽车不能先独立 → 已有汽车维护价值被聚合范围遮蔽。** 这是对输出理由的机制解释；日志没有证明模型内部存在“缺一个财务数字就否决”的通用算法。

正确的两个判断互不捆绑：汽车能否独立，看其持续主体、重要性及独立变化；非汽车能否再独立，看其自身是否有足够可辨主体及维护价值。前者成立不要求后者同步成立，也不要求两块当前财务数据都已齐全。

### 3.2 行业周期与公司经济性：发生了摇摆，没有实际强行合并

Synthesis 把 C3 DRAM/NAND 全球供需和 C5 美光业务经济性暂留为独立 Unit，但在 warning/audit 中说缺少有效产能、库存、产品级成本/毛利序列，难以确认应合并还是分层。它担心重复维护，且两边 scope 都带“需求、供给、价格”，确有边界重叠。

C1 Review F1 明确建议保留两层：行业紧缺和价格是外部背景；美光 ASP、终端分配、产品/客户组合、制造成本决定公司实现结果。长期合同、组合和执行会让两者不同步，因此分项桥缺失不妨碍建立独立维护对象，需做的是收窄 scope。Finalization 按此执行，量化缺口仍写入 warning。

因此本案例不能描述为“缺产品毛利桥就被迫合并”。确实发生的是**把量化验证独立性的困难提升成身份不确定性**，但当次保留策略没有跨过“删除独立对象”的门槛。C1 Review 的“无法从卖方模型分项直接检验独立更新价值”也是证据能力限制，不是反对保留。

### 3.3 skill 层漏洞在哪里，哪些解释不成立

冻结 O0 角色说明明文允许在未来结果未知前维护经济对象；Synthesis skill 第 50–53 行允许同一因果链和证据支持不同对象，第 69–70 行说不需已证明结果或完整兑现链，第 76–77 行要求不同经济范围仍合理时暂留供 Review。没有要求独立 Unit 拥有齐全独立财务分项。

问题更接近实际执行时把“独立可更新”误作“能否用已披露分项直接证明独立”，以及将非汽车缺口传递给汽车。这可能与可量化 AEBU 总额更显眼、担心重复维护、对缺数据如何判断缺少具体示例有关；这些是材料支持的解释，不能断言其中某一个就是隐藏推理的唯一原因。

下一轮应检查 skill 能否清楚区分：对象身份成立、对象经济重要性有依据、当前状态能否量化、机制能否精确测量。财务分项可以帮助判断，但不是每项都必须满足的合取门槛。反过来，也不能把“没有分项不影响身份”扩成“只要有主题名就建 Unit”：工业是否独立仍需可辨主体与维护价值，不一定要求财务金额，但不能凭空补造。

原件：[C3 Review 汽车 F1](D:/DoxAgentPilot/cases/document2/d2_o0_review_c3/mu-d2-o0-sdkpilot-20261006-01-07-o0_review_c3/attempts/d2-pilot-o0_review_c3-873b70efda/output/completion.json)、[C1 Review 行业/公司 F1](D:/DoxAgentPilot/cases/document2/d2_o0_review_c1/mu-d2-o0-sdkpilot-20261006-01-06-o0_review_c1/attempts/d2-pilot-o0_review_c1-2a37fee0d6/output/completion.json)。

## 4. O1 生命周期：完整 Shell 的替换语义扩散到了本轮过程数组

### 4.1 本次首代不是机制研究失败，而是交付账本的生命周期混淆

State 的正式结果新增一条：

```json
{
  "unit": "数据中心非HBM内存业务",
  "name": "LPDRAM供给约束引发SOCAMM每机配置下调",
  "discovered_during": "STATE"
}
```

正文解释 LPDRAM 可分配位数不足时，SOCAMM 每机容量、系统数量与 DDR/LPDRAM 配置可能改变。它是 State 发现的方向，不是 Realization 新发现。State 同时形成 4 条增量决议，编排将这 1 条新增和 4 条决议放入 Realization 的冻结 context。

Realization 首代构造完整 Shell 和 16 个 Factor 后，又把上述历史新增放入本轮 `late_additions`，阶段仍为 STATE；将原 4 条决议连同自己新形成的 6 条放入 `open_discovery_resolution`，共 10 条。

18:36:08 的 commentary 说要检查是否保留 State、Baseline 和此前 late addition；18:37:43 说已保留原 State/Baseline、1 个既有 late addition，决议“累计为 10 条”。这两句是直接可见的生命周期决定，远比笼统说“所有数组都累积”更准确：模型将完整 Shell 的保留要求外推到顶层过程数组，主动把历史账本再交付。

18:42:44 驱动接纳失败，g1 receipt 原文：`ValueError: late_additions: unknown unit or incorrect discovered_during`。本次具体触发条件是阶段不符，Unit 名有效；不能仅按复合错误字符串把它解读成未知 Unit。

### 4.2 为什么当时 skill 写了增量，仍然发生误读

冻结 Realization skill 前部要求从 refreshed canonical_shell、State、Baseline 和 discovery records 承接研究；末部明确完整 Shell 替换上一轮，保留有效 State/Baseline/Gaps/ref。同时也已经写着 `late_additions` 仅为本轮新发现、`discovered_during: REALIZATION`，resolutions 为 incremental，Finalization 再完整闭环。

所以不是 skill 完全没有增量规则。可见的失误是 Agent 执行了“完整模型保留”，却把“发现史不丢失”理解为顶层历史必须重复输出。输入里全量 canonical_shell 与累计过程记录并排存在，输出里三项又同处 envelope；强烈的保留指令与末尾局部增量语义没有被稳定地区分。

其本地检查能够确认 JSON Schema、研究结构及 State/Baseline 保留，仍未及时发现过程阶段错误。公开 commentary 宣称校验通过不能替代驱动器实际接纳；两个检查覆盖范围不同。此处没有证据说明它故意改阶段绕过校验——它实际上保留了 STATE，错误是重交，不是伪造首次发现时间。

### 4.3 修正的实际动作和边界

同 thread 的下一 request 携带接纳错误后，18:51:48 commentary 明确承认把 State 记录再次放入本轮增量数组；重新读取 skill 和上游，以只读 `audit/research_output.g1/completion.json` 为底稿。

修正只改变两个数组：`late_additions=[]`；resolutions 从 10 条改成 6 条本轮新增。重新比较确认：

- 首代与成功代 `canonical_shell` 完全相同，16 个 Factor 没重做，原 State/Baseline 没被删除。
- 首代前 4 条 resolution 与上游累计 4 条逐项相同；后 6 条与成功代 6 条逐项相同。
- 首代新增仍可从不可变 State/context 查到，没有删除发现史，也没有把 discovered_during 改成 REALIZATION。
- g1 研究 786.455 秒（约 13分06秒）后拒收；g2 修正与重新交付 513.040 秒（约 8分33秒）。这是当时生命周期错误的实际成本，不代表需要重新研究全部机制。

### 4.4 用本次真实数据说明四阶段的不同生命周期

下表是**2026-10-06 Pilot 当时的作者交付合同**，不是对正在修复的接纳器设新的拒收要求：

| 阶段 | 输入累计新增/决议 | 本轮作者 late_additions | 本轮作者 resolution | 下轮可见结果 |
| --- | --- | --- | --- | --- |
| State | 0 / 0 | 1 条 STATE 新发现 | 4 条本轮决议 | 累计 1 / 4 |
| Realization 成功代 | 1 / 4 | 0 条 | 6 条本轮决议 | 累计 1 / 10 |
| Gaps | 1 / 10 | 2 条 GAPS 新发现 | 12 条本轮决议 | 累计 3 / 22 |
| Finalization | 3 / 22 | 0 条新发现 | 25 条最终决议 | 完整最终去向，历史 provenance 仍保留 |

Finalization 的 25 条包括 22 个必需闭环方向及额外 3 个 MERGE 去向。它不是第四个普通增量阶段；把“resolution 都只写增量”推广到 Finalization 同样会错。

三个生命周期应分开理解：`canonical_shell` 是本轮完整有效模型；中间阶段的作者过程记录描述本轮新增/更新贡献；历史新增和决议由编排累计供后续理解，最终阶段形成最终闭环。完整输出包含上游 State 是正常的，包含上游作者过程增量则在当时触发错误。

### 4.5 与当前编排修复方向的关系：不能再把研究理解问题变成硬闸

当前已批准的[编排鲁棒性方案](C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2.1/d2_d3_orchestration_robustness_repair_plan_20261007.md)要求历史重交按键合并、保留首次 discovered_during，Resolution 中间阶段和 Finalization 都累计；不因历史重交重跑机制研究。该方案处于主线程开发，本文件不干预它，也不以读取到的中间代码宣称验收已完成。

因此给 ChatGPT 分析时，不能把“本轮只写增量”重新设计为严格拒收门禁。skill 应让 Agent 明白作者贡献与编排累计快照的区别，避免无意义重交；接纳器则能够容忍重交、修复历史 provenance。Agent 不需为了通过格式检查把首次发现时间改成当前阶段，也不需因为收到累计数组就复制累计数组。编排接纳成功与长期研究心智稳定是两项不同证据。

原件：[Realization 首代原件](D:/DoxAgentPilot/cases/document2/d2_o1_realization/mu-d2-o0-sdkpilot-20261006-01-12-o1_realization/attempts/d2-pilot-o1_realization-14404d32de/audit/research_output.g1/completion.json)、[首代失败 receipt](D:/DoxAgentPilot/cases/document2/d2_o1_realization/mu-d2-o0-sdkpilot-20261006-01-12-o1_realization/attempts/d2-pilot-o1_realization-14404d32de/audit/pilot_sdk_receipt.g1.json)、[成功产物](D:/DoxAgentPilot/cases/document2/d2_o1_realization/mu-d2-o0-sdkpilot-20261006-01-12-o1_realization/attempts/d2-pilot-o1_realization-14404d32de/output/completion.json)、[冻结 skill](D:/DoxAgentPilot/cases/document2/d2_o1_realization/mu-d2-o0-sdkpilot-20261006-01-12-o1_realization/attempts/d2-pilot-o1_realization-14404d32de/input/skill.md)、[公开过程](D:/DoxAgentPilot/cases/document2/d2_o1_realization/mu-d2-o0-sdkpilot-20261006-01-12-o1_realization/attempts/d2-pilot-o1_realization-14404d32de/audit/pilot_process.jsonl)。

## 5. 带入 ChatGPT 对话时应保留的背景

| 项目 | 已确认坏例/困难 | 不应带入的错误前提 | 结合 skill 分析的核心 |
| --- | --- | --- | --- |
| 1 | 拆分宽候选漏回报；较窄 HBM 代表与汇总边界让盈利归属含糊 | 候选无去向、原报告未提供、每个维度必须新建 Unit | 丢弃/归并后核对实际语义承接，检查 scope/boundary/reason 一致性 |
| 2 | C5 从定价问题到持久对象有组织困难，当前输出转写成功 | 已生成股票技术面或期权 Unit；商品价格也应禁止 | 限制证券市场论点进入研究身份及业务推断，保留业务价格和技术机制 |
| 3 | 宽 AEBU 与非汽车证据缺口遮蔽汽车；行业/公司独立性被量化缺口干扰 | 一项分项缺失就硬否决；行业/公司本次已合并；无财务数就随意造主题 | 把身份、维护价值、数据可测性分开，对每个子对象分别判断 |
| 7 | 完整 Shell 保留规则扩散到过程数组，首代重复 1/4 历史记录而拒收 | 没有写增量规则；机制研究失败；必须强化阶段拒收 | 教清三种生命周期，承认新编排容错，保留首次发现 provenance |

冻结的 Candidate、Synthesis、Realization skill 与当前工作区对应文件在规范化换行后文本一致；字节不同来自换行。历史行为可与这些 skill 直接对应，但不等于一次 Pilot 已验证稳定。最有价值的分析是找出 Agent 在具体示例中如何误用已写原则，而不是继续堆叠抽象的“不要重复、注意独立、遵守 schema”。

下面的证据索引以本次实际目录扫描自动列出；所有链接均指向原件。本轮仅新增背景文档，不修改 prompt/skill、源码、Pilot 冻结输入或产物，不重跑模型。

## 6. 核验后的原件索引

| 节点 | 正式产物 | 冻结 skill | 冻结上下文 | 原始过程与审计 |
| --- | --- | --- | --- | --- |
| d2_o0_candidate_c5 | [completion](D:/DoxAgentPilot/cases/document2/d2_o0_candidate_c5/mu-d2-o0-sdkpilot-20261006-01-03-o0_candidate_c5/attempts/d2-pilot-o0_candidate_c5-fa08c0ebaa/output/completion.json) | [skill](D:/DoxAgentPilot/cases/document2/d2_o0_candidate_c5/mu-d2-o0-sdkpilot-20261006-01-03-o0_candidate_c5/attempts/d2-pilot-o0_candidate_c5-fa08c0ebaa/input/skill.md) | [context](D:/DoxAgentPilot/cases/document2/d2_o0_candidate_c5/mu-d2-o0-sdkpilot-20261006-01-03-o0_candidate_c5/attempts/d2-pilot-o0_candidate_c5-fa08c0ebaa/input/context.json) | [过程](D:/DoxAgentPilot/cases/document2/d2_o0_candidate_c5/mu-d2-o0-sdkpilot-20261006-01-03-o0_candidate_c5/attempts/d2-pilot-o0_candidate_c5-fa08c0ebaa/audit/pilot_process.jsonl) / [笔记复盘](D:/DoxAgentPilot/cases/document2/d2_o0_candidate_c5/mu-d2-o0-sdkpilot-20261006-01-03-o0_candidate_c5/attempts/d2-pilot-o0_candidate_c5-fa08c0ebaa/audit/pilot_issues.md) |
| d2_o0_synthesis | [completion](D:/DoxAgentPilot/cases/document2/d2_o0_synthesis/mu-d2-o0-sdkpilot-20261006-01-05-o0_synthesis/attempts/d2-pilot-o0_synthesis-a24ed3fc14/output/completion.json) | [skill](D:/DoxAgentPilot/cases/document2/d2_o0_synthesis/mu-d2-o0-sdkpilot-20261006-01-05-o0_synthesis/attempts/d2-pilot-o0_synthesis-a24ed3fc14/input/skill.md) | [context](D:/DoxAgentPilot/cases/document2/d2_o0_synthesis/mu-d2-o0-sdkpilot-20261006-01-05-o0_synthesis/attempts/d2-pilot-o0_synthesis-a24ed3fc14/input/context.json) | [过程](D:/DoxAgentPilot/cases/document2/d2_o0_synthesis/mu-d2-o0-sdkpilot-20261006-01-05-o0_synthesis/attempts/d2-pilot-o0_synthesis-a24ed3fc14/audit/pilot_process.jsonl) / [笔记复盘](D:/DoxAgentPilot/cases/document2/d2_o0_synthesis/mu-d2-o0-sdkpilot-20261006-01-05-o0_synthesis/attempts/d2-pilot-o0_synthesis-a24ed3fc14/audit/pilot_issues.md) |
| d2_o0_review_c1 | [completion](D:/DoxAgentPilot/cases/document2/d2_o0_review_c1/mu-d2-o0-sdkpilot-20261006-01-06-o0_review_c1/attempts/d2-pilot-o0_review_c1-2a37fee0d6/output/completion.json) | [skill](D:/DoxAgentPilot/cases/document2/d2_o0_review_c1/mu-d2-o0-sdkpilot-20261006-01-06-o0_review_c1/attempts/d2-pilot-o0_review_c1-2a37fee0d6/input/skill.md) | [context](D:/DoxAgentPilot/cases/document2/d2_o0_review_c1/mu-d2-o0-sdkpilot-20261006-01-06-o0_review_c1/attempts/d2-pilot-o0_review_c1-2a37fee0d6/input/context.json) | [过程](D:/DoxAgentPilot/cases/document2/d2_o0_review_c1/mu-d2-o0-sdkpilot-20261006-01-06-o0_review_c1/attempts/d2-pilot-o0_review_c1-2a37fee0d6/audit/pilot_process.jsonl) / [笔记复盘](D:/DoxAgentPilot/cases/document2/d2_o0_review_c1/mu-d2-o0-sdkpilot-20261006-01-06-o0_review_c1/attempts/d2-pilot-o0_review_c1-2a37fee0d6/audit/pilot_issues.md) |
| d2_o0_review_c3 | [completion](D:/DoxAgentPilot/cases/document2/d2_o0_review_c3/mu-d2-o0-sdkpilot-20261006-01-07-o0_review_c3/attempts/d2-pilot-o0_review_c3-873b70efda/output/completion.json) | [skill](D:/DoxAgentPilot/cases/document2/d2_o0_review_c3/mu-d2-o0-sdkpilot-20261006-01-07-o0_review_c3/attempts/d2-pilot-o0_review_c3-873b70efda/input/skill.md) | [context](D:/DoxAgentPilot/cases/document2/d2_o0_review_c3/mu-d2-o0-sdkpilot-20261006-01-07-o0_review_c3/attempts/d2-pilot-o0_review_c3-873b70efda/input/context.json) | [过程](D:/DoxAgentPilot/cases/document2/d2_o0_review_c3/mu-d2-o0-sdkpilot-20261006-01-07-o0_review_c3/attempts/d2-pilot-o0_review_c3-873b70efda/audit/pilot_process.jsonl) / [笔记复盘](D:/DoxAgentPilot/cases/document2/d2_o0_review_c3/mu-d2-o0-sdkpilot-20261006-01-07-o0_review_c3/attempts/d2-pilot-o0_review_c3-873b70efda/audit/pilot_issues.md) |
| d2_o0_review_c5 | [completion](D:/DoxAgentPilot/cases/document2/d2_o0_review_c5/mu-d2-o0-sdkpilot-20261006-01-08-o0_review_c5/attempts/d2-pilot-o0_review_c5-8cb0ec5c1b/output/completion.json) | [skill](D:/DoxAgentPilot/cases/document2/d2_o0_review_c5/mu-d2-o0-sdkpilot-20261006-01-08-o0_review_c5/attempts/d2-pilot-o0_review_c5-8cb0ec5c1b/input/skill.md) | [context](D:/DoxAgentPilot/cases/document2/d2_o0_review_c5/mu-d2-o0-sdkpilot-20261006-01-08-o0_review_c5/attempts/d2-pilot-o0_review_c5-8cb0ec5c1b/input/context.json) | [过程](D:/DoxAgentPilot/cases/document2/d2_o0_review_c5/mu-d2-o0-sdkpilot-20261006-01-08-o0_review_c5/attempts/d2-pilot-o0_review_c5-8cb0ec5c1b/audit/pilot_process.jsonl) / [笔记复盘](D:/DoxAgentPilot/cases/document2/d2_o0_review_c5/mu-d2-o0-sdkpilot-20261006-01-08-o0_review_c5/attempts/d2-pilot-o0_review_c5-8cb0ec5c1b/audit/pilot_issues.md) |
| d2_o0_finalization | [completion](D:/DoxAgentPilot/cases/document2/d2_o0_finalization/mu-d2-o0-sdkpilot-20261006-01-09-o0_finalization/attempts/d2-pilot-o0_finalization-b3b2e61d75/output/completion.json) | [skill](D:/DoxAgentPilot/cases/document2/d2_o0_finalization/mu-d2-o0-sdkpilot-20261006-01-09-o0_finalization/attempts/d2-pilot-o0_finalization-b3b2e61d75/input/skill.md) | [context](D:/DoxAgentPilot/cases/document2/d2_o0_finalization/mu-d2-o0-sdkpilot-20261006-01-09-o0_finalization/attempts/d2-pilot-o0_finalization-b3b2e61d75/input/context.json) | [过程](D:/DoxAgentPilot/cases/document2/d2_o0_finalization/mu-d2-o0-sdkpilot-20261006-01-09-o0_finalization/attempts/d2-pilot-o0_finalization-b3b2e61d75/audit/pilot_process.jsonl) / [笔记复盘](D:/DoxAgentPilot/cases/document2/d2_o0_finalization/mu-d2-o0-sdkpilot-20261006-01-09-o0_finalization/attempts/d2-pilot-o0_finalization-b3b2e61d75/audit/pilot_issues.md) |
| d2_o1_state | [completion](D:/DoxAgentPilot/cases/document2/d2_o1_state/mu-d2-o0-sdkpilot-20261006-01-11-o1_state/attempts/d2-pilot-o1_state-7d7f6de1d7/output/completion.json) | [skill](D:/DoxAgentPilot/cases/document2/d2_o1_state/mu-d2-o0-sdkpilot-20261006-01-11-o1_state/attempts/d2-pilot-o1_state-7d7f6de1d7/input/skill.md) | [context](D:/DoxAgentPilot/cases/document2/d2_o1_state/mu-d2-o0-sdkpilot-20261006-01-11-o1_state/attempts/d2-pilot-o1_state-7d7f6de1d7/input/context.json) | [过程](D:/DoxAgentPilot/cases/document2/d2_o1_state/mu-d2-o0-sdkpilot-20261006-01-11-o1_state/attempts/d2-pilot-o1_state-7d7f6de1d7/audit/pilot_process.jsonl) / [笔记复盘](D:/DoxAgentPilot/cases/document2/d2_o1_state/mu-d2-o0-sdkpilot-20261006-01-11-o1_state/attempts/d2-pilot-o1_state-7d7f6de1d7/audit/pilot_issues.md) |
| d2_o1_realization | [completion](D:/DoxAgentPilot/cases/document2/d2_o1_realization/mu-d2-o0-sdkpilot-20261006-01-12-o1_realization/attempts/d2-pilot-o1_realization-14404d32de/output/completion.json) | [skill](D:/DoxAgentPilot/cases/document2/d2_o1_realization/mu-d2-o0-sdkpilot-20261006-01-12-o1_realization/attempts/d2-pilot-o1_realization-14404d32de/input/skill.md) | [context](D:/DoxAgentPilot/cases/document2/d2_o1_realization/mu-d2-o0-sdkpilot-20261006-01-12-o1_realization/attempts/d2-pilot-o1_realization-14404d32de/input/context.json) | [过程](D:/DoxAgentPilot/cases/document2/d2_o1_realization/mu-d2-o0-sdkpilot-20261006-01-12-o1_realization/attempts/d2-pilot-o1_realization-14404d32de/audit/pilot_process.jsonl) / [笔记复盘](D:/DoxAgentPilot/cases/document2/d2_o1_realization/mu-d2-o0-sdkpilot-20261006-01-12-o1_realization/attempts/d2-pilot-o1_realization-14404d32de/audit/pilot_issues.md) |
| d2_o1_gaps | [completion](D:/DoxAgentPilot/cases/document2/d2_o1_gaps/mu-d2-o0-sdkpilot-20261006-01-13-o1_gaps/attempts/d2-pilot-o1_gaps-e84ad5b941/output/completion.json) | [skill](D:/DoxAgentPilot/cases/document2/d2_o1_gaps/mu-d2-o0-sdkpilot-20261006-01-13-o1_gaps/attempts/d2-pilot-o1_gaps-e84ad5b941/input/skill.md) | [context](D:/DoxAgentPilot/cases/document2/d2_o1_gaps/mu-d2-o0-sdkpilot-20261006-01-13-o1_gaps/attempts/d2-pilot-o1_gaps-e84ad5b941/input/context.json) | [过程](D:/DoxAgentPilot/cases/document2/d2_o1_gaps/mu-d2-o0-sdkpilot-20261006-01-13-o1_gaps/attempts/d2-pilot-o1_gaps-e84ad5b941/audit/pilot_process.jsonl) / [笔记复盘](D:/DoxAgentPilot/cases/document2/d2_o1_gaps/mu-d2-o0-sdkpilot-20261006-01-13-o1_gaps/attempts/d2-pilot-o1_gaps-e84ad5b941/audit/pilot_issues.md) |
| d2_o1_finalization | [completion](D:/DoxAgentPilot/cases/document2/d2_o1_finalization/mu-d2-o0-sdkpilot-20261006-01-14-o1_finalization/attempts/d2-pilot-o1_finalization-c925a585a0/output/completion.json) | [skill](D:/DoxAgentPilot/cases/document2/d2_o1_finalization/mu-d2-o0-sdkpilot-20261006-01-14-o1_finalization/attempts/d2-pilot-o1_finalization-c925a585a0/input/skill.md) | [context](D:/DoxAgentPilot/cases/document2/d2_o1_finalization/mu-d2-o0-sdkpilot-20261006-01-14-o1_finalization/attempts/d2-pilot-o1_finalization-c925a585a0/input/context.json) | [过程](D:/DoxAgentPilot/cases/document2/d2_o1_finalization/mu-d2-o0-sdkpilot-20261006-01-14-o1_finalization/attempts/d2-pilot-o1_finalization-c925a585a0/audit/pilot_process.jsonl) / [笔记复盘](D:/DoxAgentPilot/cases/document2/d2_o1_finalization/mu-d2-o0-sdkpilot-20261006-01-14-o1_finalization/attempts/d2-pilot-o1_finalization-c925a585a0/audit/pilot_issues.md) |

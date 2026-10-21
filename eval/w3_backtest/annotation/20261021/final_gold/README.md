# 用户最终Gold录入回执（2026-10-21）

用户提供两张截图：batch01_conditional.png 与 batch02_03.png。保留37条既有明确标签，补齐29条未评估和7条条件评估，共更新36条。最终73条全部可评分：7 LONG、4 SHORT、62 NO_TRADE。

原始Gold在gold.before.jsonl；图片、逐条理由和来源在本目录。原始条件评估亦保存在每条prior_review，最终标签不再视为未标注。NOISE组名不决定动作，NOISE-09最终为LONG。NASA的LONG表示多供应商采购预期重估，不表示RKLB已经取得NASA合同。

旧基线按完整73条Gold重新计算（不改写旧run）：{'MISSED_OPPORTUNITY': 7, 'MATCH': 63, 'WRONG_TRADE': 3}，正确率86.30%。旧37条Gold的89.19%不可与新73条Gold直接比较。

| Case | Final Gold | 理由 |
|---|---|---|
| EXTRA-INTC-OPENAI | TRADE SHORT | FT披露OpenAI年化收入较此前市场认知低约200亿美元，是AI投资与商业化预期的重大信息纠正，影响已扩散到半导体及AI基础设施股票；属于广泛AI风险重估，并非虚构Intel订单取消。 |
| EXTRA-INTC-TERAFAB | TRADE LONG | Musk明确限制TSMC可能承担的角色，Intel CEO确认继续参与Terafab，直接缓解Intel可能被排除的重大担忧，正向修正市场关注的Foundry竞争地位预期。 |
| EXTRA-RKLB-SYNSPECTIVE | TRADE LONG | Rocket Lab正式签下20次新增Electron发射任务，使Synspective累计任务达47次、Electron积压任务超过100次；新合同有明确商业分量，且距公告仅约3分钟。 |
| EXTRA-RKLB-NASA | TRADE LONG | Bloomberg新披露NASA拟改变采购方式，批量购买多种火箭服务，为SpaceX以外发射供应商带来重要市场机会；RKLB可受益于政府多供应商采购预期重估，但尚未取得具体订单，属于有争议的LONG。 |
| NOISE-01 | NO_TRADE  | 美国政府整合AI门户与BE现场发电需求之间缺乏足够直接、重大的新增经济传导。 |
| NOISE-02 | NO_TRADE  | SK Hynix旗下Solidigm探索IPO不直接改变Intel经营价值；核心消息已被此前Event覆盖。 |
| NOISE-03 | NO_TRADE  | ASML与Intel投资比较主要重述历史财务、估值及长期观点，没有新的Intel级重大催化。 |
| NOISE-04 | NO_TRADE  | Oracle/Bloom合作保持不变的澄清此前已经传播；回顾性标题没有新增足够重要的风险解除事实。 |
| NOISE-05 | NO_TRADE  | Intel继续参与Terafab、TSMC最多分租的关键信息已在更早报道出现，是同一利好的再次传播，不足以产生第二次独立交易。 |
| NOISE-06 | NO_TRADE  | 美国工程服务市场预测仅将MU香槟工厂作为历史背景案例，没有新增MU投资、建设或盈利预期变化。 |
| NOISE-07 | NO_TRADE  | Trefis重新分析MU估值、利润率、SCA合同及存储周期，有重要经营变量但没有足够新的盈利、定价或合同预期修订。 |
| NOISE-08 | NO_TRADE  | Nvidia新PC处理器发布以及Intel、AMD盘前下跌属已知事件；Stocktwits报道仅回顾，没有新的竞争冲击。 |
| NOISE-09 | TRADE LONG | 此前TSMC可能参与Terafab的消息损害Intel预期，Intel CEO随后直接向Bloomberg确认公司将继续参与；虽然不是新签合同，但属于重大竞争疑虑后的高层澄清，具有新的可信度和风险修复作用。 |
| NOISE-10 | NO_TRADE  | DIGITIMES讨论Intel、TSMC、Samsung背面供电及先进封装技术趋势，没有具体改变MU的HBM竞争地位、客户采购或盈利预期。 |
| NOISE-11 | NO_TRADE  | Reuters再次转述Bloomberg关于Intel继续参与Terafab的报道，主要经济事实已在NOISE-09披露，没有足够新增信息支持再次LONG。 |
| NOISE-12 | NO_TRADE  | 中美AI监管立场的积极解读，没有对应正式、重大且超预期的监管变化；对Intel需求和Foundry的传导太间接。 |
| NOISE-13 | NO_TRADE  | Electron第97次任务成功具有经营价值，但属于成熟发射业务正常进展；Cantor重申评级以及Neutron、Iridium前景没有形成新的重大Surprise。 |
| NOISE-14 | NO_TRADE  | Rohm、Toshiba、Mitsubishi功率半导体合作谈判受阻，与Intel主业、CPU业务缺少实质经济连接。 |
| NOISE-15 | NO_TRADE  | 对Intel Foundry、14A、季度业绩和潜在客户的综合评论，基本属于已披露事实，没有新的重要客户、制程资格或商业化突破。 |
| NOISE-16 | NO_TRADE  | 文章讨论Texas大学橄榄球及防守目标；关键词Intel和Defense均不能形成公司或防产业关联。 |
| NOISE-17 | NO_TRADE  | Motley Fool讨论380倍PE、历史订单和AI增长是否支撑估值，属于既有事实和估值观点，没有新的公司经营恶化或盈利预期下修；高估值本身不能成为SHORT催化。 |
| NOISE-18 | NO_TRADE  | 量子技术市场规模预测及整个行业，Intel只是被列举公司之一，没有新增Intel订单、客户采用、技术突破或盈利变化，缺乏INTC级经济影响。 |
| NOISE-19 | NO_TRADE  | 特朗普AI监管表态及私营安全标准组织计划，即使属实，也没有形成明确AI投资需求修订，更无法显著传导至BE现场供电订单。 |
| NOISE-20 | NO_TRADE  | 台湾政府支持TSMC海外扩张、核心研发留台，主要涉及TSMC自身地域与制造布局，没有改变MU存储供需、产品竞争力或商业预期。 |
| NOISE-21 | NO_TRADE  | Ballard与Siemens氢燃料电池列车订单涉及不同企业、技术和应用场景，不能因同属燃料电池推断Bloom商业机会发生重大变化。 |
| NOISE-22 | NO_TRADE  | 单款搭载DDR5与SSD的游戏PC促销，不代表整个PC内存需求、DRAM定价或MU订单发生变化。 |
| NOISE-23 | NO_TRADE  | Musk对Terafab的澄清确实缓解Intel被TSMC取代担忧，但MarketWatch报道前Bloomberg、Reuters及马斯克本人已传播主要信息；当前报道没有足够独立新增催化。 |
| NOISE-24 | NO_TRADE  | 博物馆VR影片上线属于文化内容分发，不涉及足以改变MU内存需求、客户关系或盈利预期的经济事件。 |
| NOISE-25 | NO_TRADE  | 32.87美元是实际公布的GAAP EPS，确有正面财报信息，但单行快讯不足以独立评价完整财报与指引；预定的定期财报事件按此前侧重非财报独立催化的策略口径不直接建立新仓位。 |
| BE-01-01 | TRADE SHORT | Oracle对Project Jupiter采取不可抗力保护是与BE重大项目直接相关的真实利空；虽已由Bloomberg首报且10:40市场明显负面反应，低开跌幅不足以证明完成重定价。方向成立，当前是否仍有空间是关键边界。 |
| BE-02-01 | TRADE SHORT | Reuters约18:55—18:59首次进一步披露项目可能整体延迟一年，相对于上午force majeure报道是有实质意义的新量级信息；19:05仍可能发生新一轮风险重估。Blue Owl重申承诺是重要抵消因素，但不能直接否定新增催化，属于有争议的SHORT。 |
| BE-02-03 | NO_TRADE  | Force majeure、管道问题和融资压力多已公开；BorderPlex调整不足以单独证明BE订单受损。同时Bloom合同承诺及Morgan Stanley的风险重新解释成为重要同期信息，重复旧负面叙事不足以支持再次SHORT。 |
| BE-03-01 | NO_TRADE  | Ameren的500MW规划有经营分量，但Fremont扩产及Ameren消息此前已披露，9月29日上午也已成为市场关注的上涨催化。10:56的Barrons主要是估值与已发生行情的解释，缺少足够新的独立增量。 |
| INTC-02-02 | NO_TRADE  | TSMC/Terafab最初不确定性确实对Intel不利，但核心消息自10月2日起已传播；10月5日新加入的Susquehanna CPU份额分析尚不足以构成独立重大Intel盈利下修，不能仅因原始利空方向正确继续追空。 |
| MU-01-03 | NO_TRADE  | UBS看多MU和回购逻辑有研究价值，但核心观点9月23日已公开，9月24日15:39的Barrons转载没有明显新的市场预期修正，且已超过系统15:30日内平仓时点。 |
| MU-02-01 | TRADE SHORT | 约5180亿美元算力义务从可能被理解为单年支出纠正为多年安排，是潜在重大市场认知变化；若市场此前采信单年口径，纠正可显著下修近期AI算力需求预期。但判断依赖此前错误口径确实已被市场采信。 |

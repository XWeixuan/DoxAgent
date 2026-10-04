# MU Monitoring Execution Policies V1

Publication state: PARTIAL

## 下一季盈利基线再上移 (pol_b5f4a190b15fbc22d2fe)

- Direction: LONG
- Match scope: Micron季度业绩、下一财季收入/GAAP EPS/毛利率指引、发布前同口径卖方共识及会计口径说明。
- Activation semantics: OR (any one complete Condition)

- C1: Micron在FQ4 FY2026达到或超过既有指引的同一业绩公告中，给出的下一财季收入与同口径EPS指引中点均高于公告前可比卖方共识，且GAAP毛利率指引不低于86%。

## DRAM与NAND同步进入周期反转 (pol_9f726827c3fe37bc2ac6)

- Direction: SHORT
- Match scope: Micron财报中的DRAM/NAND实现ASP、bit shipments、合并毛利率、收入/EPS指引及产品桥接。
- Activation semantics: OR (any one complete Condition)

- C1: Micron在同一季度确认DRAM与NAND实现ASP均环比下降、两类产品bit shipments也均环比转弱，并同步下调合并GAAP毛利率或下一财季收入/EPS指引。

## 战略协议进入重复履约 (pol_aa7a5be0213e21347434)

- Direction: LONG
- Match scope: Micron战略客户协议、take-or-pay履约、重复出货、合同收入、客户订金和资产负债表现金承诺。
- Activation semantics: OR (any one complete Condition)

- C1: Micron首次把一组重要战略客户协议明确连接到至少两个连续季度的重复产品交付，并在同一正式财报中确认对应收入认列或客户现金订金已实际入账。

## 长期协议减损下调收入与现金可见性 (pol_5a365e8f9461c155bfd8)

- Direction: SHORT
- Match scope: Micron或客户对长期协议延期、削量、降价、取消、违约争议及合同收入/现金承诺余额的正式披露。
- Activation semantics: OR (any one complete Condition)

- C1: Micron或协议客户正式确认一份长期协议的延期、采购削减、价格边界下调、取消或履约争议已经生效，并使Micron下调该协议计入的合同覆盖收入、现金承诺余额或采购预期。

## 客户信用事件破坏现金转换 (pol_99664428e2bd31afe8bc)

- Direction: SHORT
- Match scope: Micron应收账款、信用损失准备、客户逾期/重组、合同订金退款、经营现金流指引及相关财务附注。
- Activation semantics: OR (any one complete Condition)

- C1: Micron确认一名或一类客户因逾期、重组或偿付能力恶化产生信用事件，并因此确认信用损失费用、退还已收战略订金或下调经营现金流指引。

## 主要竞争者提前释放DRAM供给 (pol_83c565b3ca305ee04c41)

- Direction: SHORT
- Match scope: 三星、SK海力士等主要DRAM供应商的新节点/先进封装量产良率、客户资格、重复DRAM出货、行业交期及合同价格。
- Activation semantics: OR (any one complete Condition)

- C1: 一家主要DRAM供应商在2028年前确认一项新节点或先进封装产能达到量产良率并向多个重要客户重复交付合格DRAM，同时DRAM市场交期缩短或合同价开始下降。

## 服务器成本压缩内存实际需求 (pol_24beda52c9f742e3f61a)

- Direction: SHORT
- Match scope: 主要CSP/服务器OEM的AI服务器TCO、内存成本、已批准部署数量、每机内存配置、上线时点和DRAM/内存采购。
- Activation semantics: OR (any one complete Condition)

- C1: 一个主要CSP或服务器OEM明确因内存或整机成本越过预算边界，削减已批准的AI服务器数量、每机内存配置或上线节奏，并同步减少或延期相关内存采购。

## Micron制造中断削弱交付 (pol_a2fb4594422ae0c078a2)

- Direction: SHORT
- Match scope: Micron晶圆厂、先进封装、物流、不可抗力、非计划停产、可售bit产量、客户交付和恢复时间。
- Activation semantics: OR (any one complete Condition)

- C1: Micron确认关键晶圆、先进封装或物流节点发生非计划中断，并下调可售bit产量或客户交付，且恢复期跨越正常维护窗口。

## 竞争者中断转化为Micron份额 (pol_ff3a0a91823cdc8ed4eb)

- Direction: LONG
- Match scope: 三星电子、SK海力士的DRAM/NAND制造或封装中断、产量与预计恢复日期，以及Micron替代交付能力、客户分配与新增订单。
- Activation semantics: OR (any one complete Condition)

- C1: 三星电子或SK海力士正式确认关键DRAM或NAND合格产出下调及预计恢复日期，同时Micron确认自身交付未受损并获得对应的新增客户分配、订单或交付份额。

## FY2027盈利共识向高位收敛 (pol_91de6c49a351d4c52d90)

- Direction: LONG
- Match scope: 同口径FY2027收入/EPS卖方估计分布、财报后连续快照、中位数、上下分位和样本口径。
- Activation semantics: OR (any one complete Condition)

- C1: Micron发布新的经营信息后，至少连续两个同口径共识快照显示FY2027收入与EPS中位数共同上修，且上下分位区间整体向高位收窄。

## FY2027盈利共识广泛下修 (pol_18375c047cd41dcb080f)

- Direction: SHORT
- Match scope: 同口径FY2027收入/EPS估计时间序列、覆盖分析师上/下调广度、中位数和分布迁移。
- Activation semantics: OR (any one complete Condition)

- C1: Micron出现新的经营证据后，至少连续两个同口径共识快照显示FY2027收入与EPS中位数共同下调，且超过半数覆盖分析师下修、分布整体向低位迁移。

## 消费终端集中削减DRAM需求 (pol_44f6f2f6aef8fe3247f8)

- Direction: SHORT
- Match scope: 主要PC/手机OEM的量产基础DRAM配置、采购延期/取消、库存目标、渠道库存与终端sell-through。
- Activation semantics: OR (any one complete Condition)

- C1: 至少两个具有全球销量影响的PC或手机OEM在同一产品周期降低量产机型的基础DRAM配置并削减或延期采购，且库存升至各自主张的正常目标以上。

## AI PC提高基础DRAM容量 (pol_e8c333d452eb6a5674eb)

- Direction: LONG
- Match scope: AI PC量产平台、主流机型基础DRAM容量、零售sell-through、OEM采购和Micron客户端bit shipments。
- Activation semantics: OR (any one complete Condition)

- C1: 覆盖多个主流量产PC机型的新AI平台把基础DRAM容量提高至少一个商业配置档位，并在首个完整销售期实现终端sell-through与OEM DRAM采购同步同比增长。

## AI手机提高基础DRAM容量 (pol_89acbb844ab129e28c32)

- Direction: LONG
- Match scope: 苹果、三星等头部手机的主销量机型、基础DRAM容量、激活/零售、移动DRAM采购与Micron移动bit shipments。
- Activation semantics: OR (any one complete Condition)

- C1: 苹果或三星等全球主要手机平台把主销量量产机型的基础DRAM容量提高至少一个商业配置档位，并在首个完整销售期实现激活或零售与移动DRAM采购同步增长。

## HBM失利释放传统DRAM供给 (pol_6cc7553c86a52b4d1466)

- Direction: SHORT
- Match scope: Micron HBM客户资格、封装良率、订单计划、DRAM晶圆分配、传统DRAM合格bit出货与订单满足率。
- Activation semantics: OR (any one complete Condition)

- C1: Micron下调已披露的HBM量产与履约基线，把原HBM晶圆资源转回传统DRAM，并确认传统DRAM可交付合格bit增加。

## HBM增量需求继续挤占传统DRAM (pol_4fbb78816d7863893f09)

- Direction: LONG
- Match scope: 主要AI平台最终量产BOM、HBM容量/层数、系统部署量、Micron绑定订单及HBM晶圆/先进封装分配。
- Activation semantics: OR (any one complete Condition)

- C1: 主要AI平台最终量产BOM把每加速器HBM容量提高到已知计划之上，并使Micron获得新增绑定订单且进一步提高HBM晶圆或封装分配。
- C2: 主要AI平台把已批准的量产系统部署量上调，并使Micron获得超出当前合同基线的新增绑定HBM订单且进一步提高HBM晶圆或封装分配。

## 中国DRAM进入海外主流量产 (pol_9173828b029d26dab73d)

- Direction: SHORT
- Match scope: CXMT等中国DRAM供应商、非中国主要OEM、主流DDR/LPDDR量产资格、采购许可、出口与重复出货。
- Activation semantics: OR (any one complete Condition)

- C1: CXMT等中国DRAM供应商取得至少一家非中国全球主要OEM的主流DDR或LPDDR量产资格和适用采购许可，并连续两个交付期商业出货。

## DRAM提前采购转为去库存 (pol_dc8910e0a47064ba8786)

- Direction: SHORT
- Match scope: PC/手机OEM与渠道的DRAM库存目标、库存天数、订单削减/延期、book-to-bill及终端sell-through。
- Activation semantics: OR (any one complete Condition)

- C1: 至少两个主要OEM或渠道主体确认DRAM库存高于各自正常目标并同步削减或延期订单，且同期终端sell-through低于采购、无法解释库存上升。

## CXL扩大服务器DRAM净容量 (pol_ba44e8df4267758d9039)

- Direction: LONG
- Match scope: 主要CSP/OEM的CXL量产部署、主机DRAM与池容量、可比工作负载总DRAM采购、供应商资格和Micron份额。
- Activation semantics: OR (any one complete Condition)

- C1: 主要CSP量产部署CXL内存池后，新增池容量超过被替代的主机DRAM、可比工作负载总DRAM采购上升，并确认Micron为量产合格供应商。

## CXL池化降低DRAM净采购 (pol_3b1b86a10edc1c1b864b)

- Direction: SHORT
- Match scope: CXL池化/分层的生产部署、可比工作负载总DRAM容量、主机与池采购、供应商份额和Micron订单。
- Activation semantics: OR (any one complete Condition)

- C1: 主要CSP量产CXL池化或分层后，可比工作负载总DRAM容量和采购低于旧架构，并披露Micron池化产品新增量不足以抵消其主机DRAM订单下降。

## 中国恢复Micron采购准入 (pol_6f1d62d831892cfa91db)

- Direction: LONG
- Match scope: 中国网络安全/关键基础设施对Micron的采购限制、正式许可、生效范围、受限客户恢复采购和区域出货。
- Activation semantics: OR (any one complete Condition)

- C1: 中国监管正式缩小或取消对Micron的现有限制并生效，且至少一家此前受限的重要客户恢复Micron产品的量产采购或区域出货。

## 中国扩大Micron采购限制 (pol_e28a1b1e8eedc9bf53f5)

- Direction: SHORT
- Match scope: 中国对Micron的新增采购禁限、关键客户/产品范围、生效日、订单取消、区域库存重分配和市场份额。
- Activation semantics: OR (any one complete Condition)

- C1: 中国正式把Micron采购限制扩大至此前可服务的重要客户或产品类别并生效，导致可识别订单取消、区域库存转移或份额流失。

## 限制中国内存转化为Micron订单 (pol_57a710424ce10ddd34db)

- Direction: LONG
- Match scope: 美国/盟友对CXMT/YMTC采购的约束性规则、适用OEM、产品范围、生效许可及订单转向Micron。
- Activation semantics: OR (any one complete Condition)

- C1: 美国或关键盟友实施对主要OEM具有约束力的CXMT或YMTC主流内存采购限制，并由受影响OEM把可识别量产订单转向Micron。

## 新节点形成持续非价格降本 (pol_7bda98ed38ceabd5d415)

- Direction: LONG
- Match scope: Micron新DRAM/NAND节点量产良率、可比每bit成本、ASP变化和毛利率成本桥接。
- Activation semantics: OR (any one complete Condition)

- C1: Micron确认新DRAM或NAND节点达到稳定量产良率，并在ASP环比涨幅放缓或转平的至少两个连续季度实现可比每bit成本下降且成本或mix继续正向贡献毛利。

## 制程或封装良率损害经济性 (pol_7a415b51d22b5270ab7f)

- Direction: SHORT
- Match scope: Micron新节点、HBM堆叠/先进封装良率、量产计划、报废重工、单位成本、高端产出与毛利指引。
- Activation semantics: OR (any one complete Condition)

- C1: Micron确认新节点或HBM先进封装良率低于量产计划，并因报废或重工上调单位成本、下调高端产出或下调毛利率指引。

## 多产品多客户扩大高端利润组合 (pol_82f070e721f50f0bd599)

- Direction: LONG
- Match scope: Micron HBM、服务器内存、企业SSD的客户量产资格、重复出货、CMBU/CDBU收入利润率及同期ASP。
- Activation semantics: OR (any one complete Condition)

- C1: Micron至少两个高端产品家族分别在多个独立客户平台完成量产资格并进入重复出货，且在同期ASP涨幅放缓时CMBU或CDBU收入/利润率继续提高。

## HBM内容再次下调削弱高端组合 (pol_9858f29d38daac1ac509)

- Direction: SHORT
- Match scope: NVIDIA VR300及其他主要AI平台最终BOM、HBM4E层数/容量、Micron订单分配和高端收入。
- Activation semantics: OR (any one complete Condition)

- C1: NVIDIA VR300最终量产BOM把每GPU HBM4E容量进一步降至512GB以下，并使Micron下调对应已规划HBM订单、分配或高端收入。
- C2: 除NVIDIA VR300外的另一主要量产AI平台降低每加速器HBM层数或容量，并使Micron下调对应已规划HBM订单、分配或高端收入。

## 战略协议下限保护周期毛利 (pol_ae6303d518afce4bd609)

- Direction: LONG
- Match scope: Micron战略协议价格下限、覆盖产品/数量、市场DRAM/NAND价格、实现ASP、合同毛利和履约量。
- Activation semantics: OR (any one complete Condition)

- C1: 可比DRAM或NAND市场价格跌破战略协议下限后，Micron确认受覆盖产品实现ASP高于市场、合同毛利仍高于历史周期峰值且履约量未明显下降。

## 战略协议上限压缩盈利弹性 (pol_cb41fb89c61bd57d0545)

- Direction: SHORT
- Match scope: 战略协议价格上限、覆盖产品、市场价格、Micron实现ASP及收入/毛利桥接。
- Activation semantics: OR (any one complete Condition)

- C1: 可比DRAM或NAND市场价格升至战略协议上限以上后，Micron确认受覆盖产品实现ASP明显落后市场，并因价格上限下调增量收入或毛利预期。

## 关键投入冲击推高制造成本 (pol_c8b3001cc1fac69def5e)

- Direction: SHORT
- Match scope: 300mm晶圆、化学品、能源、水、关键设备的供应中断/配给/涨价及Micron单位成本、产量、利用率和毛利率。
- Activation semantics: OR (any one complete Condition)

- C1: 关键投入供应商或Micron确认300mm晶圆、化学品、能源、水或关键设备发生配给、中断或不可转嫁涨价，并使Micron下调产量/利用率、上调可比单位成本或下调毛利率指引。

## 新增产能错配形成低利用率 (pol_1090d9ad1d4a1e31e622)

- Direction: SHORT
- Match scope: Micron Tongluo、新加坡、美国新厂的合格产出、已披露爬坡计划、实际利用率、订单、折旧、减值、单位成本与现金回报。
- Activation semantics: OR (any one complete Condition)

- C1: Micron在Tongluo、新加坡或美国任一新厂形成客户合格产出后，因订单不足把利用率下调至该项目已披露爬坡计划以下，并确认由该厂折旧、减值或单位成本造成毛利或现金回报下调。

## HBM或服务器内存质量事故 (pol_2759d37860e386284043)

- Direction: SHORT
- Match scope: Micron HBM/服务器内存量产产品的可靠性、兼容性、客户停供/资格、返修保修、高端出货与利润。
- Activation semantics: OR (any one complete Condition)

- C1: Micron量产HBM或服务器内存发生公司或量产客户确认的可靠性或兼容性事故，导致该客户停止接收相关量产产品，并使Micron下调相应高端出货或利润。
- C2: Micron确认量产HBM或服务器内存的可靠性或兼容性事故，在财务报表中确认相关退货、返修或保修费用，并下调相应高端产品利润。

## 企业SSD进入多客户重复部署 (pol_17b9ee999d1e3ca3ab45)

- Direction: LONG
- Match scope: Micron Gen6/245TB QLC企业SSD、主要客户量产资格、重复采购、生产部署、出货与CDBU结果。
- Activation semantics: OR (any one complete Condition)

- C1: Micron确认Gen6或245TB QLC企业SSD在至少两个独立主要客户平台完成量产系统资格，并在至少两个交付期进入重复采购或生产部署。

## 企业SSD故障撤回量产资格 (pol_07c01cd6da0c52f29fe6)

- Direction: SHORT
- Match scope: Micron Gen6/高容量QLC SSD控制器、固件、耐久性/兼容性、主要客户量产资格、部署、订单和出货。
- Activation semantics: OR (any one complete Condition)

- C1: Micron量产Gen6或高容量QLC SSD出现经公司或主要客户确认的控制器、固件、耐久性或兼容性缺陷，导致客户暂停生产部署或撤回资格并削减订单/出货。

## 超高容量QLC成为标准AI数据层 (pol_df1332e7ce712ae0dc0c)

- Direction: LONG
- Match scope: 主要CSP/OEM的AI数据层、122TB/245TB QLC SSD标准架构、每集群容量、Micron量产资格和重复采购。
- Activation semantics: OR (any one complete Condition)

- C1: 一家主要CSP或OEM把122TB/245TB级QLC SSD纳入量产标准数据层并提高每集群部署容量，且确认Micron为合格供应商并进入重复采购。

## 软件效率降低AI工作负载SSD强度 (pol_220988f8255d00da9ea3)

- Direction: SHORT
- Match scope: 主要CSP的生产AI工作负载、压缩/KV cache/去重、优化前后本地SSD容量、部署规模与企业SSD采购。
- Activation semantics: OR (any one complete Condition)

- C1: 主要CSP把压缩、KV cache管理或去重应用于一个已上线的生产AI工作负载，披露该工作负载优化前后的本地SSD容量下降，并同步下调对应企业SSD采购或部署量。

## NAND有效供给提前反转紧张 (pol_0e87268a0a6c902e6295)

- Direction: SHORT
- Match scope: 三星、SK海力士/铠侠等主要NAND供应商、2H27前合格bit shipments、库存、lead time与合同价格。
- Activation semantics: OR (any one complete Condition)

- C1: 在2H27前，一家占全球NAND出货约20%或以上的供应商确认合格bit shipments高于原计划，并伴随行业/渠道库存上升、交期缩短和合同价转跌。

## 主要NAND项目延期延长短缺 (pol_8ae20cddcbcd95e5df29)

- Direction: LONG
- Match scope: 主要NAND供应商原定2027的量产良率/客户资格里程碑、延期幅度、行业库存、lead time和分配。
- Activation semantics: OR (any one complete Condition)

- C1: 一家重要NAND供应商把原定2027年的量产良率或客户合格产出里程碑正式推迟至少两个季度，且同期行业库存仍低、交期或分配仍紧。

## 中国NAND进入海外高价值市场 (pol_45eb7e1c000d1b922426)

- Direction: SHORT
- Match scope: YMTC等中国NAND供应商、非中国企业/终端客户、量产资格、准入许可、重复shipment和收入/高价值mix。
- Activation semantics: OR (any one complete Condition)

- C1: YMTC等中国NAND供应商在至少一家非中国重要企业或终端客户取得量产资格与适用准入，并连续两个交付期重复出货，使其高价值产品收入份额提高。

## PC换机提高基础NAND容量 (pol_83b41a8f6712532b7514)

- Direction: LONG
- Match scope: 主流PC量产机型基础NAND容量、零售sell-through、客户端NAND/SSD采购、Micron出货和渠道库存。
- Activation semantics: OR (any one complete Condition)

- C1: 覆盖多个主流量产PC机型的平台把基础NAND容量提高至少一个商业档位，并在首个完整销售期实现sell-through与客户端NAND采购同步增长且渠道库存保持正常。

## 手机换机提高基础NAND容量 (pol_89cd5e7a7c9bc8de372e)

- Direction: LONG
- Match scope: 苹果、三星等头部手机主销量机型、基础NAND容量、激活/零售、移动NAND采购、Micron出货和渠道库存。
- Activation semantics: OR (any one complete Condition)

- C1: 苹果或三星等头部手机平台把主销量量产机型的基础NAND容量提高至少一个商业档位，并在首个完整销售期实现激活/零售与移动NAND采购同步增长且渠道库存正常。

## 替代存储减少本地企业SSD (pol_184c5d854921bdc64aec)

- Direction: SHORT
- Match scope: 主要CSP/OEM的AI数据层介质mix、本地企业SSD与HDD/网络分层、量产部署、可比容量、采购和shipment。
- Activation semantics: OR (any one complete Condition)

- C1: 主要CSP或OEM把一类量产AI数据集或缓存从本地高容量SSD迁移到HDD或网络化分层，并确认可比部署的本地SSD容量及企业SSD采购或shipment下降。

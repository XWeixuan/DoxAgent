# IONQ Massive GOLD 标注合同 · 2026-11-13

权威依据为用户本对话的七条有效标注准则、12个改判校准和50/50确认；参照 `docs/by-distribution-gold-annotation-rules-v2-20261028.md`。本层是轻量Recall-first业务初筛，Massive全市场环境要求有可解释的主体或独立正文事实，不能将背景词当新闻。

每条按完整冻结title、summary、body × IONQ独立判断；不联网补正文、不调用其他模型、不按关键词脚本生成标签，不读取生产规则/预测或其他GOLD。以原文事实说明原因，不要求订单、显著性、交易价值、新颖性或已商业化，不按市场报告/诉讼/通知等体裁排除。

IONQ业务圈层：IonQ及其实际量子业务主体；量子计算及竞争架构、量子处理器/纠错/控制软件、量子网络通信、量子传感与精密时钟、量子安全/后量子密码，以及有明确原文联系的相关产品市场、技术、企业经营、采购投资、政策和正式活动。不要求报道点名IonQ。核心或业务近邻边界合理时偏向RELATED。

排除：quantum的营销隐喻、无可识别量子业务联系的纯材料物理数学、一般AI/传统安全/传统通信/其他行业。公司介绍可能有实质独立产品经营事实，可召回；但只有“未来可能应用于量子”等罗列、市场报告公司大名单、导航/免责声明或没有实质量子产品事实的泛量子威胁背景，不自动相关。不能把一条边缘行业背景升级成多层想象中的需求。

输出每对一行JSON：`pair_id`,`ticker`,`label`,`category`,`reason`,`mixed_topics`,`confidence`。

- label: RELATED / IRRELEVANT / INSUFFICIENT。
- category: DIRECT / INDIRECT / INDUSTRY_BACKGROUND / IRRELEVANT_TEMPLATE / INSUFFICIENT；前两者对应RELATED，中两者对应IRRELEVANT。
- reason: 中文，指出原文实质事实及具体业务联系或排除边界；不得只有“量子相关”等泛评。
- mixed_topics: 正文有实质独立多主题才true，导航/公司名单不算。
- confidence: HIGH / MEDIUM / LOW。有事实但边界有争议仍作低置信业务判断，仅有效信息确实不足才INSUFFICIENT。

每篇逐一读完整证据。可用程序分页读取、保存已经作出的人工判断和检查ID覆盖，不能用程序关键词或另一个模型代替标注。不要按预期相关比例改标签。

只使用本次指定的gpt-6.1-terra high，不派生代理。分批写入各自输出文件，保留进度，核对全部ID及无重复。验证标注员向主代理仅回报完成数和文件路径，不回报任何具体文章、标签、理由或类别统计；验证内容要到常规候选冻结后才开放。

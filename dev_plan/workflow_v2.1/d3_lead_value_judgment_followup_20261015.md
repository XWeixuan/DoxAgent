# 从Lead能否前置判断Policy粒度与价值（2026-10-15）

关联证据：intc_be_d3_lead_topic_policy_16_samples_20261015.md，以及eval/d3_lead_wave_policy_audit_20261015/lead_topic_final_16_samples.json。

本次8组INTC、8组BE，共35条Lead关联（33条不同Lead）、37条不同最终coverage Policy。目的性抽样，不能推算总体低价值率；不引入新金融判断或重新研究截至日后事实。

## 形态

实际合同没有theme字段，本轮所称theme为Planning Topic。Lead={name,lead,ref}：lead正文通常已经是短研究提案，包含历史基线、未来可能事件、经济传导、未知问题及禁止误推。Topic={name,owner,brief,ref}：brief将数条提案组成可执行研究范围，明确与相邻课题分工，但不固定Policy数量。最终Policy包含title、transmission、match_scope、多个criterion及calibration，才冻结交易判断尺度。

## 判断边界

只凭现有Lead，能够判断：明显重复或共享同一机制；已有事实冒充新事件；泛行业故事与目标ticker缺乏明确连接；缺少分量依据；将一个程序步骤或极细参数提前当独立研究主题。足以对研究优先级、合并候选和待核实问题作决定。

不能可靠判断：最终Policy是否重复；同一问题的两个阶段是否应共同构成一条规则；实际触发是否足够大、足够新、足够早；多层传导最终能否建立；最终数量会是一还是多。它们依赖研究结果、ticker层暴露、市场基线、实际criterion/calibration及相邻成稿比较。

三个反例：Fremont题目虽窄，但Lead已给出75.3百万美元相对半年capex77.823百万美元，不能按窄主题剔除；一条VLSI/EC/客户赔偿Lead分为三种法律责任范围，不当然是碎片；Mobileye组织继任Lead后来分出Mentee工具复用规则，这种具体独立席位并未在Lead阶段冻结。

两类可前置预警：Mobileye研发补助以子公司经营利润论重要性，INTC层重要性尚未建立；e-SAF和制氢取水Lead承认未证明Bloom入选/受影响，仅行业或SOEC适配不足以证明BE应独立交易。这支持条件性投入或与主课题合并，不能直接证明未来所有相关Policy均低价值。

## 对前一轮建议的修正

Planning适合承担**研究议程收敛**，结合Lead、D1/D2与全部Topic判断是否值得独立投入，而不是仅凭Lead预测最终Policy合格性。重要性尚未知应区分“合理且值得核实”与“已知联系弱、只是可想象”。允许零Policy研究结论，不预设每题必产规则。

用户要的最终Policy主题审查与碎片合并，需要完整成稿，因此其最终决策主责更适合Integration：主Build结束后结合全量候选、来源及经济机制作组合审查，区别边界修补、共享机制合并、外围淘汰与真正需要补研。补研结束后仍需落实同一决定；不能无限补证据来使每个已有题都可发布。

所以应是Planning前置筛研究价值、Integration后验决定独立Policy席位。若只能选一个节点完成“完整审查将发布的Policy主题，合并碎片并淘汰低重要性规则”，在本次澄清的目标下，应选Integration，前一轮优先Planning的说法需要作此限定。并非建议新增打分配额、强制一题一Policy或按热门程度删题。

本轮未修改代码、提示或生产。

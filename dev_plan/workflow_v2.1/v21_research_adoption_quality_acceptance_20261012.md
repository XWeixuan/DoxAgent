# 迁入前研究资产验收记录

日期：2026-10-12。对应生产部署方案及 `eval/v21_production_release_20261012` 冻结清单。本文件记录迁入前验收；生产切换结果另记部署验收报告。

- MU 采用 `mu-v21-20261010-02-d3-resume1`，262 Policies；BE 采用 `be-v21-20261011-01-d3`，124 Policies；INTC 采用 `intc-v21-20261011-01-d3`，173 Policies。逐套核对源发布四文件 SHA、D2 来源和由 canonical Policy 重构的 projection。源文件保持不变。
- 三套保留 PARTIAL 和实际 remaining research gaps/late Lead coverage。未将未收录线索伪装成已完成研究，也没有重新跑整套研究。当前验收证明冻结已接纳结果的合同、来源和可消费性，不证明所有研究内容不存在遗漏。
- INTC 的四个 `final_edit_incomplete` 确有原 agent 已写出的最终编辑，因其他 Policy 前缀的 Condition ID 被拒收，旧触发文本仍被保留。派生 release 采用这些已有编辑，按本 Policy 合同把外来 `pol_...:C#` 归一为局部 `C#`，保留 Policy ID，并重新构造 projection/四文件哈希。没有增造研究结论。
- 四项修订分别纠正：chiplet SHORT 要求已经存在的 Intel 可访问商业开放权被撤回；48D 区分永久经济权利和支付时点；融资按实际每股净效应评价并去除碎片阈值；secure enclave 区分永久现金/股权条款和到账时点并去掉任意金额阈值。具体全文、来源路径、前后 SHA 和 ID 修订见 `intc-repair-evidence.json`。
- 仅关闭已实际修正的四项 final-edit 标记及对应缺口说明；其他缺口原样进入派生 release。源版本未修改。
- 目标版本由生产 reservation 动态分配；不复用源版本覆盖生产历史。研究来源 Event v1 保留在 PolicySet 内，激活时另沿用当前生产 Event 引用。
- 正式 Worker 所需 D1 文件按被接纳 report/attempt 选择并核验 SHA；D2 使用最终 handoff 文件，不搬同路径被覆盖的历史尝试文件。发布正文同时保存 SQLite 本地正文与 SHA 缓存，支持大于 2 MiB 的本地 D2/D3。
- 隔离环境真实调用已验证 W2 召回、W3 显式 Condition/合法增量；真实 MAINTAIN 第三次验收为 PARTIAL，实际输入包含完整 MU D2、C1/C3/C5、Future Nodes、Entity Relations/Network 和当前 Event 材料，上游 warnings 为空。没有提交测试交易意图或修改生产引用。

证据：`research_quality.json`、`intc-repair-evidence.json`、`adoption-manifest.json`、`maintain-input-proof.json`、`model-smoke.json`（后两项从远端收集）。离线验收与真实模型验收不互相替代。

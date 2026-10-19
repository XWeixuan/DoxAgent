# NEW Case 未进入 W3：CoreWeave / RKLB 排查

日期：2026-10-16，北京时间。本轮只读生产与本地代码、离线重放 Router，不修改提示词、业务代码、Case 或路由结果。

## 具体 Case 与真实路径

标题：CoreWeave Slides on Debt; TSMC Moves on Outlook | Stock Movers。

Case：case_72e2bd20a8434f8aa4ae781481e6f736；ticker=RKLB。北京时间 2026-10-15 23:23:25 准入，23:26:04 完成。

| 项目 | 落盘结果 |
| --- | --- |
| W1 R2 | NEW，normal |
| W2 R2 | policy_hit=false，policy_related=true，normal |
| W2 条件映射 | policy_ids=[]、hit_condition_ids=[]、matched_condition_ids=[] |
| W2 R3 | condition_results=[] |
| 初始路由 | ARCHIVE |
| 副作用 | archive_message、emit_delta，均 COMPLETED |
| W3 | 无 W3 Case、无阶段记录 |

因此，本例是 NEW + RELATED + NO HIT，不能把“未命中”当成“不相关”。没有出现已路由 W3 后排队或执行失败的情况。

## 直接路由原因

router.route_runtime_case 对正常置信 NEW 保留例外：policy_related=true、policy_hit=false、related_new=false 时 ARCHIVE，其他 NEW 进入 W3。alignment_flags 需要在 matched_condition_ids 中找到被 R3 标记 is_new=true 的条件引用；空映射/空 R3 得不到 true，最终 related_new=false。

这条例外已写入原 W3 重构实施方案的入口表，不是最近并发修复新增的规则。生产 scheduler 镜像为 doxagent-v2:main-0d1791ef，其 router.py SHA256 与本地当前 HEAD 对应文件一致：6e7667fb5948384e020f9feda3dbdec409527c21dd16d2d6f4046e3b95cc53fb。没有证据将本例归因于路由补丁未上线。

使用真实 W1/W2/R3 回执离线重放：本例 ARCHIVE；仅将 RELATED 改为 false 则 W3；改为 HIT 则 W3。对照只验证 Router 分支，不是重新研判新闻。

## 真正暴露的问题

1. **提示词与路由契约错位。** 本 Case 冻结的 W2 R2 提示词在 matched conditions 中要求只记录“明确命中、完整满足”的 Condition，不把仅相关或部分成立当成命中；而 Router 又用 matched_condition_ids 作为 RELATED 新事实判断的对象集合。RELATED=true 但没有条件映射的结果可通过当前 schema 和服务验证。
2. **缺失判断被当作否定判断。** R3 没有给出条件判断，不等同于对某个相关事实明确给出 is_new=false；代码却将两者折叠为 related_new=false，并触发归档。
3. **W1/W2 判断事实不一致。** W1 R2 用 CoreWeave 的 CDS/债务新闻作为 NEW 理由，指出加载的 RKLB 事件均不能覆盖它；W2 评估的是 RKLB 的 Neutron 延期，并认为延期已被基线吸收。对 RKLB Case，W1 没有在理由中确认 RKLB 相关新增事实，或说明 CoreWeave 新闻对 RKLB 的事实联系。后续 W1 R3 又抽取了 Peter Beck 不冒险赶进度的表态，与 R2 的 NEW 理由不是同一个事实。
4. **路由解释信息不完整。** route.reason 只保存 NEW、confidence、policy_hit，不含 policy_related 与 alignment 状态，容易把此例误读为 NEW+不相关却归档。

## 同类范围

查询生产创建时间从 2026-10-15 起、已有初始 route 的 NEW Cases：183 个 policy_hit=false/related=false，全部 W3；43 个 policy_hit=false/related=true，全部 ARCHIVE。

43 个 RELATED Case 的 matched_condition_ids 均为空；其中 42 个 R3 条件结果为空，1 个非空。即使 R3 非空，没有 matched 条件映射也无法让 alignment_flags 的 related_new 成立。这是系统性契约风险，但不代表这些消息都具备交易价值；本轮也没有对它们逐条作业务重评。

## 建议修复边界

- 若继续保留“RELATED 但无新增事实则归档”，只在相关对象映射完整、R3 明确判断相应事实不新时适用。缺少映射/覆盖不能直接视作 false；应进入 W3 完成独立判断，或走明确的输出修复流程。
- 对齐 W2 R2 的 HIT 条件与 RELATED 条件语义，分别输出可追溯对象；R3 必须覆盖这些对象。保持 schema、提示词、服务验证和 Router 一致，不能只修展示或单独强行填条件 ID。
- W1 针对绑定 ticker 确认核心新增事实，修复混合多公司快讯中的对象漂移；保持 R2 NEW 依据与 R3 抽取事实一致。
- route.reason 增补 policy_related、alignment 覆盖状态与实际分支原因，便于解释。
- 如果业务决定所有正常置信 NEW 都进入 W3，则应明确删除 RELATED 归档例外。这是入口规则变更，不能把原方案曾保留的规则描述成已实现错误后悄然删除。

本轮仅诊断，未执行上述修复或重跑原 Case。

## 证据

eval/w3_route_audit_20261016/：coreweave_case_receipt.json、router_replay.json、new_route_groups.json、frozen_prompt_contract.txt、frozen_w2_r2_excerpt.json、production_router.json。

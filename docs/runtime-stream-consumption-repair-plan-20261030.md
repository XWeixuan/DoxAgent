# Runtime消息消费失效：根因、精益修复与发布验证

## 已确认根因

北京时间10月30日01:24只读取证：BE、CEG、COHR、INTC、MU、NBIS的persistent_runtime_v2游标分别停于2062、143、300、5004、5101、856，队首均被StandardMessage.body的dict→str校验挡住，待消费47/23/82/171/127/77，共527条。逐条显式解码原NativeContent引用后，正文16,271–41,988字符均可通过相同模型校验。不是正文文件丢失，也不是没有消息。

`MessageBusV2Repository._materialized_members`把两列JSON别名成relation_json/standard_json；NativeContent.row仅对明确JSON列名展开，别名不在名单，后续却直接model_validate_json。正文或嵌套字段达到16KiB会以引用落盘，小正文通过，大正文必然失败；顺序流不前进，后续新文被队首阻挡。两个单独正确的约束（薄读不展开任意列、业务需要完整正文）在边界没有接上。

源码追溯：join别名来自f7dc71723（2026-09-02）；NativeContent行白名单c0887ce84（09-14），后续00ee814cd增加definition标记识别。并非只因Scheduler416fb2fa旧于Bus2fe86d0f，当前main同一缺陷仍在，单纯重build不足。MU/COHR队首发布时间为10月28日17:11/17:14Z，早于本轮统一I/O发布，说明此次验收遗漏了既有端到端消费失败。

Read中SNOW当日11条可以从生产API读库路径返回，INTC/BE当日0、ALL各返回20条历史；Read runtime checkpoint=head且无当前source error。前端默认CURRENT_TRADING_DAY，相关ticker没有当日case会显示空，不等于清库。另有独立API查询超时，不用本修复冒充全部UI问题已解决。

## 修复边界与步骤

1. 在真正物化StreamMember/StandardMessage的业务边界，先json解析、调用已有NativeContent.decode，再model_validate；一次必要展开，不重序列化整个正文，不扩大全局row白名单，不把所有薄状态查询改为宽读。
2. 仅对选中的stream_item成员解码，保持原limit、顺序、ARTICLE_ONCE、consumer cursor和持久化去重。真实缺失/损坏仍明确失败；不skip、不挪游标、不清库、不重发旧消息。
3. 用已有真实接纳/发布fixtures覆盖跨16KiB正文、多成员及嵌套大metadata、重启读取、无关大正文不被解码、缺失内容不推进游标；再通过Scheduler→Runtime执行入口验证case接纳与游标推进、二次tick不重消费。先复现红灯再修改。
4. 同时审查同模块其它JSON别名→模型边界，限定修复实际存在的漏解码，不新建兼容层/恢复队列。Owner发布时点与Runtime实际消费时点分开，今后观察同时跟踪游标、真实case和UI，不再仅PUBLISHED标签证明可消费。
5. 相关测试与冻结已提交归档Review，追加changelog，推送main。从正式release归档build相关生产消费者。优先Scheduler与Bus；核对BODY legacy后链路与MonitoringTool实际角色，更新必要消费者，保留Profile、数据库、inflight W作业及现有恢复协议，不夹带其它会话未提交交易改动。
6. 发布前后保存相同游标/队首/积压账；逐个证明6ticker从原队首可读、游标实际增长、Runtime case/去重或明确历史接纳决定正常落盘，并核对Read可见。旧文可能被既有业务窗口拒绝，不强行触发历史交易；禁止跳过真正应处理新消息。
7. 观察资源及本地编排跨度，记录积压自然恢复与新稿。恢复两小时一次、至少24小时只读观察，以最终修复版本稳定生效起点明确续计，旧证据和版本切换保留。

## 未混入本次紧急修复

W3 claim已有6个真实receipt，claim跨度mean3.646秒/max8.011秒，EXPLAIN为SCAN runtime_v2_effects+临时排序，约17,532历史rowid；索引/OR历史扫描属于独立非LLM耗时问题。当前优先修复导致完全不消费的确定性契约错误，不在紧急补丁里重写W1/W2/W3调度。

Site匿名内存增长、Reuters/IBKR/Calcalist/DIGITIMES访问失败和API慢查询继续记录，不用提高全局并发或延长超时掩盖。

## 实施/验收记录

待补：代码、红绿回归、正式版本与逐ticker生产续作证据。原始故障证据在`eval/lean-architecture-observation-20261029/20261029T171819Z/`；本次补丁证据在`eval/runtime-stream-consumption-repair-20261030/`。


### 开发验证与Review

- 新增4项：即时/缓冲两种大正文及嵌套metadata跨重启传入Scheduler（原游标和重复tick验证）；真实SQLite Runtime执行并保留唯一case；limit=1不加载后续缺失大正文，遇到真正缺失时游标不动。旧实现先红（同生产body dict→str），补丁后4项通过。
- 组合套件首跑64通过、11失败：10条为旧CPU admission测试桩缺少10月28日已引入的current_revision_admitted，1条Benzinga精确字典断言未包含既有validated_request_ids。未修改对应生产逻辑，更新桩为当前revision+worker ACK/max_age语义，未到期ACK复用（不要求每tick重写ACK），mock无发网证据仍断言空ID；相关合同重跑另记。
- 除Stream成员join外，同仓库其它实际模型别名均为payload_json，已在原codec名单。没有拓宽NativeContent.row白名单或增加任意列全对象解码；新代码每个选中对象JSON解析/解码一次，避免text展开→json dump→再次模型JSON解析。
- 实际消费者：Scheduler负责消费、Bus record_consumable会读流，BODY legacy finalize复用Bus接纳，Worker默认工具注册的monitoring.recent_events也会读流。用一份正式backend镜像更新以上4角色；API只查Read，不为此无关重启。Worker滚动沿现有持久化续作/进程归属协议，不添加W门禁。

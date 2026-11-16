# W3线程复用与Compaction：周四、周五生产勘察

2026-11-15。只读排查，未改代码、Prompt、Schema、线程状态或生产配置。

## 结论

担忧有实际依据，但不是所有Case都在共用长线程。约79%的Case是其物理线程的首个Case；约21%复用历史Case线程。复用高度集中，少数线程连续承载几十个Case，已经出现大量实际压缩记录。

更关键的设计事实是：**48小时是随复用不断延后的闲置过期时间，并非线程绝对寿命；历史Policy/Event关联累积并长期参与后续路由，也没有按Case数、token量或压缩次数轮换的机制。** 因此一个持续活跃的线程可以跨多日不断扩大关联面。

这证明了跨Case上下文累积与压缩暴露，并不能直接证明四个badcase是被旧上下文污染，也不能将全部SDK长耗时归为压缩。原生reasoning加密且summary为空的事实不变，本轮没有尝试解密或冒充隐藏思维链。

## 窗口和口径

本周四、周五按美东自然日：11/12 00:00至11/14 00:00 EST，即UTC11/12 05:00至11/14 05:00，北京时间11/12 13:00至11/14 13:00。按Trade阶段accepted_at纳入，不按页面更新时间；进入Trade但失败的Case也保留。

统计基于源库32921条阶段元数据（最早10/13），每个Case只计一次Trade，不能将Trade+Operations两个回合误算成两个Case。物理thread_id相同才算真正复用；不同子线程共享同一模板不算。某Case之前已有其他Case使用过同一thread，才计“历史复用Case”。

| 时段 | W3 Case | 物理thread | 历史复用Case | 复用率 | 单线程当期最多Case |
| --- | ---: | ---: | ---: | ---: | ---: |
| 周四 | 1065 | 890 | 207 | 19.44% | 27 |
| 周五 | 910 | 738 | 208 | 22.86% | 31 |
| 两天合计 | 1975 | 1598 | 415 | 21.01% | 48 |

两天中有46个thread处理至少2个当期Case，覆盖423个Case；其余1552个thread各处理1个当期Case。423与415不是冲突：前者包括复用链在窗口内的第一个Case，后者还识别从窗口前延续的线程。历史累计多Case的相关thread共53个，包括7个本窗口仅出现一次但此前已使用的thread。

## 长尾线程与压缩

对53个历史复用thread全部尝试读取原线程，48个仍有可读文件，5个缺索引；另对历史仅一个Case且文件可读的thread以seed=20261115随机抽60个作对照，不把它们等同全体单Case线程。

- 48个可读历史复用thread中，29个在窗口内有原生`compacted`记录，共127次。
- 单Case对照60个，未发现`compacted`记录。
- 不把压缩次数与受影响Case数混同：一次Case可多次压缩，也可能某Case完全没有压缩。
- 原生流有12行无法完整解析，本轮跳过并保留计数；127是成功解析记录的观察值，不声称覆盖丢失行或不可读线程。原线程读取约660.6MB，按精确路径、nice15、限时预算执行，没有全库导出或常驻采集。

| ticker / thread后缀 | 两天Case | 历史累计Case¹ | 窗口压缩 | 累积Policy/Event关联² |
| --- | ---: | ---: | ---: | --- |
| NBIS / 8055980a1c95 | 48 | 59 | 27 | 13 / 152 |
| NBIS / 1e18957f83f6 | 47 | 79 | 30 | 20 / 232 |
| COHR / 1617e60a3134 | 36 | 50 | 9 | 7 / 221 |
| MU / 3f5b5cad52e2 | 28 | 52 | 6 | 14 / 234 |
| BE / f9c97c819cf2 | 26 | 84 | 6 | 13 / 231 |
| IONQ / c81a719f3d6f | 18 | 23 | 5 | 7 / 78 |
| NBIS / 95a2b217b56f | 18 | 18 | 9 | 10 / 77 |
| IONQ / 6cc0dbd98564 | 18 | 29 | 3 | 5 / 109 |
| BE / 8a6622c8aaaa | 13 | 13 | 2 | 4 / 57 |
| NBIS / 709f059eb28f | 12 | 30 | 4 | 8 / 152 |

¹ 历史累计为本次采集可见阶段记录，不保证覆盖已退出数据；表内重度线程最早可追至11/6。² 关联数是采集时注册表快照，不是每个Case关联数，也不是窗口内新增数量。

例如NBIS的`01a1aee2-8337-7200-993e-1e18957f83f6`历史累计79个Case、窗口47个Case、窗口压缩30次，已挂20个Policy和232个Event关联。BE的`01a1ae33-8a7f-7333-b738-f9c97c819cf2`累计84个Case、窗口26个、压缩6次。这已经超出通常理解的“一段短暂关联会话”。

与用户四badcase对应：COHR/Fermi线程窗口36例、压缩9次；BE/Nebius线程窗口26例、压缩6次；两个RKLB badcase共用的`01a1ba20-d1bb-7e30-a55a-7c287b0411bb`累计31个Case，但本轮可读流未检出compacted。可见“长期复用”和“必然多次压缩”不是一回事，不能统一归因。

## 当前编排逻辑

### 1. 模板、Case线程、恢复身份是三件事

模板承载AGENTS/唯一Skill等公共指令，按兼容性生成；新Case没有可复用线程时，从模板准备独立子线程，Ready/Capsule属于其物理准备优化，不是把模板直接作为所有Case的业务对话。

同一Case的Trade与Operations必须使用同一个Case线程。恢复时优先找本Case已拥有的记录；若Trade阶段input_refs已经固定record_id，则恢复原记录，发现被别的Case占用会报身份冲突，不静默换新。已提交阶段读取原结果，不为了重试改用另一个线程。这些是防止重复执行的保障，应与跨Case复用分开评估。

### 2. 跨Case关联匹配

`W3ThreadRegistry.acquire()`在同ticker内、ACTIVE、compatibility_key一致、未过期的CASE线程中选择：

1. 先按当前W2的Policy关联尝试；HIT使用policy_ids，RELATED使用related_policy_ids，去重后按传入顺序遍历。
2. 再按实际Event匹配关联尝试。Event路径只将OLD的真实覆盖视为匹配，NEW中用于Prior的Event引用不直接作为命中；键经过范围/身份解析。
3. 对当前关联的候选按last_received_at降序、thread_id排序，优先空闲的记录。不是计算“所有Policy的最大交集”，也不是只绑定单一Policy。
4. 如果较高优先级关联存在候选但全部忙碌，立即返回未选中，走新Case线程准备；不会为了复用等待它空闲，也不会再降到其他关联。无匹配同样创建新线程。

这保留实时并发，但也意味着并发强时分散出多个线程，消息串行到达时更容易反复使用同一条热点线程。

### 3. 兼容性与上下文隔离边界

兼容key包含指令资产、冻结的Trade执行profile、工具/环境能力、W3契约版本、适用时的Trade/Ops Schema hash和入口layout。不同ticker、不同兼容key不会直接复用CASE线程。新公共模板的key可跨ticker共享指令，但CASE acquire仍限制ticker。

当前Case使用自己的冻结输入、研究manifest、context_ref和输出契约；这些控制本次可读数据与合法输出。**它们不等于清空原thread已有消息和工具历史。** 同一个thread继续执行，本次Agent仍处于包含此前Case回合或其压缩记忆的会话。换模型也不意味着重置历史。

Trade profile仍参与兼容性；当前Operations虽统一Luna/max/default，不能据此认为所有Trade模型分组已合流。原badcase报告的实际模型应以原生turn_context为准，不以逻辑profile替代。

### 4. 关联累积、续期与释放

选中线程后设置owner_case_id，递增lease_token，设置约20分钟lease_until，并将last_received_at更新为本次Trade接受时间，expires_at更新为该时间+48小时。

Policy关联在本次线程准备时INSERT OR IGNORE追加；Event关联在运营过程中追加。主键是record_id/link_kind/subject_key；source_case_id只保留该关联首次写入来源，不是所有历史命中清单，不能用links表直接算每次复用的确切理由。当前没有随新Case移除旧关联的步骤。

运营完成/终态释放owner，使同一线程再次可选；release不清空对话、不删除关联，也不重置原生压缩状态。因此A因P1入线程、后续Case兼有P1/P2而补入P2，再有仅关联P2的Case进入，从机制上可形成主题桥接。表中多Policy、多Event累积证明桥接条件存在，但本轮未逐次重建每条路由选择，不能把每次复用都断言为Policy触发；Event也是合法路径。

### 5. 过期和清理

维护检查线程是否活跃、当前owner是否还有在途Effect；活跃或仍需继续的线程不会简单删除。过期且需终结的Operations在原身份上标失败，释放owner；符合条件后标DELETING、调用原线程删除、再标DELETED。也有历史线程接管/兼容与失败准备恢复路径，不能把删除保护全部取消来解决增长。

这里的48小时因每次复用续期，持续活跃的线程可以存在超过48小时。源码未见按绝对创建年龄、累计Case数、累计token或compaction次数触发跨Case轮换的条件；压缩是底层SDK会话行为，不是Runtime主动按某个Case数重建干净会话。

## 风险判断与下一步

已证实：热点线程承载数十Case；关联集合只增不减；闲置TTL可持续续期；热点线程发生多次原生压缩。未证实：压缩分别消耗多少秒、是否直接导致badcase、旧Case结论是否被错误当作当前事实。`compacted`主要是完成记录，不是完整压缩开始/结束计时，不能用相邻消息空档当成压缩耗时，更不能把压缩存在等同进程被阻塞。

最值得后续设计评估的是跨Case历史继承的必要性，而不是再次优化SQLite或扩大并发。应将“同Case Trade→Operations与故障恢复必须保留身份”作为底线，独立评估跨Case继续同thread是否仍有足够业务收益；若保留，应研究清晰、有界的轮换条件和关联有效期，避免用持续续期代替生命周期边界。不要把缩短TTL当作充分解决：热点线程只要不断活跃仍可无限续期。

要证明污染需用少量相同冻结Case做干净线程/复用线程对照，分别核验时效判断、Prior来源、Policy身份和引用；要证明性能损耗需补SDK压缩区间证据。两者均不是本轮统计可以替代的结论。本轮只勘察，不实施策略或重跑交易。

## 证据和代码位置

统计：`artifacts/audits/w3_thread_reuse_20261115/metadata.json`、`summary.json`、`compactions.json`及采集脚本。原始业务正文、加密reasoning未导出。统计选取源stage而非Read展示；原流扫描覆盖48条历史复用线程及60条随机单Case对照。

主要源码：`src/doxagent/persistent_runtime_v2/w3_threads.py`的acquire/create_case/link/release；`w3_execution.py`的_thread、trade、operations及维护清理；`w3_context.py`的policy_associations/event_matches。三份当前源码经LF规范化的SHA256与生产Scheduler实际文件一致，因此上述不是只看未发布本地分支的推断。

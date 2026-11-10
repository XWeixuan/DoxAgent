# W3 大量显示 PENDING：CDC JSON subtype 回归与修复

2026-11-11（北京时间）。这是已执行排查/修复记录，发布及存量恢复结果在后文补齐。

## 结论与实际影响

实际 Runtime 已完成，Read/API 却没有更新，并非本次确认的模型/Job整体锁死。选定 BE Case `case_0ca2e5bfe7374ac5949181f2c507b967`：源 Trade于05:18:52、Operations于05:20:15 COMMITTED；06:52 API仍返回PENDING_W3、无W3决策，两个W3 attempt却均SUCCEEDED。另一 BE Case于06:41:49已完成Operations，API仍为PENDING。

06:58前的有界清单：219条这种错误回执，82个Case；81个源两阶段已完成，69个Read与源状态不一致，未知/回执遗失为0。其余包含仍在途或已通过其他正常回执显示完成的Case。清单会随自然业务增长，不把219当永久最终数。

这是**本会话上一轮W3性能修复遗漏CDC类型集成验证的回归**。当前证据不支持磁盘治理误删W3输入/Session或把Runtime运行整体锁死；不要为恢复显示而重跑模型或交易。

## 完整因果链与引入时间

1. `057a67e4`（03:57提交）将Case终态小字段合入Effect事务，使用SQLite `json_set`，避免整份冻结上下文重写。04:29:43版本发布后，本次清单最早错误于04:33:14出现。
2. `7e69c23a`（04:58提交、05:02发布）又将Trade/Operations业务字段改为增量JSON更新，扩大该类型回归对最终决策显示的影响。
3. SQLite JSON函数产生带JSON subtype的TEXT值；触发器内`json_object('payload_json',NEW.payload_json)`会把它嵌为对象，而不是原回执契约的JSON文本字符串。实际生产回执4438879、4438811等的payload_json均为dict。
4. `SourceOutbox.event()`只对字符串执行原Native展开，`projector.native()`也只把字符串当业务载荷，因此对象封装被当作SQL外层行，缺少业务`source`，投影报KeyError。
5. 既有隔离机制记录gap并继续推进checkpoint，所以Runtime source head和Read checkpoint可以完全一致；**追平checkpoint不代表没有投影缺口**。较早的PENDING展示留在Read，新的W3 attempt/模型回执仍能单独出现，造成“成功但一直阻塞”的表现。
6. 正常repair按相同错误路径重试，不能自动恢复；这是确定性格式问题，重启Scheduler、Worker或增加超时无效。

原优化目的和原子性仍有价值；本次不回滚为大对象整写，而是在CDC边界恢复正确封装。

## 对存储治理相关改动的专项检查

已阅读用户提供的`D:/DoxStorageLifecycle20261111/docs/storage-lifecycle-implementation-20261111.md`，核对生产角色和冻结源码：Runtime Scheduler仍为b8d9ed13/62d6be3311df；Worker为187d79f3/64c3564fce85，06:46刚完成存储治理发布。异常最早04:33，时间明显更早。

- 新Worker候选选定的是initialization_owners、snapshot_retention、session_retention，属于初始化快照归属与Session保留；没有改本次出错的Runtime Case更新和CDC封装。
- 活跃W3/Case原Job与回执仍在，选定Case两阶段COMMITTED；没有发现本次异常由冻结内容或W3 Session被删引起。
- `d08df6dd/187d79f3`主要调整一次性发布空闲门禁；其与06:46 Scheduler重启的发布交接另行记录，但不能解释04:33起持续存在的类型错误。
- 存储治理已将runtime_tasks的inputs/receipt从CDC移出，原任务仍保有原数据。这不改变Case/Job的实际执行来源；一个旧测试仍要求该CDC含inputs，与已上线瘦身契约冲突，在不改本补丁的main也复现，单列为既有失败，没有改业务恢复它。
- 另有较早ValueError/PROVENANCE_UNVERIFIED gaps，不与本次确定的Case JSON subtype缺口混同、不批量删除或清空。

## 实际修复边界

仅修改`src/doxagent/v2_read/outbox.py`：

- Producer触发器对payload/payload_json/data_json/inputs/receipt等既有SQL JSON文本列使用`|| ''`剥离SQLite JSON subtype，保留原字符串内容及NULL。覆盖INSERT/UPDATE/DELETE，既有专门薄回执规则保持。
- Reader在内存中将历史回执里已嵌入的dict/list恢复为文本封装，再走同一个Native解析器。保留原不可变回执和原Source seq，不改源Case或历史模型结果。
- 已有entity/contribution水位继续防止乱序repair覆盖更新状态；不新增队列、缓存或业务状态机。

仅发布Projector单文件overlay并显式重装Runtime Case捕获触发器；保持Runtime、Worker、Prompt/Skill、模型、Schema、路由、交易、冻结上下文和并发不变。存量只通过既有投影repair/水位恢复，不启动W3、不重放交易，不重建Read库或重置全局checkpoint。

## 验证

- 同一干净main，新增6个实际SQL/历史回执用例全部失败；补丁后6项通过。
- 包含真实SQL部分更新、五种JSON列和NULL、历史对象封装、Native大字段展开、W3 PENDING→COMPLETED/NO_TRADE/COMMITTED、原回执/源Case不变，以及旧PENDING回执重放不回退新状态。
- 目标组合62项通过；1项main既有`test_native_large_input_roundtrip_capture_and_backup`因runtime_tasks已瘦身而失败，已在未修改的main单独复现并保留证据，不为通过测试恢复已取消的冗余输入捕获。

## 发布、存量恢复与最终验收

待实际发布后填写。验收必须同时检查源业务状态、Read gap和API字段，而不是只看服务健康或checkpoint。

## 追加：旧详情计时契约不一致

存量回执修复后，86个Case的Read状态均已与源一致，但详情的lifecycle_timing仍settled=false。进一步确认：Read CASE_FIELDS从00ee814c（10/18）起未保留created_at/completed_at/decision_completed_at，44d9f4f7（10/28）增加的详情计时却依赖这些字段，导致完成Case按当前as_of无限增长。该问题属于旧Read字段瘦身与后来API消费者未对齐，不是本次11/11 Worker三文件发布引入。新增用例在原逻辑复现None终止时间。

修复只保留已被API使用的三个小时间字段，并让旧Read行在缺字段时仅对COMPLETED Case使用已有Summary中AVAILABLE的completed_at。无记录时间仍未知、在途Case仍继续计时，不制造结束时间，不改现有decision_completed_at优先口径。该兼容路径使旧历史无需全量回灌冻结上下文。发布仅涉及Projector与API的精确单文件层，Scheduler/Worker不重启。

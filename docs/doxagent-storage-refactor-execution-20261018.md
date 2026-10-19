# DoxAgent 存储架构重构执行回执

实施方案：`docs/doxagent-storage-architecture-refactor-plan-20261018.md`。实施分支：`codex/storage-lifecycle-v4`，起点 `160f352ec3b7b627391959ed005178367e937a0b`。截至 2026-10-19，本轮重构代码、对应 owner 上线及必要恢复验收已完成；存量深度清理按最新指令单独进行。

## 本轮边界

用户后续调整为“先完成全部重构项的收尾；存量深度清理后续单独做”。已停止额外历史分支批量清理；没有继续批量删除旧研究根、现有初始化快照、主 Codex Home 的历史 Session，亦未安排 Source/SDK 数据库 VACUUM。正常运行的各 owner 生命周期仍是本轮重构的一部分。

W3/Worker 全量研究资料及 Context 重复物化保持不变。`.w3-packs`、生产 W3 工作区、主/模板及有效父子线程不计入本轮重构去重收益。没有改写、伪造原有来源不完整的业务记录。

## 重构项及实际机制

| 方案项 | 最终机制和边界 | 上线/证据 |
| --- | --- | --- |
| R0–R1 | Read 使用物理 current 表，短 history 不再与当前对象重复保存；撤除全量 business_revisions 保险副本，GC 按实际 owner/ref 推进 | V4 generation 已上线；原保险副本全量反连接孤本为 0；新旧固定水位 99 个端点业务结果一致 |
| R2、R4 | Read 只存实际查询/增量计算所需的原生字段；Fact/Event/Policy 定义内容寻址保存一份，版本/激活/成员保存引用 | V4 初始主库约 2.56 GiB，初始 7,585 个共享定义；实际 current/history refs 保护正文 |
| R3 | 只保存实际 Policy 正命中及撤回；不再展开每个 Case × 所有未命中 Policy | 原 825,071 条逐 Policy 观察收敛为 30 个命中 Case 集合；未决与明确未命中仍由 Case 摘要区分 |
| R5 | view 15 分钟、tail 30 分钟；前端恢复/过期显式续 view/reset；保留旧 token 的明确过期语义 | 前端 66 项测试及类型检查通过，生产 Web 已上线；后端未悄悄延长 view |
| R6 | alias+generation、选择性可达文件迁移；当前源一致性 snapshot + 临时 CDC bridge + 后续追赶；源持久保存必要的小型业务结算事实 | V4 已切换；旧 Read consumer 已正式退役；全量四源重建最终结果见后文 |
| D1 | backend/site/supervisor/guardian/repair-agent 稳定依赖阶段；app 只带提交确定的源码/合同。approved SDK/Chrome donor 只含真正使用的文件树 | 多次真实源码构建复用相同 dependency key/diffIDs；backend 稳定 14 个依赖层、app 21 层；无逐发布 app 祖先叠加 |
| D2 | 统一 build/deploy/release/reconcile，逐组件 current+previous；实际容器/template/Guardian repair 引用保护；部署成功后自动收尾，单主机 timer 幂等补偿 | 真实生产多次 release 验收；timer 已启用；BuildKit 可回收缓存预算 6 GB、低盘余量目标 20 GB；不直接操作 containerd 文件 |
| E1 | 新 Event Maintenance 分支从所需业务版本构建；历史分支保留当前及确切被引用版本，清退完整 CDC/Delta 过程副本；终态 hook+现有 coordinator 低频补偿 | 原生快照摘要逐版本核验、Case/activation/frame/Reference 保护；历史深度批处理已停止，常规有界 owner 继续工作 |
| T1 | ACK+7 天运输尾部；精确 repair packet，而非一个旧 min(seq) 永久 pin 健康后缀；过期后明确要求 current snapshot rebuild | 四源维护命令已收敛；最终上线/重建验收见后文；没有使用伪造 now 绕过七天限制 |
| N1 | 原生源行 owner 替换/删除同步释放 refs；旧 refs 有界回填后才进入 CAS 回收；必要历史、transport 例外和 hold 保护 | 非 W3 源文件按真实可达性治理；7 天孤本窗口与短暂隔离/重新取得引用恢复；不改 W3 冻结资料 |
| W1 | Worker 内既有后台任务每 10 分钟有界 SDK thread/delete；成功 7 天、失败 14 天，真实恢复 hold 与整个父子/fork/session 树保护 | 实际 SDK 0.159.3 fork/delete canary 通过、父线程 SHA 不变；生产自动 owner 已实际执行；没有启动本轮额外批量 Session 删除 |
| A1 | 已采纳证据保存必要原件并对不可变副本共享内容；成功 7 天/失败或取消 14 天后退出无引用过程输入，未发布终态也有退出；Observation/引用源/最终及未发布输出独立保留 | immutable evidence 与非 W3 Runtime workspace owner 测试通过；不再把所有 runtime-* 或未发布 run 一律永久保护 |
| A1 补充 | 初始化 pre-node `.init` 快照内容相同只保存一份不可变内容；恢复 fork 独立可写；当前/上一份成功初始化、在途 rerun 和恢复责任保护，终态快照有身份墓碑 | 实际 1,056 个既有快照可精确对应 Source attempt；本轮只上线新 producer/owner，存量迁移、去重和清退留待单独执行 |
| S1 | 复用浏览器原 owner：缓存 512→256 MiB、PMA/dump 48h+128 MiB；冷/退役 profile 也参与补偿；快照保留两份普通版本，protected 不覆盖删除 | 正在写入/挑战 owner 锁不打断；错过 create-time prune 可跨轮补偿；不删 cookies/账户/Service Worker 业务状态 |
| B1 | 同 owner 最新验证成功 coherent 迁移恢复集替代上一集；压缩进度可恢复、失败不退役旧有效集；维修 worktree 终态补偿保留 Git branch/receipt | 恢复压缩/中断恢复、alias-aware provisioning、旧 V3 拒绝大备份后自动迁移等回归通过 |
| 观测 | 主机每日容量样本 30 天、只读 storage-status；源 ACK/pin/GC cursor、Read 当前对象/短历史/空闲页各自可查；Bus 现有 operational_alerts 接入低容量/7 天预测告警 | 不增加新监控平台；free<10 GiB 暂缓大依赖构建，普通读取/业务不受此阻塞；七天预测没有足够样本时不编造 |

### 新发现及修正

初始化 `.init` 实际约 16.71 GB（约 15.56 GiB），由初始化每个内部节点复制完整运行目录产生，并非 W3 物化。旧快照全部 1,056 个均通过 `SHA256(workspace_run_id:snapshot_id)` 与 initialization_attempts.execution_id 精确对应，没有用日期/目录名猜 owner。保留 before-node 恢复契约，改为共享不可变文件；mutable source 和 fork 都不与快照共享 inode。已有快照本轮未做额外批量迁移。

四源重建暴露了两个与保留天数无关的放大：按每条初始化历史事件重复聚合进度，以及每个 Turn 跨 ticker 查 Case 时反复访问兼容 history。分别改为隔离导入完成后聚合一次、物理 current 查询和小型 native identity 索引。隔离 bulk importer 的 SQLite 页缓存限定 64 MiB；跨事务导入每 500 次提交；单条失败有 savepoint，不把一条坏记录变成整批回滚。显式索引迁移与隔离导入取锁冲突曾使一轮验证退出；该轮没有记为通过，已修正等待并重新验证。

隔离全量导入不再为每个历史 Turn 重算图，而在所有源表导入后统一结算每个 Case；hot-path/W3 时长同样在所有尝试到齐后计算，避免导入顺序造成漏算。仅增加所需的 `runtime_mode` 小字段。成功/失败图等价、真实 Runtime 快照及 W3 成功/失败 15 秒时长 fixture 均通过。正常在线投影仍按增量工作。

Event Reference 引用扫描原本递归读取大段 before/after 正文，曾触发约 968 万次递归调用。现改为实际 root/version 的 SQLite 标量投影和小型覆盖索引；最终写锁内只复核目标 root 的 Case/在途 frame。交付的 before/after 证据仍保留，仅成功任务已不需要的 retry baseline 退出。

重复 upsert 现按定义引用比较，不再为比较相同 Fact/Event/Policy 反复展开大正文；正常服务端读取保持完整正文与哈希校验。真实旧/新镜像的 100 次约 337 KB 合成对象无变化写入为 3.232→0.719 秒，内容相同且历史行仍为 0。此项沿用现有定义模型，没有新增别名权威或持久物化缓存。

Source 临时快照使用独立 `snapshot:` consumer，24 小时租期；正常返回/异常都退出，catchup 也不创建永久 `read:` consumer。异常被 kill 的临时租约会由源 owner 到期收尾，普通 Read consumer 不按闲置时间自动注销。

快照有效期间，其旧 SQLite 视图可能仍读取已被当前行替换的 native hash，因此同一租约暂缓该源的 native 文件退出，运输过期和引用回填继续。普通 Read consumer、超过 24h 的旧临时租约不冻结文件 GC。两个新代际构建器只接管新建独立目录，异常、Ctrl+C 和 CLI SIGTERM 都退出本次暂存与临时 consumer；不增加全局目录扫描器，不接管已存在/共享目录。

启用前的实际 SQL 计划发现：旧 stale-ref 清除会让 Runtime 的 11,876 条 Case 引用逐条扫描 7,879 个 Case。现改为按既有 owner 索引分页、主键点查；正常行回填与遗留 orphan-owner 游标都完成才允许文件 GC。当前引用和外部恢复 hold 保留，archive/exception 同样有界。远端 EXPLAIN 已从 SCAN 变为主键 SEARCH，避免清理进程自行长期占用源写锁。

收尾发现的两个终态漏项已补齐：工作区原本只枚举 published run，远端存在 56 个超过 14 天、失败/取消且无 published bundle 的 run 候选；现在同一 owner 也覆盖其无引用输入副本，保留 draft/output/audit、引用原件、SQLite/Observation 和恢复 hold。Runtime 过程 owner 同时覆盖失败任务 14 天及已成功任务的旧失败/取消尝试，保留最终/错误/usage 回执；写锁内复核状态和更新时间。退役请求误复用在改变 generation/回执前明确拒绝，不破坏原记录。未启动人工批处理。

最终四源恢复验证又发现并修正三项恢复契约：薄存储的 StandardMessage 已无内联 `body`，消息及 Case 路由更新改从既有 BodyRef 取得搜索文本，跨 UTF-8 分块不会截断；只读当前源不能重现过去的有效成员关系及已覆盖过程的尝试/用量，因此在既有 Runtime 小型业务事实台账保留 catalog、ModelAttempt、usage 和稀疏候选计数，不复制 Context 或通用历史表；当前 activation 导入前先放入既有 catalog，避免为已经生效的 Policy 再制造一次 ADD。完整 Source 导入无需因此重跑数小时的 Library 编译，隔离候选库通过产品恢复函数补全，再追平真实增量；补全前后的证据分开留存。

另有 2026-09-10 的 MU 旧快照只在历史恢复集找到原库，并非当前 activation/Case/reference 的在线源。其 Library、Event、Detail、Fact 共 16 条、12,772 字节，作为一次性遗留恢复事实保留到同一台账，未复制整套旧库。原根仍按存量清理边界保留。

## 生产状态与验收

Read alias `/data/read/active.json`；兼容路径 `/data/read/v2.sqlite3` 指向 `serving-v4-20261018/v2.sqlite3`；生产 generation 为 `75a54e2449a84065a0aee86dac09e353`。旧 V3 不冒充可直接回滚到 V4 schema 的镜像。

最终产品代码为 `d21505b58e72d3a1552ea3166c2c304a075549ed`。API、Projector、四源 Transport Maintenance 已部署该版本；统一发布器接纳结果 `errors=[]`。Transport 上一可用版为 `f9e83a0e6875f7cd42a1e2ea711ef9ee86ca3522`，两版均具备有界 orphan-ref 处理、快照 native 租约保护和相容运输格式，旧 `618fd9c5` 不再承担其回滚责任。实际命令明确包含 Research、Initialization、Bus、Runtime 四源。常规运行回执已确认四源 `error=null`、引用回填游标推进；初次 native 引用回填仍在后台进行，`ready=false` 期间不提前删文件，不将初次回填完成冒称已经验收。没有额外启动历史运输批量清理。

其他 owner 的生产版本：Worker/Scheduler `f391ccf5`，Initialization `bc23faad`，Guardian `ff75083f`，Web `f7ba4f87`；其余既有存储 owner 为 `99d2da1d`。实际 API、Web、Worker 和 Site/Supervisor 健康检查均 healthy；其他无健康端点的角色为 running，Transport 另由真实维护回执验收。发布资源 timer 为 enabled/active。并行工作后来把 Site 推进到 `68c9414c`，Supervisor 仍为 `b1f8237c`；没有用本轮代码覆盖该变化，实际容器镜像引用仍受发布回收器保护。

四源完整扫描 304,903 行，进程退出 0。隔离候选经恢复修正补全并追平明确验收水位：Research 12,016、Initialization 138,868、Bus 2,948,257、Runtime 1,979,072，`quick_check=ok`。最终对照生产库的 13 类业务对象（Case、Fact、Event/Policy Detail、文档/研究章节、Policy admission/hits、Event/Fact/Policy change、usage、execution）**缺失键均为 0**；除 Case 和 Event 的已解释字段外，其余 11 类原始 payload 也完全一致。重建保留 24 条来源缺口，不把 `verified=false` 改写成 true。它与原始完整扫描、补全回执分别留存。

差异核实：Event Detail 去除 Library 编译摘要字段后全量内容差异为 0，旧物化摘要与当前原生源的编译摘要不冒称一致；Case 除全局 revision 和新增字段外，161 条旧 `W3=PENDING` 在源中实际已为 FAILED，另 3 条按真实 trade_intents 的重复交易状态重建。Source 状态和三条 intent 均已逐项核实；没有为追求旧缓存逐字节相同而还原错误状态。最终候选库的 99 个端点再次全部 HTTP 200。

本轮两套验证代际及两个已退出验证容器已清退；先核验 alias、consumer、打开文件和 cwd，再只删除本轮确切目录。删除前独立占用合计约 5.82 GiB，磁盘可用量实测从 37,849,853,952 到 44,102,758,400 字节（约 41.07 GiB）；净差还包含同期生产写入，不作为纯回收归因。该动作只是验证暂存收尾，没有继续清退存量业务目录。证据包位于 `D:/DoxAgentStorageExecution/final-source-proof-evidence.tar.gz`，15 个文件逐 SHA/JSON 校验，包 SHA256 `65655cfc87fb330e9de6666aa9280a554db8c5a18a6c8dd3c00bad7ed0a5fb3e`。

最终实际 Read 整目录占用 3,344,166,912 字节（约 **3.11 GiB**）；其中生产代际目录 3,343,626,240 字节，主数据库文件 2,828,976,128 字节（约 **2.63 GiB**）。短历史当前为 0 行，WAL 为 0；这只是该次采样，短历史允许在正常十五分钟窗口内变化。没有把数据库文件大小与旧整目录占用混作同口径比较。

### 正确性与性能

- 迁移前后固定水位 99/99 代表端点业务结果一致；上线后另一轮 99/99 HTTP 200，涵盖 MU/INTC/BE、7/30/ALL、Policy ACTIVE/HIT/MODIFIED、Event、Runtime、Bus 及新旧 Case。
- 后端 Read 全套 **170 passed**；owner 相关套件 **10 passed**；含发布器/Session/快照/备份/运输的组合回归 **31 passed**；这些套件有重叠，不相加成虚假的独立测试总数。容量告警/发布器回归 9 passed；前端 66 passed + 类型检查。
- 最终快照/指标修正后 Read 全套 **174 passed**；与已提交的 Site/Yahoo 修复集成后组合 **215 passed**；最后终态退出、maintenance recovery、Session/初始化快照、发布器及快照结算组合 **45 passed**。这些包含前述测试，不另行叠加为总数。
- 加入快照/native 并发和构建暂存退出后，Read 全套 **180 passed**；随后实际 CLI SIGTERM 单独 **1 passed**，引用游标/运输/备份组合 **19 passed**。没有把这些重叠套件加总或称为一次更大的全套运行。
- 最终消息/历史目录/用量恢复修正后 Read 全套 **184 passed**；随后成员关系导入顺序、尝试与候选计数恢复组合 **23 passed**。单独组合不冒称全套重新运行。真实重建候选与生产库分别 **99/99 HTTP 200**；另存业务差异报告，不把不同全局 seq、view token 或新增字段的响应哈希差异误称数据丢失，也不声称这轮 99 个响应逐字节相同。
- 旧/新固定样本 Policy p95 887.02→422.8 ms。最终常态负载下 108 次 Policy 请求 p50 161.84、p95 463.10、max 555.18 ms。重建与其他运维并行期间同类 p95 737.9 ms，不能宣称所有压力场景都满足 500 ms。
- 并行验证中 Event p95 366.1 ms、Runtime 128.8 ms、Bus 127.8 ms、Case 270.1 ms。以上是服务端/ASGI 实测，不代表完整浏览器导航 FCP；浏览器自动化初始化报系统路径不存在，**浏览器首批可用内容 p95≤2s 尚无完整实测，不据此宣称通过**。
- 最初生产有 37 个来源不完整记录（33 个原有 + 4 个 CEG W3 template invocation 缺 case/source_case/control_epoch）；最后运行状态为 41 个，仍保留真实 `PROVENANCE_UNVERIFIED`，没有伪造 provenance，也不让这些局部缺口冻结全库 GC。运输端精确保留对应 41 个例外，而非永久保护其后的整个健康后缀。
- 既有全套大范围回归中有一项 `test_phase11_real_tools` descriptor 失败；基线 160f352e 同样复现，定位为原 crawler_plane.list 的空 input_fields。本轮没有越界修改该工具以制造全绿。

### 恢复材料

- D 盘 V4 完整选择性读取基线：`D:/DoxAgentStorageExecution/read-serving-v4/serving-v4.tar.gz.enc`，SHA256 `a34e44c40b400fe0be34654c078a4e6520f8bb41e75bcdf3de69468765ca40cc`；69,358 个文件逐 SHA 校验、SQLite quick_check、解密还原通过。它是业务读取基线，不冒称完整旧技术 CDC。
- 必要的最新 Runtime 小型业务结算事实独立一致性导出至 `D:/DoxAgentStorageExecution/canonical-business-facts/business-facts.jsonl.gz.enc`；53,860 行，压缩加密 6,097,952 字节；逐 JSON/内容读取验证；SHA256 `37eb4312c5eed58bbab8cf89611abb4883c55275fa0197841f53e5083bfd95fb`。私有 ACL 密钥在同 owner 目录。
- 最终补全后的 Source 小型业务事实另有一致性导出 `D:/DoxAgentStorageExecution/canonical-business-facts/business-facts-final.jsonl.gz.enc`：171,443 条、15,727,200 字节；解密、gzip、逐 JSON、逐类型计数和流摘要全部核验。SHA256 `96d9960ed24abe4255bf4235eb653296315d1e324e82af0ab9c82624dce19c43`。这是必要恢复事实，不是给可再生缓存额外制造备份。
- 既有 coherent Source/业务文件恢复集在 `D:/DoxAgentPilot/backups/v21-production-20261012/`，本轮没有为了普通缓存再复制一整套；旧测试的唯一资料归档已生成，但整根退出按最新指令留待下一轮。

## 存量清理的当前边界

用户调整前已完成的动作保留独立原始回执，包括旧 Read 代际退出、已核实的旧隔离 Context 输入退出、采用证据副本物理去重及发布系统退役旧镜像。已完成动作不反向撤销，也不把下一轮潜在收益记入本轮。

暂停后不再执行额外的 Event 分支批量循环、主 Session 批量 delete、现有 `.init` 迁移/去重/退出、四个旧测试根删除、旧维修目录逐项清退、Source/SDK 库物理压缩。四套旧测试唯一资料 v2 包已校验，但原根仍在线；不能把“备份完成”写成“根已清退”。

一次性迁移/去重回收与新增占用抑制分别核算。SQLite freelist 只是同库可复用空间；BuildKit 的 shared/logical cache SIZE 不等于独立物理回收；Read 原文件 hardlink 不与新代际重复计收益。最终磁盘余量以 `df` 实测为准，不把所有操作中的净变化简单求和。

## 后续观察与交接

- **七天自然业务和发布增长观察尚未经过，不宣称长期稳定验收已完成。** 已部署日样本/owner 回执与统一查询入口；后续只需核对自然流量、每 1,000 Case/Library 发布/初始化的增长和退出，不为观察另建自动任务。
- W3 Context 暂缓项仍可能持续增长；它的代价与 `.init` 不混同，也不能承诺本轮以后永远不满盘。重构后的短历史、缓存、版本退出将持续约束各自的增长责任。
- 产品改动在独立分支，已合入主分支截至 `d8a7fee9` 的已提交 Site/Yahoo 修复，保留本轮存储 owner；主工作区存在大量并行 W3/其他未提交工作，没有 reset/stash/覆盖。主分支后续集成必须保留本轮存储提交，未来发布前先集成本轮分支，再使用已落地的统一 release 入口；旧 main 的 Read V3 不能作为 V4 API 的可用代码回滚。
- 主机查询：`sudo /usr/local/sbin/doxagent-release storage-status`；日样本 `/var/lib/doxagent/releases/capacity/`；发布状态与收尾回执 `/var/lib/doxagent/releases/state.json`、`last-reconcile.json`。
- 原始执行证据：主工作区 `eval/storage_lifecycle_execution_20261018/`；大日志/校验材料 `D:/DoxAgentStorageExecution/`。报告不保存凭据或环境秘密。

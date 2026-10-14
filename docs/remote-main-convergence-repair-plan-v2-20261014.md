# 远端修复合入 MAIN 与临时发布清理方案 V2

日期：2026-10-14，Asia/Shanghai。状态：**修订方案，未执行合并、发布、备份或删除。**

本版以 [修复遗漏调查](remote-release-reconciliation-20261014.md) 和 [合并前容量审计](remote-premerge-storage-audit-20261014.md) 为基础，替代原报告中的实施顺序与清理保护规则。原目录清单、修复来源和复现结果继续作为依据。

## 1. 本版的取舍

目标仍是：有价值的线上修复进入 MAIN；正式服务使用该 MAIN 提交构建的应用；运行与重建入口不再依赖临时发布目录；已无价值的临时资源退出。

**强化保护的对象，是唯一源码、业务事实与执行记录、不能直接重新取得的研究/正文/工件、有效身份和私钥。缓存、可重新构建的二进制、重复副本不为清理再做一套备份。** 不以“没有容器引用”“目录叫 test”“处于 Exited”代替价值判断，也不把一个未知小对象升级为整个项目的阻塞。

本次不做数据库压缩、历史业务裁剪、存储架构迁移、全盘备份、新备份平台、身份重建或自动重试系统。MAIN 管代码与部署模板；业务卷、真实 secrets 和身份数据继续留在 Git 外。专用 Web/Chrome/Guardian 镜像可以不同，但其应用来源与构建依赖应可追溯。

## 2. 四项风险审查结论

| 风险领域 | 上版已有保护 | 是否足够 | 本版必要补充 |
| --- | --- | --- | --- |
| Git 脏树、未提交补丁 | 不直接 reset/pull；逐补丁判断价值 | 不足：没有完整覆盖 staged、untracked、ignored 唯一文件和仅在容器内的修改 | 先保存一次可恢复的源码差异包，核实唯一内容已落入 MAIN 或恢复包，再清工作树 |
| 生产库、SQLite WAL | 数据库不作为垃圾删除；保留正式恢复集 | 不足：未规定 WAL 一致性、数据库与外置工件配套、跨库恢复和发布后写入处理 | 用 SQLite Backup API；按实际写入/迁移范围保护；原卷原路径复用；代码回退不自动回滚业务库 |
| Volume、容器可写层 | 保留业务 named volume；解除挂载后再退役容器 | 不足：未覆盖匿名卷、共用生产卷的测试容器、可写层唯一文件 | 固定真实 volume 名称；逐个待退出容器检查可写层；只提取唯一有用文件；退出容器不附带删卷 |
| Secrets、认证、Profile | 保留 env/secrets/Profile | 不足：未覆盖容器内 NSS/密钥库、无引用旧 Profile 卷及跨环境可解密条件 | 小体积敏感配置单独受限保留；Profile 与 Registry/必要密钥配套；复用原身份，需快照时走已有停用单身份流程 |

### 本轮补充取证

2026-10-14 **22:34:40 起**只读采集了 51 个容器的 mounts 与 `docker diff`、10 个卷的引用/顶层目录、已知业务 SQLite/WAL 文件大小和少量敏感路径权限。51 次 diff 均成功，记录未截断；未读取凭据正文、Cookie 内容或业务表全量数据。

确认了以下具体风险：

- 正式服务与 MU/INTC/BE/RKLB 隔离研究容器共用 `doxagent-v2_v2-data`。其内部同时有正式业务目录、四个 `*-v21-test`、`v21-production-validation` 和 workspaces；**不能按整个卷或 test 前缀清理**。同卷不代表同一数据库，是否需要停止某个 Worker，要看其实际数据库/文件路径。
- Site Access 可写层存在 `/home/siteaccess/.local/share/pki/nssdb/{cert9.db,key4.db,pkcs11.txt}`。尚未核定它是空默认库还是含自定义证书/密钥；不宣称它就是 Cookie 解密密钥，也不能直接丢弃。
- 研究 Worker 可写层存在 `/tmp/unit*.json`、`/tmp/completion.json` 等产物候选；也存在明确可重建的 yfinance 缓存。文件名不能证明前者已持久化，需要在退出前对照对应 attempt/output。
- 三个无容器引用的卷 `doxagent-barrons-cdp-attach-control-profile`、`doxagent-barrons-chrome-control-profile`、`doxagent-barrons-playwright-chrome-control-profile` 含 `Default`、`Local State` 等真实 Chrome Profile 结构，不能按 unused volume 直接处理。
- 匿名卷 `aa65271055f4655e6024bff4c456cf3ce135b39a25d3313c16801ce4463f16c4` 正被 egress-clash 挂到 `/root/.config/mihomo`。另两个无引用匿名卷也有 `config.yaml`；不能只看匿名名称把配置一起删掉。
- Runtime、Bus、Site Registry 的 WAL 当时分别约 2.50、4.23、3.97 MiB，证明正在使用 WAL。某一时点 WAL 为零，不等于以后可脱离主库处理。
- `.env.v2`、Codex auth、provider sources 文件当时为 0600；secret、Profile/credentials 目录为 0700。当前权限无需顺带整体调整。

这是路径与元数据审计，**不是已完成恢复验证**。可写层中的候选文件是否唯一、旧 Profile 是否仍有有效身份，需要在实际退役对应对象前完成小范围核实。

## 3. 数据按价值处理

| 对象 | 处理决定 | 所需保护 |
| --- | --- | --- |
| 未入 Git 的有效生产补丁、未推送唯一提交 | 保留并整合 | 一次完整差异包；必要的未推送提交导出；一份核验过的异机副本 |
| Runtime/交易账、研究及激活状态、Bus 原始新闻与正文、不可重新获得的工件 | 保留原件，不在清理中迁移或删除 | 对本次受影响的权威状态准备一套可恢复副本；业务文件按实际引用配套 |
| Read 库 | 本次原位保留 | 存在历史 provenance/读取约束，暂不按普通缓存重建；无破坏性变更时不再为它复制约 25 GiB |
| 活跃 Codex sessions/threads、attempt 冻结输入/实际输出 | 保留有恢复/业务依据的原件 | 不按工作区年龄裁剪；容器临时产物只有确认已持久化或无价值才丢弃 |
| 有效 secrets、认证、含唯一密钥的 NSS/系统密钥库、有效 Profile | 保留当前可用组合 | 小配置受限复制；受影响 Profile 必要时作一致快照；不重置身份 |
| 已提交源码副本、旧 dist、普通依赖/下载/编译缓存、pycache、崩溃报告、运行时 socket | 可直接清理 | 只查当前用途、必要依赖及活动状态，不为它们备份 |
| 无引用旧 Profile、旧 auth/config、未知小文件 | 只核实该对象 | 重复、空默认或可重新生成则直接删；唯一有效内容先提取；未查清的小对象暂留，不拖住其他清理 |

“有备份”必须有具体内容范围与可恢复证据。同机同盘副本只能防误操作，不能防整机磁盘损坏；本次需要的唯一恢复材料保留一份在异机受限位置。不要用“Git 已 push”代替 secrets、未跟踪文件或业务数据保护。

## 4. Git 与未提交生产补丁

### 4.1 一次性保全，覆盖到实际文件

在修改/退出相关工作树前，记录 HEAD、分支/工作树关联、staged/unstaged 状态，以及 untracked 和有价值 ignored 文件的精确路径。保存：

1. 相对 HEAD 的 staged binary patch、相对 index 的 unstaged binary patch；两份共同恢复当时最终文件与暂存状态。
2. 未跟踪的唯一源码、部署模板和必要二进制文件原件；包括未纳入旧取证筛选规则的文件。删除项、文件模式及有意义的链接目标记录在清单中。
3. 仅存在远端、无法从已有远端 refs 取得的提交，以限定 refs 的 Git bundle 或等价可验证提交副本保留；已在 origin 的完整历史不重复打包。
4. 从待重建/删除容器中发现、确实未在宿主源码或镜像基线中的生产代码修改。`docker diff` 只证明路径改变，内容应与当前镜像/对应源码核对后再提取。

源码差异包保留 base commit、文件列表、SHA256；在隔离检出验证 patch 可恢复，唯一文件校验匹配。一份异机副本即可，不压缩复制整个大工作树，不重复打包 `node_modules`/venv/旧 dist。

真实 `.env`、tokens、auth、订阅地址和私钥不混入拟提交 patch；若原 tracked 文件意外含凭据，恢复原件走受限包，提交只保留脱敏模板。原始记录不打印正文。

### 4.2 整合及工作树退出

- 在独立整合工作树处理 MAIN、发布分支与维护分支；保留主检出已有 W3/回测工作，不为了腾空间 stash/clean/reset 它。
- 每项补丁标记为“合入 MAIN”“已被新实现吸收”“无须保留且已核实”“仅恢复包保存”。文件不同不直接判成遗漏。
- 修复的生产最小变更与本地正在开发的回测功能分开；整合后做对应回归，追加 changelog。
- 远端 `/home/ubuntu/doxagent` 保留为最终 MAIN 根。移动到目标提交前，先移出已保护的代码差异，并保留 env/secrets；不能对整根执行覆盖式解压或不加区分的 `git clean -fdx`。
- 旧 registered worktree 用 Git worktree 流程退出。若只因已确认的历史依赖删除和已保全差异而仍显示 dirty，可以对该精确 worktree 有限强制退出；不为这些噪声无限等待“天然变干净”。不得先删主库 `.git`，也不影响仍保留工作树的共享 Git 对象。
- 差异包在 MAIN 修复及生产切换验收后收尾；已在 MAIN/origin 可恢复的纯代码不长期保留多份副本。仍未整合的唯一内容继续单独保留。

## 5. 生产数据库与 SQLite WAL

### 5.1 本次默认原位复用，不搬库

保持 Compose 项目 `doxagent-v2`、实际业务卷名称、容器内数据库路径和外置工件根不变。MAIN 收敛是代码与发布入口调整，不能顺带把 `/data` 换成新的空卷、移动 SQLite 或重建 Read。

核查候选代码的启动入口、repository 构造和 migrator 实际写入；不能仅凭“schema marker 当前”认定无迁移。现有 `production_v2.migrate` 的快速路径仍会处理 trigger/index；现有自动备份列表也不等于覆盖 Site Registry、Codex 状态及所有其他业务文件。

纯源码合并和删除不承载状态的旧构建目录，不要求停库或新增大备份。本次第一次切换涉及维护/消费等业务行为，应先具备**一套近期、覆盖受影响权威状态的恢复集**；既有恢复集若版本、范围与本次切换所需状态可核验匹配就复用，否则补足缺项。旧库页数相同或文件名带 backup，不足以证明已覆盖当前状态。

保护范围按实际用途选定：通常含 Runtime、research、initialization/control、Bus，以及所需 Event Library、published/native/content/receipt 文件；Site Registry 仅在本次会重建/迁移其使用者或改变其状态时补入。其他库若被改动同样纳入；不为几个已知库之外的所有目录做全树复制。Codex sessions、Profiles 原位保留；只有涉及其搬迁/删除或对应状态格式变化，才补其专门恢复材料。

### 5.2 一致性、容量与验证

- SQLite 使用现有 Backup API 路径产生逻辑一致快照；不直接 `cp` 正在运行的 `.sqlite3`，也不把分时复制的 db/wal/shm 当一致备份。WAL 属于数据库持久状态，不能手删或截断；SHM 可重建，不表示可以在活库上随意移除它。依据：[SQLite WAL](https://www.sqlite.org/wal.html)、[Backup API](https://www.sqlite.org/backup.html)。
- 需要一组跨库可一致恢复的状态时，短暂停止相关任务准入、排空并停住**实际写这些库的进程**，冻结引用后再顺序快照，完成后恢复服务。SQLite 单库快照不自动提供跨库事务一致性。包含 GUI/脚本/隔离 Worker 等非标准写入者时一并核实；使用不同测试库的 Worker 不必为此停掉。
- 原件与备份中的不可变工件按引用清单配套，保留目录映射、文件 hash 和已完成发布边界；不只备份数据库、遗落其指向的唯一正文或研究原件。已有异机不可变文件可按清单复用，不重新传相同内容。
- 执行前按逻辑库大小、所需配套文件、复制/压缩峰值及在途 WAL 增长估算余量。22:34 核实的 Runtime/research/control/Bus 主库合计约 **12.26 GiB**，另 Site Registry 约 **0.55 GiB**；尚未计配套文件及施工峰值，不能拿磁盘约 16 GiB 可用就直接开做。
- 先释放已确认可重建的缓存/旧镜像；必要时顺序生成、校验、压缩并移出单库副本。余量不够就只延后需要该备份的发布/迁移，不改为裸复制、不删原件、不顺带 VACUUM 腾空间。
- 对新副本做可打开、schema/关键高水位、指定代表性业务对象与引用文件核验；压缩/传输校验 hash，在异机或隔离目录实际解压打开一次。大型全库 integrity_check/全表 COUNT 不作为本次普遍门槛；恢复副本的专项检查也不在活生产库上运行。
- 本次保留一套完成核验的恢复集；复用已有生命周期代码，不再新建多套全量备份。同一任务的新集未完整完成前不删旧可用集；其他正式恢复集不被本次临时目录清理连带删除。

### 5.3 切换失败时保留最新业务事实

默认回退应用镜像与配置，继续挂原业务卷；**不自动用发布前快照覆盖上线后已写入的 Runtime/交易账、激活状态或新闻。** 尤其不能因恢复旧账重放真实订单。

若发现不兼容/破坏性 schema 迁移或数据库已损坏，先暂停受影响写入，保留故障时的当前库及其 WAL、已对外执行的交易/持久回执，再确定针对性恢复与账实对照。只有此分支需要数据库恢复；健康的源码回退不启用它。不允许新旧版本同时作为同一数据库的重复 singleton writer 运行。

## 6. Docker Volume 与容器可写层

### 6.1 固定卷身份，退出容器不删数据

实际运行配置至少应核对以下存储：

| 实际卷/挂载 | 内容与决定 |
| --- | --- |
| `doxagent-v2_v2-data -> /data` | 正式业务、Codex、工作区及隔离研究混存；保留同一卷，禁止整体删除 |
| `doxagent-v2_v2-site-strategy -> /site-data` | Registry、Profiles、credentials；保留同一卷 |
| `doxagent-v2_v2-enrichment-identities -> /identities` | 独立正文认证身份，不被 Site Profile 保护范围自动覆盖；保留 |
| `doxagent-v2_v2-chrome-control -> /run/doxagent-chrome` | supervisor socket/VNC 控制状态，可重建；本次优先原位复用，不为其备份 |
| egress-clash 的匿名配置卷 | 正在使用，保留原卷 ID；不在本次 MAIN 收敛重建 egress |
| Guardian `/repair-source` 与正式 secret bind mounts | 源码挂载可切换，secret 路径原位保留；新路径验证生效后才退出旧发布根 |

正式模板将需持久复用的卷绑定到已存在的明确名称，避免项目目录变化引出新命名卷。继承的默认 project/volume/env 值以实际解析配置核对；不为此把整个 deployment 含 secrets 的 JSON 提交 Git。

本次不执行 `compose down -v`、`docker rm -v`、`volume prune`、`system prune --volumes` 或 `compose up --renew-anon-volumes`。无引用不证明卷无价值；匿名卷也持久保存数据。依据：[Docker Volumes](https://docs.docker.com/engine/storage/volumes/)、[Compose up](https://docs.docker.com/reference/cli/docker/compose/up/)。

允许按**精确容器 ID**退出已完成的旧测试容器，不附带删卷。退出后仍保留 shared volume；业务库中引用的 adoption/研究原件不随测试根清理。四个活跃研究 Worker 只在任务已终态、无持有中的写入/必需恢复责任后退出，不把它们全部停止作为 MAIN 代码合并条件。

### 6.2 可写层只提取唯一价值内容

对本次将重建/删除的容器，复查 `docker diff` 并对照 mounts。目录项改变不代表目录内每个文件都变了，挂载入口出现在 diff 中也不代表卷内数据已被列出。失败/截断只阻止该容器退出，不阻止已核实其他对象清理。

分类处理：

1. pycache、依赖运行缓存、yfinance 缓存、socket/PID、Crash Reports、确认可再生成的 token 派生副本：直接丢弃，不导出整个容器。
2. `/tmp` 的 completion/unit/output 等：与对应 run/attempt 的持久化产物核对。已持久化或确定无价值则丢弃；唯一业务产物以原 run/attempt 身份保存，不把可疑残片擅自发布成正式研究结果。
3. 未落源码的实际修复、唯一人工配置/证书/私钥：提取对应小路径，分别进入源码差异包或受限身份包。
4. Site Access NSS：核实是否空默认库、有无自定义证书/密钥。空默认可重建则不备份；含唯一内容或不能及时证明为空时，在停稳该使用者后保留这一小目录及必要解锁条件，再退出容器。不得据 `key4.db` 名称推断它能替代浏览器 Profile 或系统密钥环。

容器镜像、`docker commit` 和 `docker export` 不当作业务卷备份。Docker 可写层随容器删除消失，卷另有生命周期，依据：[Docker Storage](https://docs.docker.com/engine/storage/)。

## 7. Secrets、认证状态与浏览器 Profile

### 7.1 小配置保留一套受限副本

覆盖实际仍用且在本次路径收敛中可能丢失的配置：`.env.v2`、`deploy/secrets`、Codex auth、Site credentials 原件、provider sources，以及仅在旧发布/可写层内的唯一密钥；另有加密存储时再配套其实际密钥。当前 `CredentialStore` 是受目录/文件权限保护的 JSON 账号密码存储，不能假设原件已加密，也不借此次任务重构凭据系统。保持原 UID/GID、模式和密钥到数据的对应关系；不通过全树 chown 或换新 UID 解决迁移权限。

异机副本放到明确受限目录，敏感包加密并将解密密钥保存在包外的受限位置；校验可解密与必要文件可读，正文不进报告、终端输出、Git、构建上下文或镜像层。创建敏感中间文件前先限定目录 0700/进程 umask 0077，不等打包结束才收紧权限。普通代码包不含敏感包。不顺带轮换所有 token、复制全部用户 `.ssh` 或把整个 `.config` 备份。

当前有效 auth 正常使用原件。旧 `doxagent-stage/codex-auth.json` 或 `deploy/secrets` 先在受限环境确认是否重复/过期；已有对应可用原件的旧副本可直接清理，不因为文件名是 auth 就永久保留。

### 7.2 Profile 原位复用；涉及浏览器重建才针对性快照

本次优先保持 Chrome 版本、同一 Profile 路径、运行用户、Identity/Registry 映射、所依赖密钥与现有 egress 绑定。只改 Site/API/Bus 代码时，不为了备份而重启全部 Chrome，不自动换国家或复制 Cookie 到新身份。

如果 MAIN 发布需要重建 Chrome 所有者、升级浏览器、搬 Profile 或退出可能含唯一有效身份的旧 Profile 卷：

1. 使用现有 `SiteAccessService.snapshot_profile` 维护入口，排空/停止对应 Identity 后作快照。该入口已调用 stop_identity/close_profile_if_idle，再由 `ProfileSnapshotManager` 打包 Profile 和一致 Registry 副本；复用现有机制，不另造并发复制器。
2. 快照保留必要 `Local State`、Cookies/Local Storage/IndexedDB/Preferences 与身份映射；若加密依赖额外 keyring/OS 用户条件，配套保存或保持原环境。不得承诺“复制 Profile 一定可登录”。
3. 可排除确认可重建的 Cache/Code Cache/GPUCache/Crash Reports，以及已停稳浏览器的运行时 Singleton socket/锁，不打包整个浏览器安装层。原始 Profile 完整保留到目标验证通过。
4. 当前 Profile 已在使用则不启动第二个浏览器读取同一个可写目录。恢复验证使用受限隔离副本和匹配环境；当前会话不活拷。停用/快照失败仅延后该身份迁移/删除，不冻结其他部署工作。
5. 用同一 Identity 的既有只读访问验收认证状态与必要正文权限；不以旧 `auth=VALID`、进程起来或端口可连代替验证，不触发重新登录/挑战绕过/账户或交易变更。

三个无引用的 Barron's Profile 卷逐个判定：空测试/已失效且无唯一内容的直接退出；唯一有效会话或人工设置先保留一次必要快照；未查清的卷跳过本轮。没有必要把所有历史 Profile 都迁入新生产目录。

## 8. 保留的功能修复范围

整合内容仍按原调查，不扩大设计：

| 范围 | 具体内容与兼容边界 |
| --- | --- |
| 已上线发布分支 | 纳入 `8a8f60f2` 所在发布分支的有效提交，保留 MAIN 独有双语 README；不直接从 9/28 脏 main 重建 |
| 维护恢复 | 局部 Event 错误降级/有效产物重放、有限阶段预算与确认超时恢复、成功 O3 工件恢复、发布错误归因、本轮 snapshot 的 resolved 限定、FAILED/HELD dirty 保留、Case 固定语义日读取 |
| 维护兼容 | 按当前 v2.1 O3 工件合同与 W3 task_phase/Case 身份移植，不整份覆盖旧 maintenance 文件；保留当前 SQLite 异步访问、Policy 消费与 WATCH 等新规则 |
| Barron's | 所有页面形态都要求精确发布时间；当前已有的 site_budget_deferred 有限重试继续保留 |
| Worker | writer fork 交接修复、W3 Data MCP 90 秒启动预算；只提取已在线验证的最小改动，不夹带回测开发 |
| 部署配置 | 恢复 Yahoo DE/TR route 配置，实施时验证真实 listener 映射；持久卷/secret 的实际来源固定到正式入口 |
| 构建来源 | 固定并保留当前 approved IBKR/Chrome 依赖和对应 protobuf 条件；验证新 MAIN 构建可用之后再退役旧依赖镜像 |

必要回归覆盖上述 badcase、幂等/阶段恢复、v2.1/O3 合同、同语义日、dirty parent、SWEEP、Barron's 时间、writer/MCP 及配置解析。已有“5 failed → 5 passed”和 Barron's 样本复现只证明局部缺口，不替代整合测试。

## 9. 修订后的实施顺序

| 顺序 | 操作 | 必须满足的最小条件 |
| --- | --- | --- |
| 1 | 锁定本次精确目录/容器/镜像范围，刷新 refs、挂载与运行状态 | 不依赖旧计数执行删除；不读出 secrets 正文 |
| 2 | 保存唯一源码差异和必要小敏感配置，独立整合并验证 | 唯一补丁有可恢复副本；本地其他工作未改动 |
| 3 | 取得发布余量：清理可回收构建缓存、已无依赖旧镜像 | 不备份这些低价值资源；保留当前回滚镜像、动态 agent、approved 构建来源；无构建在用 |
| 4 | 核实启动/迁移影响，准备本次需要的一套恢复材料 | 所需权威库与工件范围覆盖、备份一致且异机可打开；不复制未受影响大 Read 库 |
| 5 | 提交/合入 MAIN，生成正式 Compose/构建产物 | 目标应用 revision 明确，所有关键实际卷名称与 secret/bind 来源保持正确 |
| 6 | 按服务切换，迁移 Guardian 源码挂载和正式发布入口 | 同一业务路径无新旧重复 writer；必要身份已做针对性保护；挂载未变成空卷 |
| 7 | 验收业务与存储 | 关键激活/Case/原 Effect/执行回执连续、正文/研究引用可读、Identity 可用；无人为重放交易 |
| 8 | 精确退出旧容器/工作树/覆盖目录，收尾重复恢复材料 | 对应唯一内容已保存，当前依赖已解除；跳过未查清的少数高价值对象，其他候选继续清理 |

缓存、无依赖旧镜像可以在 MAIN 合并前清理；含唯一补丁的工作树必须先保存修复。当前 release 根及 writer-fix 目录最后清理，直到 Compose、Guardian 和运维入口都迁出。无需等待所有临时目录都能删除才完成 MAIN 合并和生产切换。

容量以每步 `df` 净变化复核。既有审计中的可回收缓存约 9.14 GiB、旧镜像专属层估算约 8.99 GiB 不相加承诺回收量；优先取得本次实际峰值所需余量，达到后不扩大为一次全局清理。

## 10. 验收与停止条件

只设以下与实际损失直接相关的停止条件，且尽量限定到对象：

| 发现 | 处理 |
| --- | --- |
| 待删工作树/容器有唯一修复或业务产物尚未保全 | 暂不删该对象，先提取；其他低价值对象继续 |
| 新 Compose 改指空卷、错误路径、错误 UID/密钥 | 不切换该服务，修正实际装配 |
| 要执行破坏性迁移/搬库但缺一致恢复集或余量 | 不执行该数据库操作；不删源库换空间 |
| 浏览器仍忙、Profile 快照失败或唯一密钥未保全 | 暂缓该 Profile/浏览器所有者重建，其他服务可继续 |
| 活跃任务未终态、旧 singleton writer 尚未退出 | 不重叠启动其替代者，不退役该 Worker；不阻止代码合并 |

最终留一份简洁收据即可：目标 MAIN SHA、保留修复与回归结果、容器 image/revision、实际 mounts、保护材料位置/核验结果（不含秘密）、精确删除路径/ID、剩余暂留对象及原因、df 净变化。无需新审批平台、全表审计、所有文件逐次哈希、无限等待窗口或长期保留所有旧版本。

成功标准：MAIN 包含有效修复；正式应用/Guardian 来自同一 MAIN revision；生产根无未提交应用补丁；临时路径引用解除；业务原件与有效身份保持可用；可重建的旧资源已按范围清理。个别尚需保留的身份卷或活跃研究 Worker 如实列为例外，不伪装成“远端只剩 MAIN”或扩大删除范围来达成表面整洁。

## 11. 本版证据与边界

- 原始来源与目录用途：[远端发布调查](remote-release-reconciliation-20261014.md)。
- 容量、镜像/动态依赖与恢复集扫描：[合并前容量审计](remote-premerge-storage-audit-20261014.md)。
- 本轮只读补充：`eval/release_risk_review_20261014/risk-metadata.json` 与 `inspect_risks_remote.py`。未进行备份、重启、认证探测、业务写入或删除；新方案中的保护尚待后续实施。
- 本地机制核查：`production_v2.py`、`v2_read/cli.py`、`backup_retention.py`、Site Strategy `service.py`/`profile_storage.py`/`repository.py` 及正式 Compose。使用现有 Backup API、Profile 维护快照和持久卷，不另建通用保护框架。

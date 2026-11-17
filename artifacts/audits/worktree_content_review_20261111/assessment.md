# DoxAgent 辅助 Worktree 内容价值复审与迁移方案

日期：2026-11-11。已执行 `git fetch origin`，成功后 `origin/main` 仍为 `3348f2fcd5fc3683fa9975c30003eb810933e396`。本轮没有迁移业务文件、提交、推送、清理工作树或分支；仅新增本地审查材料。

## 结论

上一轮 C、D 类共 **14 个辅助工作树，逻辑占用约12.47 GiB**。它们全部HEAD均为最新main的祖先；内容审查后，**全部建议退役**，其中6个可直接进入获准后的正常退役，W1 Index先处理已被吸收的11个旧修改，另外7个先迁出必要证据/成果。没有发现必须依赖原目录继续工作的E类工作树。

这不表示所有业务问题都已解决：前端指标方案未实施、W3内部P95≤5秒未达标、Storage的24/72小时及七天自然验收未完成。这些工作均可在main或新任务分支继续，原代码和复核依据都有明确落点，无需为未完目标永久保留旧树。

建议A类交付仅2项派生成果：一份经更新、脱敏的指标口径实施方案，以及一个融入既有测试文件的n=1/正文0%回归。**没有应直接补入main的新产品Python模块。** 14树建议B类归档374个文件，未压缩约37.67 MiB。归档后才能移除原始唯一证据。归档只是建议，本轮尚未执行。

为防止沿用上一轮对忽略文件的误判，额外复核了4个原B类候选；其中RSS Race存在值得归档的忽略证据，不能直接当作“无额外数据”退役。额外4树约3.27 GiB，和本轮14树分别计数。

## 审查方法和完整性

- 逐文件读取W1的全部11份diff，并与main当前文件、初次集成提交和后续修复比对；不存在其他已跟踪修改。
- 全量枚举18个辅助目录的20913份未跟踪文件及全部Git忽略文件，生成221881条文件记录。非依赖/缓存内容均实际读取并计算SHA256；对JSON解析结构、Python AST及嵌入代码、SQLite只读schema、JUnit结果、压缩包全部成员进行检查。原始证据保存在审查包中，精确归档范围在 `archive-files.csv`。
- Python历史比对使用main祖先对象集合；W3生成目录35份、Storage生成目录900份Python副本全部匹配main当前或历史blob。W3另外23份生产/审查Python副本亦全部来自main历史。未发现混入未迁移的源码或测试。
- 生成目录的判定结合全量路径/文件内容结构、对应测试函数、写入规则及复制源码指纹，并非仅按pytest目录名。209个JSON解析例外另审：206个是模拟文本，如100000个x/p、`retained or reproducible`和路径哨兵；另3个是实际运维混合日志，归档B，不当作损坏垃圾。旧测试的“五物理单元”已改为六单元；被撤回的D3 abandoned测试遗留是模拟native内容和测试库，不是未完成业务数据。
- 依赖、dist、字节码、pytest/ruff状态等按锁文件、包目录、构建结构确认可重建，未逐个审计第三方依赖源码。bus目录54个读取错误均在node_modules可选平台链接/包文件，不涉及唯一业务资料。扫描不等同于检测依赖目录是否曾被人为修改。
- 阅读了10个相关Codex任务的实际最近开发结果，并结合main代码及报告核实；idle/notLoaded/近期日期不作为独立保留依据。未运行这些历史脚本，未发网络业务探测或读写生产。

## 14个工作树逐项决定

下表路径均为准确根路径；子文件路径在后续章节及CSV中相对此根。D表示代码/有效成果已进入主线，C表示可重建或重复产物，B表示私有本地归档，A表示建议补入main的派生成果。

|准确根路径|实际额外内容及价值|未进入main的开发成果|处理及退役结论|
|---|---|---|---|
|`C:/Users/WEIXUANXIE/.codex/worktrees/bus-metrics-reading/DoxAgent`|14个未跟踪文件：1份指标方案、6个排查/复现脚本、4份JSON、3个空stderr；忽略内容主要为前端依赖、构建及缓存|A：通用方案和边界回归；无产品实现|10份原始方案/取证脚本/JSON归档B，复现脚本转正式测试；3个空stderr和依赖C。完成迁出后退役|
|`C:/Users/WEIXUANXIE/.codex/worktrees/execution-queue-robustness/DoxAgent`|5份执行修复证据：broker持仓查询、部署前镜像/状态、inventory、查询脚本|无；执行队列修复和报告已在main|5份B，保留历史账户/运维证据；缓存C。归档后退役|
|`C:/Users/WEIXUANXIE/.codex/worktrees/runtime-p0-main-integration/DoxAgent`|无修改或未跟踪文件；忽略内容为缓存|无；P0修复及验收记录已在main|D+C；直接建议退役。对话曾报告恢复队列未完全消化，不代表这个旧checkout仍有待合入成果|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent/.worktrees/live-margin-main-20261111`|无修改或未跟踪文件；仅缓存|无；配额/保证金预览通用源码和模拟测试已合入main|D+C；任务明确完成同步，163回归记录已保存；建议退役|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-luna-r3-20261111`|无修改或未跟踪文件；仅缓存|无；原生Luna R3、父上下文回放和真实验收已在main|D+C；无需因11/11创建而保留；建议退役|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-w1-index-20261106`|11份旧修改，详见逐文件表|无；全部功能已集成并有后续修复|D；不要复制旧文件覆盖main。旧diff已留审查包；获准后按11个精确路径处理旧工作区修改，再正常退役|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-w3-execution-20261110`|2229未跟踪文件：2194测试生成物、35份基线/审计/回归文件|无产品代码；有10份历史审计证据B|迁出10份B约10.30MiB；生成物、旧源码副本和重复基线无需保留目录。继续性能工作从main开展；建议退役|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-w3-luna-fast-20261111`|无修改或未跟踪文件；仅缓存|无；Fast开关及生产验收已在main|D+C；`17c8e4b61`/`a0a249d9c`已被main吸收；建议退役|
|`D:/DoxAgentHttpCoverage20261107`|211份未跟踪文件：实际站点HTML/RSS/API响应、覆盖对照、TechNews重放、发布/失败窗口、16个取证脚本和重复batch包|无产品代码；覆盖修复、fixture和正式报告已入main|210份B约14.45MiB；重复batch包C。脚本是固定日期/容器路径的取证，不作为通用工具直接进入main；归档后退役|
|`D:/DoxAgentTradeUpgrade`|无修改或未跟踪文件；前端依赖/缓存|无；已有收入审计、Live/Paper接入工作及后续main配额修复覆盖本树|D+C；最近实盘任务已在主目录/独立margin树完成，本目录无残留私有配置；建议退役|
|`D:/DoxNewswireHttp`|52份未跟踪：BW原始响应/identity/发布/观察数据、8个一次性脚本及4份根目录回归日志；忽略目录还有模拟测试和两份报告|无产品代码；BW/Newsfile修复和通用测试均已入main|45份B约0.93MiB；空文件、模拟测试C；忽略的最终报告逐字匹配main，进度稿被其替代。归档后退役|
|`D:/DoxStorageBaseline20261111`|无修改或未跟踪文件；仅缓存；Detached基线|无；基线`ace2b960`有命名分支及main历史可重建|D+C；长期观察需要基线指纹，不需要实体checkout；建议退役|
|`D:/DoxStorageLifecycle20261111`|18399未跟踪：18274生成物、123份治理材料、2个缓存/哨兵|无产品代码；owner/retention/发布门禁等均已在main|123份中91份非空证据/脚本B约11.71MiB，32份空输出C；生成物和2份缓存C/D。长期观察迁移后从main继续；归档后退役|
|`D:/DoxW3CdcRepair20261111`|3份未跟踪：修复中间回执、最终86Case比对、JUnit|无；CDC修复和最终报告即当前main|3份B约0.11MiB；缓存C。任务已完成，归档后退役|

各树历史跟踪数据中出现的 `replay.sqlite` / `observations.sqlite3` 是已提交的验证夹具；可从Git恢复，不属于独有本地数据库。14个辅助目录未发现必须保留的未追踪 `.env`、私钥或用户数据库；当前主目录及其环境配置不属于本轮退役对象。

## W1 Index：11份diff的逐文件结论

根：`C:/Users/WEIXUANXIE/Desktop/DoxAgent-w1-index-20261106`。以下全部归D，无需迁移或重做提交。审查包逐文件保存了diff。

|精确相对路径|实际改动|main吸收/替代依据|
|---|---|---|
|`changelog`|日冻结、事实补入、独立W1 pin两条说明|main已有同义实现记录；第二条原文仍在，不追加旧的“本轮未发布”重复记录|
|`docs/w1-daily-known-index-20261106.md`|58行设计与本地验证说明|与`29d8fb837`中文件逐字一致；main又追加minimal branch/retired publication生产兼容修正|
|`src/doxagent/event_library/compiler.py`|可选day筛选入口|工作文件与当前main一致|
|`src/doxagent/event_library/provider.py`|known_index传递day|工作文件与当前main一致|
|`src/doxagent/event_library/repository.py`|Fact首次准入、Reference/14天/7天并集|旧文件逐字进入`29d8fb837`；main的`891382c0c`/`942c39366`补最小分支历史和退役时钟，不可用旧文件覆盖|
|`src/doxagent/persistent_runtime_v2/daily_index.py`|日冻结、已发布事实PROCESSED桥接|旧文件逐字进入集成提交；main的`3974f98e7`新增冻结Fact内容集合、空白归一化及桥接过滤，防止跨分支历史事实膨胀|
|`src/doxagent/persistent_runtime_v2/openrouter.py`|W1日合同重试说明移动态尾部|main保留完全相同的daily metadata判断、split_instructions和Runtime Retry Correction；文件差异来自后续供应商/缓存等演进|
|`src/doxagent/persistent_runtime_v2/providers.py`|filtered_index、新Fact读取|旧文件逐字进入集成提交；main再扩展published_facts供冻结集合去重|
|`src/doxagent/persistent_runtime_v2/repository.py`|daily表、单赢家冻结、already_published|旧文件逐字进入集成提交；main仍有Protocol/InMemory/SQLite三套daily_known_index和原子PROCESSED逻辑|
|`src/doxagent/persistent_runtime_v2/service.py`|日冻结接线、独立W1根路径/版本、指令/详情pin|main仍保留全部关键路径；额外调用frozen_fact_contents/visible_library_bridges并有W3/模型迁移改进，不回写旧整文件|
|`tests/test_w1_daily_index.py`|205行窗口、并发、跨日、重试、详情隔离回归|旧文件逐字进入集成提交；当前文件完整保留并新增minimal branch和retired clock回归|

仅凭“git status有11项”保留这棵树没有必要；直接将旧补丁再应用main反而可能回退已发布修复。本轮不擅自丢弃这些未提交文件；后续须按明确清单获准处理。

## W3 Execution：2229个文件的拆分

- `eval/w3_nonsdk_audit_20261111/pytest-tmp/**` 2187份，加 `diagnose-tmp/**` 7份，共2194份：全量检查了模拟Runtime/Research库schema、case/lease/provider结果JSON、schema/prompts、日志、Native分块清单和源码副本；35份Python均匹配main，属于确定性测试现场，不是必须归档的真实延迟样本。
- `eval/w3_latency_implementation_20261111/production-sources/**` 5份Python与`review-baseline/**`中18份Python都在main历史；`review-baseline/proof.json`逐字等于main的`production-baseline-proof.json`。`production-baseline.tar.gz`的19个文件全部在main历史且全部有已展开副本。归D，不重迁移源码，也不为这些副本保留checkout。
- B类精确10份：`calendar-regression.xml`、`causal-followup-regression.xml`，及nonsdk目录内 `frozen-cohort-summary.txt`、`ledger-latest-summary.txt`、`ledger-latest.json`、`ledger-summary.txt`、`ledger_analysis.json`、`metadata-latest.json`、`metadata.json`、`targets.json`。它们记录具体历史cohort、结果及归因边界，不能从今天重跑复原。
- 性能硬指标仍未完成：最终固定11例Trade/Operations/完整Case P95为6.066/6.692/12.009秒。main报告和探针已经保留最终版本，旧目录只补足上述历史取证。后续调优从main开展，不需要回到旧HEAD。

## Storage Lifecycle：18399个文件与123份治理材料

`eval/pytest-*` 共18274份，约2.24GiB：1585个SQLite模拟库、9382个JSON、5730个文本、900份Python副本、662个二进制及15个测试tar。全量Python匹配main；模拟库包含测试Research/Runtime/Read/owner/CAS结构，JSON/text包含已知模拟占位。它们是可重建的测试现场，不等于18274份生产审计成果。

`eval/storage_lifecycle_20261111/` 精确123份：**91份B、32份空输出C**。B约11.71MiB；不为节省几MiB丢弃有意义的发布失败、撤回候选和前后对照。精确91行见archive清单，重点内容如下：

- 最终验收：`final-production-verification.json`、`final-source-hashes.json`、`final-state.json`、`completion-metrics.json`、`final-evidence.json`。实际source hash无mismatch、常驻角色和一次性刷新核验为true；这些是历史时点证据，不是当前生产健康保证。
- owner归属修正：`multi-ledger-inventory.json`、`snapshot-owner-inventory.json`、`owner-paths.json`、`refresh-owner-paths.json`、`legacy-owner-*.json`、`d3-candidates.json`。多控制库846份唯一owner/3份未知的证据推翻早期单主库解释，应一起保存对照，不把过时单库结论继续作为事实。
- 发布与压缩：`bus-compaction-preflight.json`、`after-bus-compact.json`、`before-release.json`、`after-release.json`、`production-preflight*.json`、`production-final-code.json`、`worker-baseline-cancel.json`、`ledger-*.json`、非空build日志/错误输出及JUnit。3个扩展名JSON实际是多段运维输出，仍归B。
- 28个一次性脚本也归B，附带旧生产路径、容器名、commit、候选和基线，属于复现/追责材料。`bus_freelist_compact.py`会停容器、VACUUM和重置扫描游标；`owner_catchup.py`/`owner_targeted.py`会执行GC；`deploy_cancel.py`指向后来撤回候选。**不将这些历史动作脚本作为通用生产工具直接迁入main或再次执行。** 正式通用owner、容量探针与发布脚本已在main。

`eval/tmp/`另外2份为分词缓存和命令哨兵，归C。

仍未完成的是24h/72h/7d自然收敛、完整前端因果对照及部分恢复责任验收。继续观察只需main已有正式代码、远端既有owner/容量回执和这91份基线。没有本地长跑进程、未合入实现或必须留在这个checkout中的状态；迁出证据后建议退役原树。

## HTTP Coverage、Newswire、CDC及前端的实际价值

### HTTP Coverage

211份未跟踪内容已读取：HTML/RSS/API响应及缓存头、窗口内HTTP与官方RSS集合对照、TechNews上午时间解析漏稿重放、Canary/NSF/Salesforce/CRN探针与验收。`batch.tgz`两个成员逐字等于展开响应，C；其余210份B，含16个固定日期和生产路径的取证脚本。

正式修复、fixture、190项测试结论和 `docs/http-source-coverage-and-latency-followup-20261107.md`已入main。`acceptance-final.json`保留并行发布超时/失败，不能只因文件名final就称全部成功。没有独有产品代码；`compare.py`/`replay.py`等有复核价值但硬编码2026-11-04/05窗口，保留随证据归档，不额外包装一个新工具系统。

### Newswire HTTP

45份B的精确清单包括BW48份未跟踪材料中的非空/非重复部分及4份根目录真实测试输出（37/40/44/116通过等分组，不相加）。保留 `before.json`、`after.json`、`identity-live.json`、`production-final.json`、`release-receipt.json`、原响应及脚本，可追溯URL去重、隔离和新稿延迟。

`adapter_probe.py`含base64嵌入的产品源码，已解码逐函数比对：除parse_businesswire外函数均与当前main AST相同；该函数的main版增加HTML实体解码，保留原headline生成slug，旧探针不是遗漏的新实现。归B作实验快照，不覆盖main。

忽略的 `.test-tmp/report-final.md`已逐字进入 `docs/newswire-http-discovery-migration-20261107.md`，归D；`report-in-progress.md`是被最终版替代的进度稿，归C。其余忽略目录为模拟acquisition/identity/正文测试库和产物；正式报告与54通过/1跳过验收已在main。原任务明确完成发布，无需保留checkout。

### CDC Repair

`eval/w3_cdc_receipt_repair_20261111/after-live-output.txt`、`production-final-output.txt`、`regression.xml`全部B。中间记录尚有缺口，最终记录gaps为空且86例源/Read一致；正式CDC JSON类型修复、计时修复和报告均在当前main。无剩余开发，不把中间“health starting”误读为当前未收尾。

### 前端指标

前端任务实际只交付了勘察与方案，没有声称完成实现。当前main的 `w3_contributions()`仍混合全部W3阶段并取整体终态跨度，故方案主体有价值。另一方面 `serving_fields.py`现在已包含 `decision_completed_at`；旧方案5.2第2步“新增该字段”已被后续实现吸收，迁移前必须删改该步骤，不能原封不动再开发。

6个脚本中 `inventory.py`/`sample*.py`/`detail*.py`读取历史固定case/部署路径，归B；`local_reproduce.py`是纯本地单样本复现，证明n=1、2秒均值/P95及正文0%，可转成现有测试文件的回归（A）。已有test_bus_dashboard覆盖n=1 e2e，但没有同一场景的正文真实0%断言，补这个边界即可，不照抄整个脚本。

原238行方案含生产case身份、具体镜像/运行统计，原件归B。A类文档仅提炼业务口径、轻量实施与验收部分，删除私有个案和部署细节，更新已被main吸收的步骤。

## A类精确迁移清单（本轮仅建议）

|来源完整路径|建议main目标|迁移方式|
|---|---|---|
|`C:/Users/WEIXUANXIE/.codex/worktrees/bus-metrics-reading/DoxAgent/docs/frontend-bus-source-and-runtime-latency-audit-plan-20261111.md`|`C:/Users/WEIXUANXIE/Desktop/DoxAgent/docs/frontend-bus-source-and-runtime-latency-audit-plan-20261111.md`|提炼3.3、5、6等通用方案，去除原始case/镜像/私有运行明细；更新decision_completed_at已在serving字段中的现状；保持为待实施方案，不冒充修复已完成|
|`C:/Users/WEIXUANXIE/.codex/worktrees/bus-metrics-reading/DoxAgent/eval/frontend_metrics_audit_20261111/local_reproduce.py`|`C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/test_bus_dashboard.py`|将19行场景转为pytest tmp_path测试，复用现有ReadStore fixture/辅助函数；只补n=1、均值/P95、正文0%的真实边界，不搬SSH取证脚本|

其他源码/测试无迁移项。若后续实施这两项，仅暂存两个目标文件和必要changelog；当前main已有其他未跟踪资料，不混入提交。

## B类精确归档范围与退役顺序

`archive-files.csv`逐行给出源根路径、相对路径、SHA256、大小及建议目标，包含全部374份本轮资料，另含额外RSS Race的110份资料。建议本地目标为 `D:/DoxAgentArchives/worktree-retirement-20261111/<工作树标识>/<原相对路径>`。这是拟议路径，本轮未创建或复制归档。归档不进入main/Git，不上传。

1. 先按A清单处理两项派生成果，并按B清单复制/归档；核验数量、大小和每个SHA256。原始文档与实际生产证据保持原字节。来源脚本里的旧路径只作历史说明，不自动改写或执行。
2. 接着处理W1的11个已被吸收修改：审查包已有逐文件diff；必须在后续获准清理名单中明确授权舍弃这11项。只针对这11个精确路径恢复HEAD的工作区/intent-to-add索引状态，再正常remove；不使用hard reset、clean或force。未获准前保持原样。
3. 对C类生成目录，按文件清单和已经核对的根路径精确清理；对B类，只有归档校验成功才移出原目录；不使用“清空整个eval”规则，不把tracked fixture当垃圾。任何新增文件或哈希变化都从旧清理清单剔除并重审。
4. 原树退出后仅用 `git worktree remove <准确路径>` 正常移除；有拒绝就停在该树处理具体原因，不force。保留所有分支。本轮不执行任何上述步骤。
5. 主目录和main保持不变。退役后未完工作从最新main继续：前端方案实施、W3两个剩余瓶颈探测、Storage长期自然验收；这些是待办，不是留住历史目录的理由。

逻辑空间估算不含共享.git，也不等于精确NTFS回收量：本轮14树12.47GiB，扣必要本地归档仅约0.04GiB；实际磁盘回收要以未来退役前后卷空间核验，不能把D盘释放当作C盘收益。

## 对额外4个原B候选的修正

- `C:/Users/WEIXUANXIE/.codex/tmp/doxagent-relevance-20261103`：D，干净、无额外忽略资料，main祖先；建议退役。
- `C:/Users/WEIXUANXIE/.codex/worktrees/main-convergence/DoxAgent`：D+C，无额外成果；建议退役。
- `D:/DoxAgentDialogFix`：D+C，UI修复已在main，仅依赖/缓存；建议退役。
- `D:/DoxAgentRssRace20261107`：代码D，但忽略的 `eval/rss_http_race_20261107/` 有实际网站响应/发布日志，110份非空B约17.58MiB。它们不在上一轮untracked统计中；其余测试库、WAL等可重建。归档后退役，不能直接按“没有untracked”跳过归档。

本轮没有修改Codex会话cwd或内部数据。退役会使历史对话原cwd不存在；继续任务时使用main目录即可，无需为保留对话文本保留整个checkout。实际执行前仍要重验届时没有任务正在向被退役路径写入，这是执行时并发检查，不是本轮内容价值保留理由。

## 审查材料

- `archive-files.csv`：精确B清单，含建议归档目标。
- `file-decisions.csv`：全部额外文件A/B/C/D决定及理由；生成文件不逐条展开在正文。
- `file-manifest.jsonl`：内容类型、SHA、JSON结构、SQLite schema、main重复/历史匹配；依赖按包/构建结构分类。
- `trees.json`、`content-checks.json`、`archive-checks.json`、`bw-embedded-check.json`：分支关系、完整性、压缩成员和嵌入源码比对。
- `task-results.json`：实际任务完成/未完结果；11个`.diff`：W1逐文件证据。

大体量中间清单与原始内容只保存在压缩审查包中；正文、精确归档清单和汇总保留为可直接阅读文件。这些是一次性审查材料，不是新的Worktree管理系统。

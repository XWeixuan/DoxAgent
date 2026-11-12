# Runtime → Read 投影性能修复执行回执（2026-11-12）

## 交付结论

本轮按[复查修复方案](runtime-read-projection-followup-diagnosis-and-repair-plan-20261112.md)实施并发布。Runtime小时级积压已消退，20:49的生产采样首次同时观察到四源追平。20:29约7,624坐标的Runtime距离，到20:48:31降为0，持续输入期间约19分钟消退。

**严重积压故障已恢复，但完整性能合同尚未全部通过。** 自然延迟、冷激活、Reference、持续容量及全机资源的未达/未证明项在下文保留。17,204个历史gap未清除，本次追平不代表历史完整性全部恢复。

应用提交：`90647e2f2e47265fb78dda3eb5103446af2682cc`；API/Web为祖先提交`f818d0b4ab4f`。代码已推送main。实施工作树：`D:/DoxProjectionFollowup20261112`；Desktop原工作区既有脏改动保留。

## 已执行的修复

| 环节 | 实际修改及正确性边界 |
| --- | --- |
| Library | 拆开身份解析和整库物化，复用已完成版本。旧库先核对成员、root、版本、出生身份；不确定provenance回退物化lineage-gap证据。真正新版本共用一个只读连接，消除逐fact建连和重复版本预检。 |
| Reference | captured摘要复用不可变成员；跨root另核对出生身份和pinned Library。成员与完成凭据同事务提交。相同输入且当前delta匹配完成凭据时，只更新任务证据、日链及当天状态。新task仍保留审计；保留日内累计旧before/V0基线。 |
| 激活/文档 | 按发布run与artifact SHA复用正文和页面，每次核验发布态/ticker；继续更新lineage、admission、active关系。相同D2在一个激活内只解析一次；复用时仍读取固定D2的Shell摘要，保留Policy归属。 |
| Policy | lifecycle只定位新集合和上次集合，usage按Policy键范围定位；head存在性不变时不枚举历史目录。首次、删除/恢复仍重算，派生前以native水位保护迟到head。历史catalog与详情保留。 |
| SQL | EXPLAIN确认普通JOIN即使指定current_identity仍可能反向扫描全ticker。请求键与Bus父子聚合固定外层连接顺序，使用current_identity/current_parent完整键；未建大型新索引。 |
| 源读取 | 每轮每源懒开一个自动提交只读连接，读页缓存每源1MiB，轮末关闭。源写入/ACK保持独立，不跨mapper持有源读事务；新增“源端新提交可见”和“缺失源边界”回归。 |
| 计时 | source/table/namespace、候选记录量、map/ingest、7档直方图、verified skip、writer等待/持锁/COMMIT计时。慢事件每分钟最多5条，不打印正文/账户。按真实时间戳复算窗口。 |
| 页面 | 现有Read元数据保存源坐标、源事件时间、最老待同步时间，按页面依赖源随ReadContext固定快照返回。刷新时钟与事实时钟分开，历史gap独立显示。header显式选择新字段，旧闭合schema客户端兼容；无新增轮询器。 |

使用既有object_current/read_meta，无新增表、服务、writer或业务优先队列。未重置generation/checkpoint，未调整上游业务并发，未触发模型重跑或交易动作。变更均已追加changelog。

## 对照、测试与发布证据

- 两条生产重型Reference只读对照：6.033秒→0.342秒、20.525秒→0.548秒；候选记录4,161→35、13,051→65。省略项载荷、元数据及贡献差异均为0。此为mapper对照，不冒充完整消费容量。
- 纯状态更新两组对照：70/22条输出→3条，载荷差异0；mapper约55–164ms，尚不是已达到50ms端到端目标。
- Bus父子聚合66ms→0.9ms；源元数据对照72次连接/0.612秒→4次/0.053秒。
- 真实789条激活输出的2MiB/16MiB写缓存对照未显示收益，未应用此设置。临时库总量22.8MB，已清理；外部非阻塞采样器已清理，未复制35GB生产Read。
- 后端106项核心回归及16项snapshot/备份/保留/排序回归通过；前端70项测试、1项浏览器用例、TypeScript与构建通过。保留既有bundle大小提示；未声称全仓库测试全部执行。
- 生产14份应用文件匹配Git提交字节；前端40个构建文件校验通过，并补做公开HTTP读取检查。API/Projector仍使用同一Read与generation `75a54e2449a84065a0aee86dac09e353`。最近50个Case源/Read状态及W3 operations状态核对一致，Runtime ACK与checkpoint一致。
- 既有认证MCP summary读取AVAILABLE、refresh_error为空，旧客户端路径有效；aggregate freshness仍为STALE，未删历史gap换FRESH。

**发布漏项及修正：** 初次Web覆盖包在私有umask 077下解包，静态目录权限导致资源403/白屏；只验哈希和auth/config健康检查没有覆盖Nginx实际读取。这是本轮发布验证的遗漏。并行白屏修复任务已将公开目录/文件归一化为0755/0644，并增加worker权限负对照、JS/CSS HTTP探测和健康检查。本分支保留并合并其脚本修正。生产Web由原`975d6bb2db28`修复为`afd7485fe379`，40项内容哈希一致；实际HTTP资源检查结果另存证据。不能把修正后的结果倒写成初次发布已无故障。

生产canonical状态（Web包含上述权限补丁，验收同时核对内容、权限和HTTP）：

| 角色 | 当前镜像 | canonical commit |
| --- | --- | --- |
| v2-projector | `sha256:51b22cdf3ede257780ef584d760011ea5bffa583401a395ed0507583013ec8f4` | `90647e2f2e47` |
| v2-api | `sha256:fd787b43812ebe7a172d9c2808e876114b90433e8242027491a876d67a5393da` | `f818d0b4ab4f` |
| v2-web | `sha256:afd7485fe3792c130b1c85c0afdfd4cafcab74183a744510917535f39f8ff178` | `f818d0b4ab4f` |

只覆盖Projector、API、Web。Maintenance、Scheduler、Transport Maintenance镜像和启动时间与诊断基线相同；其他业务角色未由本次发布重建。回退沿用canonical release owner及其私有manifest/state快照。

## 最终版本连续生产窗口

北京时间 **21:17:34–21:47:34**，30.00分钟，61个30秒样本。最终Source-reader版本单独观察，不把此前候选混入其窗口。

λ/μ为坐标速率，不能当实际mutation数。“变更回执”为产生Read版本的源回执commit；verified skip、映射后无变更分别计量。研究源没有Read业务变更样本时标为未观测，不填0秒延迟。

| 源 | λ坐标/s | μ坐标/s | 坐标距离起→止 | 变更回执样本 | 延迟P95秒 | P99秒 | 最大秒 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 研究 | 0.19 | 0.19 | 0 → 0 | 0 | 未观测 | 未观测 | 未观测 |
| 初始化 | 0.03 | 0.03 | 0 → 0 | 48 | 19.17 | 23.59 | 23.59 |
| 消息 | 4.33 | 4.33 | 1 → 2 | 692 | 6.06 | 8.08 | 10.78 |
| Runtime | 1.60 | 1.60 | 1 → 0 | 1536 | 12.25 | 16.34 | 22.13 |

延迟从追平后的新源事件时间算至commit_retention登记时间，仍少一个COMMIT尾部及页面刷新周期，不能当浏览器展示的精确端到端延迟。30秒pending-age与逐commit分位分别统计。

覆盖本窗口的29个完整日志段：mapped 9683条、verified skip 1288条。日志边界可能包含少量窗口边缘；速率与提交分位另受采样窗口约束。候选输出数不是实际改写行数。延迟分位取最终版13:18至窗口末端的新源事件。

| 工作类型 | 整事件耗时直方图摘要 |
| --- | --- |
| Reference | 51条；P95桶 ≤5秒；最大 1.46秒 |
| 授权head | 26条；P95桶 ≤50ms；最大 0.01秒 |
| 激活revision登记 | 26条；P95桶 >5秒；最大 5.81秒 |
| 初始化指针 | 26条；P95桶 ≤5秒；最大 9.02秒 |
| Runtime生效切换 | 25条；P95桶 >5秒；最大 11.68秒 |
| Bus成员 | 70条；P95桶 ≤100ms；最大 0.18秒 |

资源来自cgroup CPU/块IO和真实进程RSS，未将docker-init RSS或含文件缓存的memory.current当Python RSS：

资源为独立15秒采样的30分钟窗口，与源采样起点约有10秒偏移；原始时间戳保留，不按固定报告周期外推。

| 项目 | 实测 | 合同 |
| --- | --- | --- |
| Projector CPU | 平均 0.25 核，15秒区间最大 0.43 核 | 平均≤1核 |
| Projector RSS | 最大 107.16 MiB | ≤1GiB |
| Projector块写 | 平均 4.12 MiB/s | 与GC合并评价 |
| Read GC块写 | 平均 2.49 MiB/s | 未修改GC角色 |
| 两者合计块写 | 平均 6.60 MiB/s | 稳态≤5MiB/s，追赶≤10MiB/s |
| 全机CPU | 平均 86.91% | 目标≤80% |
| CPU PSI some avg60 | 均值 44.15% | 目标≤20% |
| IO PSI some avg60 | 均值 4.77% | 目标≤5% |
| 根盘可用 | 核验时 15.30 GiB | 15GiB停止试写/构建线 |

全机CPU/PSI包含其他角色；不能将全部争用归因Projector，也不能据本轮替其他角色签收。区间内受限探测/fixture开销不隐藏。

根盘余量已接近15GiB停止线，本轮不再构建或试写。最终30分钟Read主文件保持34,745,266,176字节不变，窗口末WAL约50MiB；不能把全机余量下降全部归到Read主文件增长，其他目录需按既有生命周期核验归属。该容量边界意味着不能承诺长期资源验收已完成。

## 验收判定与未完成的严格指标

1. **已恢复：** 小时级积压消退，普通事件没有重新形成长时间积压；近期Case核对一致，无新正常业务gap；generation、旧view/排序、崩溃重放及源新提交可见性回归通过。Projector CPU/RSS按本轮实测签收。
2. **自然延迟逐源判定：** P95≤10秒、P99≤30秒任一源未满足时保留未达，不用Runtime改善替初始化签收。研究源无mutation样本，仅证明坐标追平。
3. **严格耗时未全过：** 注册库Reference P95≤250ms、纯状态≤50ms、genuine cold activation P95≤2秒尚无完整通过证据。较大生效批次仍有秒级长尾，map/ingest已分离计时；切换在原事务完成后才可见，未发布半成品。
4. **容量未签收：** 真实旧混合负载追赶均值约8坐标/s，未证明Runtime连续30分钟≥12/s。稳定期自然输入不足以证明四源≥35/s持续容量；不以skip、短峰或空映射量充数。
5. **资源未达按表保留：** Projector+Read GC稳态块写若超过5MiB/s、全机CPU超过80%或CPU PSI超过20%，不写全部达标。不擅自降低业务配额或暂停必要GC美化数字。
6. **长期验收待自然观测：** 完整主要业务时段、24/72小时及7日Read/Native/根盘增长尚未观测完毕。根盘接近15GiB停止构建/试写，按既有owner处理明确可退出产物；禁止全机prune或全库VACUUM。

剩余严格性能工作只针对已可测路径：以真实大生效批次为单位，分离大Read实际变更、逐对象比较/水位、锁等待与COMMIT；核对重复ACK/exception-pins及fact同步的无变化扫描。先完成同负载输出/元数据/水位对照和≤256MiB隔离试验，有量化收益再改。不先启用第二writer、业务优先队列、长期结果缓存或准备服务。此段不是已发布功能或已通过验收的声明。

## 历史gap独立结论

保留 `ValueError 17,138 + PROVENANCE_UNVERIFIED 66 = 17,204`，本轮没有新增或清空。

全部gap的hot头部及exception-retained包头均可定位：66项来自runtime_values；17,138项为交易费用/执行依赖链，包括te_events 16,635、te_attempts 242、te_jobs 165、te_allocations 60、te_lots 22、te_fills 14。hot未返回的10,695项仍有保留包，不是回执丢失。

只读重新映射抽样仍有费用事件缺执行依赖、worker invocation缺provenance。尚未逐项证明为有效Serving业务或可排除事实，不能统一忽略或重放所有费用。恢复顺序：按原坐标解析源/冷库归属→核对Case/fill/attempt/job/execution与provenance→恢复可验证依赖→小批重投影→核对水位、旧view和金额/份额/费用不重复→才解除相应gap与pin。禁止模型重跑、订单提交、未知归属修补和批量删gap。

## 证据与回退

证据目录：`eval/projection_materialization_repair_20261112/`。Git中提交汇总和复核脚本；完整raw采样保留Desktop同目录，未导出消息/研究正文。

- `acceptance-90647e2-summary.json`、`latency-90647e2-final.json`：最终窗口、分位与资源。
- `release-verification-90647e2-final.json`、`alias-final.json`、`validation.json`：实际部署、文件、Read别名及回归范围。
- `readonly-candidate-compare.json`、`state-only-compare.json`、`source-metadata-compare.json`、`write-cache-probe.json`：正/反对照。
- `gap-retained-headers-final.json`、`gap-probe-final.json`、`latest-cases-final.json`：历史边界和近期Case一致性。

复核以同一generation和真实时间窗口为准，不把本轮数字写死为期望值。若出现新语义差异、正常gap增长、旧view失效或水位越过未提交事实，在release.lock内调用 `scripts/ops/storage_release.py` 的canonical deploy/rollback所有者，按本轮manifest备份恢复受影响角色镜像；保留源事实和Read坐标，不回滚业务数据库。新字段显式选择且可选，代码回退不要求倒退Read generation。

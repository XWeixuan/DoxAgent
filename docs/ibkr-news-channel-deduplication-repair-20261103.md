# IBKR 多频道去重修复实施记录

日期：2026-11-03。依据 `ibkr-news-channel-deduplication-diagnosis-and-lean-repair-plan-20261103.md` 完成本地实现；未提交、部署或改变生产订阅。

## 1. 已实施

- **P0-A 文章身份：** 新增小型纯函数模块 `message_bus_v2/ibkr_identity.py`。仅已验证的6个 Dow Jones 频道、且 providerCode 与 articleId 前缀一致时，生成 `ibkr:dowjones:<core>`；接入原 BODY locator、已验证文章别名与发布候选。未知格式/非DJ保留原身份。原 external_id、SDK参数、投递确认、checkpoint与频道覆盖不改。
- **P0-B 内容与更新：** 共用原生内容整理规则，移除有明确边界的 Dow Jones END/MORE TO FOLLOW 尾注；事实在标题中的原生 flash 使用真实标题作为短内容，明确 `native_provider_flash/SHORT_FULL`。没有事实标题且只有模板时过滤并记采集计数；坏标题但真实正文存在则保留，已有有效标题不被清空。原 SDK articleText 原样保留，native 校验仍与它的确定性解析结果比较。
- 保留明确 SDK 语言，不把未知语言一律变成英语。对有 END 完结证据的已验证 DJ 原生文章，允许真正的短正文；不会要求用版权字数凑满长度，也不会把正文里的普通相关阅读措辞当作缺正文。**未放宽通用 HTTP/Reader 完整性或硬正文 hash 规则。**
- 同文新频道仅登记 origin并复用文章；有实质原生正文/标题变化时走原 content_update/input_revision。新长文可以接替 flash、在途旧结果失效；后到 flash不能覆盖完整或正在晋级的原生长文。规范化 material_hint/title/outcome复用原薄查询；仅潜在降级的变化快讯需要锁外读取旧输入，不在普通不变轮询中展开对象。
- **P0-C 旧身份：** 新alias未命中时按6个旧频道 identity_key索引点查，选择该ticker最早发布的锚点。canonical接回其原live key，保留Raw/Standard/Stream、first_seen和修订身份。历史重复Case和旧owner不删除，不进行全库回填。
- 并发准备与提交复用原alias预约、短事务标量核对和锁外准备；没有新表、迁移、后台恢复队列或跨源等待。

主要改动：`message_bus_v2/{ibkr_identity,ibkr_news,body_results,deduplication,repository,live_repository,live}.py`、`content_enrichment/{native,service}.py`。`live.py` 同时存在另一会话的Google相关性改动，本轮仅增加 `_job` 的IBKR有效标题保留；其它会话的配置、相关性及文档改动原样保留。

## 2. 验证

### 冻结历史样本

`eval/ibkr-channel-dedup-repair-20261103/frozen-replay.json`：63个核心ID组、263条历史发布输入，修复后 **63次发布、每组一个有效正文owner**，全部通过；外部正文 extractor调用0。64个严格字段重复组的244个成员中，242个包含在这次重放，另2个无事实占位在六组坏例重放中单独验证过滤。

正文来自上轮保存的117+9份样本，以及本轮只读主键补取的36份首发代表内容（252,310字节、60ms、2MiB取证上限）。同组缺少独立SDK快照的频道，复用已由首发纯hash证明相同的代表正文，并保留每条原频道ID/标题。所有时间平移到当前接纳窗口，在隔离本地数据库运行，不调用SDK、新闻HTTP、浏览器或Agent/LLM。它证明确定性合并效果，不是生产性能或未来节省率验收；0个HTTP extractor调用不意味着SDK请求已节省。

`six-cases-after.json` 对照原报告六组反例：正常长文2→1、同核心flash5→1、不同事实快讯2→2、无事实占位2→0、11/02长文5→1、11/02 BE flash2→1。

### 测试与回归

`tests/test_ibkr_channel_dedup.py` 覆盖：6频道身份及投递ID不变、未知/非DJ格式、不同时点原生内容更新、短快讯事实隔离、空占位/坏标题、语言/private scope、跨ticker共享正文独立发布、并发准备与旧结果围栏、正在晋级的长文、旧多副本最早锚点、短原生正文与SDK内容一致性、实体字符不重复HTML解析、Runtime接纳及Journal重开。

新增29项均已通过。整组集成回归 **233 passed、1 deselected，539.47秒**，`regression.xml`；补充SDK比较符/语言字段及数字快讯用例后，共 **235项不同测试通过**。`final-targeted.xml` 的38项包含当时28项新增测试与10项原生正文测试，`numeric.xml` 补充数字快讯并复核占位过滤；这些定向结果与整组有重叠，不相加冒充测试总量。未定义名称/重复定义静态检查和 `git diff --check` 通过，原有Windows curl selector/pytest警告保留于日志。

已单独复现既有非本轮失败：`test_prepare_failure_does_not_claim_or_mutate_pending_task` 在RuntimeJournal输入准备抛错后，任务实际为RUNNING，而测试要求PENDING/token=0。RuntimeJournal本轮未改；该项未混入去重修复，整组回归显式排除此项，证据见 `runtime-baseline.log/.xml`。

## 3. 整体复核与边界

- 第一份可用内容立即接纳，不等主频道、不等其它来源；没有新增 ticker数量或任务容量限制。
- 同篇每ticker仍ARTICLE_ONCE；不同核心ID的独立事实不按版权模板合并，真实更新修订原消息，不再次唤醒已消费下游。
- SDK `_article` 缓存及下载并发没有更改。本轮减少的是重复BODY本地处理、发布和下游Case，不能把它宣称为减少全部SDK正文请求。
- 原生内容解析与native校验共用规则；adapter不额外再解析一遍HTML，native也不把已经解码的比较符再次当作HTML。
- 旧重复发布不清理、旧Case不撤销。未知DJ格式、跨语言含糊输入及没有稳定文章证据的近似稿仍沿原规则；没有新建语义去重系统。
- 当前工作区还存在其它会话的Google News/相关性开发。本轮未提交或打包这些工作；测试记录应按当时源码状态解读，不把本地通过当作生产验收。

本轮已追加changelog。后续若获发布指令，应按实际改动构建Collector/BODY/Delivery及加载此代码的Bus角色，核对实际函数版本，再按自然多频道样本区分SDK请求、持久采集输入、唯一owner、BODY执行、发布和Runtime Case计数；本轮没有执行生产发布。

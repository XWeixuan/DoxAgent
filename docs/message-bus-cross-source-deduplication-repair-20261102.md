# 跨消息源确定性去重修复记录

日期：2026-11-02。依据 `message-bus-cross-source-deduplication-survey-20261102.md` 实施；本轮完成本地代码、测试与冻结样本重放，尚未提交、部署或扩大 Google News 绑定。

## 1. 实际修复

### 1.1 统一文章身份，提前复用正文

- `deduplication.article_identity/language/url_kind` 统一新输入、旧输入及发布候选的确定性身份。Benzinga 真实 API permalink、Finnhub 外部原文明确为 article；Finnhub `/api/news` 中间地址不作为原文身份。只对已知英语的 Benzinga/Finnhub/Yahoo 补默认语言，显式语言及 source 配置优先。
- `adapters.py` 在 API 输入声明这些事实；`body_results.py`、Bus 收据复用、Hub 与 Live 使用同一规则，避免 `空语言/en` 再生成两个 BODY owner 或反复补全不变输入。
- URL 仅扩展有样本依据的追踪参数规则：指定 publisher 的 `.tsrc`，以及 Barron's `siteid/mod`。文章选择参数、国家域、路径及未经验证的 www/AMP 关系仍保留。
- `PollingRepository.google_urls` 用已有映射表主键批量读取已解析 wrapper；Live 输入准备直接使用已知原文身份，保留 wrapper/raw_url 与 verified_urls。沿用输入的 allowed_publisher_domains 边界；不在 Collector 槽中联网解析。新 wrapper 继续走现有解析能力。

### 1.2 后到 FULL 可以改善失败结果

- 同原文的新 origin 带来已验证的原生完整正文时，刷新原 article 输入并立即续作，不因先前 UNAVAILABLE/PARTIAL 而忽略它。原生完整性仍使用现有 provider 校验，不把普通摘要冒充正文。
- wrapper 解析命中已有失败 article 时，允许这次新发现完成一次现有预算内的获取；无论成功或失败，最后都合并回原 owner。locator 证据与 FULL 成功证据分开，失败不会留下第二个独立文章所有者。
- 已有 FULL/SHORT_FULL 不被后到失败或摘要覆盖；真正内容更新继续修订原 Raw/Standard，保留首次观察、首次结果与 ARTICLE_ONCE，不再次生成 Stream/下游 Case。

### 1.3 Live 发布前补齐跨 URL 的精确完整正文兜底

- 将“标题+正文修订 digest”和“纯完整正文 fingerprint”分开。后者在锁外计算，随已有结果元数据保存，并写入原 `logical_message_versions.body_fingerprint`，复用现有 `logical_content` 索引。
- 强 URL 未命中时，按 ticker+纯正文 hash 查有限候选，在锁外读取候选并复用现有时间、标题事实、语言、访问 scope、独立报道冲突规则。不是标题相似去重，也不是事件级合并。短模板没有硬正文键。
- Google 标题仅在身份比较时去掉与 RSS publisher 精确一致的末尾后缀；原展示标题和真正数字事实保留，修复 `24/7 Wall St.` 等后缀引入的数字冲突。
- 命中后复用原发布身份，登记本次原文别名并跳过重复相关性/发布。不同 ticker 的发布与 Runtime 接纳保持独立。

### 1.4 并发提交及旧键接回

- 准备阶段的候选结果只传递一次到提交准备，避免相关性前、发布前重复加载同一批候选。
- 短提交事务只做已有索引的标量候选核对。若另一来源在准备后抢先发布，退出事务后重新准备本地发布；不重新抓 BODY，最多两次本地尝试，仍冲突则沿用原 Delivery 重试。
- 对旧 API 已发布记录，优先 alias/来源 identity；缺 URL alias 时使用已有 `raw_article_url` 索引点查，接回旧 Raw/Standard/live key。当前必要写入按需修正纯正文指纹；不扫描历史 Native、不重下旧正文、不重写历史 Case。

## 2. 成本与业务边界

没有新增表、迁移、后台任务、恢复账本、向量库、LLM 判重或全局锁。Native 读取、模型验证、正文 hash 和序列化在发布事务外。事务内增加的是既有 alias/body 索引的有限标量查询；已有 alias 候选时不再执行正文候选查询。

强身份命中可以省 BODY；只有取到全文后才能确认的不同 URL 副本，只能省后续重复相关性、Stream 与 Runtime 成本，不能算作省掉了正文下载。已知 wrapper 映射复用不代表首次 wrapper 解析免费。

没有改变来源轮询周期、必需频道、正文质量标准、Google 搜索规则/黑名单、浏览器身份或 W1/W2/W3。未删除历史重复发布及既有 Case，也没有全量历史指纹回填：旧的无 alias、无可索引相同 URL、且只有错误旧指纹的跨 URL 记录仍可能需要后续必要处理后才能接回。不同正文、语言、独立报道和私有访问 scope 不因正文 hash 相同而无条件合并。

## 3. 验证与证据

目录：`eval/cross-source-dedup-repair-20261102/`；`baseline.json/preexisting.patch` 保存本轮开始前状态，保留其他会话未完成改动。

### 3.1 定向与集成测试

`tests/test_cross_source_live_dedup.py` 覆盖 API 强身份、语言、跨 URL 纯正文、追踪参数、Google 精确后缀、已知 wrapper、失败后的新 wrapper、原生 FULL 晋级、Finnhub 中间地址、实质更新、旧键接回及并发准备。负例包括不同正文、短模板、独立报道、语言和 private scope。

另有真实 Live→BODY→Delivery→物化 Stream→RuntimeCoordinator 接纳测试：重复来源及 Journal 重开后仍只有一个 CASE 任务，不运行 Agent/LLM。多 ticker 测试验证一个 BODY owner、一次获取、两个 ticker 各自发布。并发发布测试在事务上下文内禁止 Native 文件读取，验证冲突仅重做本地发布且没有新增下载。

最终集成回归 **281 passed、1 deselected，433.20 秒**；随后新增 Runtime 重开与多 ticker 两项分别通过，合计 **283 项不同测试通过**。新文件的 20 项全部通过（前 18 项包含在整组回归中，最后 2 项单独运行）。XML 分别为 `final-regression.xml`、`runtime-contract.xml`、`ticker-contract.xml`。静态未定义名称/重复定义检查与 `git diff --check` 已通过。测试中的 Windows curl selector 与既有 pytest 警告保留于日志。

当前基线另有独立失败：`test_prepare_failure_does_not_claim_or_mutate_pending_task` 期望 RuntimeJournal 输入准备失败时仍为 PENDING/token=0，实际为 RUNNING。该 Journal 与当前 HEAD 的 AST 一致且本轮未修改；单独复现及证据保存于 `runtime-baseline.*`。没有为了本次测试全绿擅自改变 Runtime 领取/恢复语义。

### 3.2 31 组历史强重复样本重放

从原报告冻结的 31 组、65 次首发获取 47 条已有首发正文，按已知 revision 主键只读取得；没有扫描正文库，也未向新闻站/浏览器/模型发请求。`replay_frozen.py` 在隔离数据库执行新 Live 路径，结果 `frozen-replay.json`：**65 次原发布→31 次发布，每组一次**。

重放保留原 URL、标题与正文；发布时间按组整体平移到当前接纳窗口，适配器/相关性资格及解析、下载返回受控。它验证确定性缺陷修复，不是生产延迟、站点可用性或未来重复率验收；没有抹去历史 34 个额外发布及已建 Case。不同 URL 的部分组仍执行两次正文获取，操作次数逐组保留，未把发布减少误报成下载减少。

## 4. 整体复核与后续发布验收

本地已按报告 P0-A、P0-B、P1 实施，没有接回旧重型接纳事务，也没有实现 P2 模糊/事件去重。相关文件集中在 `message_bus_v2/{deduplication,body_results,adapters,polling,repository,live_repository,live,service}.py` 与 `content_enrichment/service.py`。

如后续发布，应让 Bus、BODY、Delivery 使用同一代码版本，再在当前 Google ticker 的自然新稿中分别核对 canonical 复用、真实 BODY 发送、每 ticker 唯一 Stream/Runtime CASE 和正文晋级；覆盖完整活跃交易日后再判断全 ticker 扩量。不得用本地重放结果宣称生产容量/延迟达标。本轮未执行生产发布或绑定扩量。

## 后续发布记录（2026-11-02）

已随ae5f3f8a整体main发布至包括Bus/BODY/Delivery在内的19个正式角色，维持同一源码版本；配套正式collection迁移成功。Google新增绑定及生产校验的真实边界见[整体发布验收](google-news-rss-whitelist-body-optimization-phase2-20261102.md)。非交易日自然cycles尚未运行，不以本地重放或绑定成功宣称交易日SLA。

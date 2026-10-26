# 有效采集周期与快速交付：实现、发布与实测

依据 `message-pipeline-freshness-and-fast-delivery-optimization-plan-20261026.md` 执行。已完成开发、分阶段生产发布和55分29秒实测。执行和证据链均有改进，但全源接近100%、真实新文章FULL稳定超过80%、普遍10秒交付三项目标尚未整体达标。以下保留发布、压力轮次、真实失败及窗口边界，不用最后小样本替代完整验收。

## 实现范围

1. **采集与慢候选工作解耦**：Google RSS 返回基础列表，不再等整批 wrapper 还原；只有经接纳的候选进入已有 BODY job 前置解析。四个解析执行槽、同链接 single-flight、SQLite 持久映射；验证出版商域，再复用兼容且未到复查期的 FULL。
2. **周期证明**：物理请求 ID、角色、请求/响应时点和缓存头；共享消费者引用原请求，due 纳入 Site cache key。辅助请求不计 listing，迟到任务归原 due，超期返回单列 late recovery。多 RSS URL 独立 FEED 单元，频道和 query 初始相位独立。
3. **日常接纳**：完整输入保留，持久原数组 offset 顺序让新增/变更优先；64MiB 有界解码缓存避免每片展开全文；跨周期只复用业务处理收据，保留每轮实际网络请求，复查到期、输入/版本变化不能跳过。
4. **获取成本**：Barron’s 两个公共 JSON 频道各自获取、批准 US5 出口、持久会话、四个 HTTP 槽；失败一次浏览器回退，失败 HTTP 短冷却。Yahoo/InvestorsHub/Investing 借用现有身份 origin anchor，独立 fetch 读取，不运行每轮整页广告渲染；合同或拒绝仍走原导航恢复。Investing 原始响应与当前原生 DOM 竞速，消除第二次无界 page.content。
5. **写入与占槽**：StorageLane 内 BEGIN 等待100ms，未取得写锁时释放 lane 重新排队；启动/DDL 仍用原等待预算，操作已提交后不重放。记录 BEGIN/事务/commit 分段。捕获进度首/末更新，CDC 忽略 position-only 更新；原始审计和业务投影保留。
6. **快速正文与分发**：两个实际浏览器正文执行槽；无槽时在已有 job 持久续跑，保留同一预算并从浏览器阶段继续。L2 按上游16项上限分批覆盖最多20 ticker；持久 L2 continuation 与 regex 快分发票据分开。新增消息提交后重新读取完整 Stream/member/body，审计 `CONSUMABLE`；这证明 Bus 可读，不宣称实际消费者已经 claim。
7. **源级修复**：代理 client/curl/stdlib/SDK 传输证明；坏 RSS 保存响应哈希与解析位置；Calcalist 每轮刷新 HEAD，tail 独立；IBKR SDK 历史请求时间方向、周期对账、短全局锁、源槽外文章下载、回调时点和即时交接；Worker 安全异常帧及 trace ID 关联。

保留 8核16G 基线、浏览器总8/BODY2初始预算，以及 profile、出口绑定、挑战维护独占。并发按请求单元/成本计算，不固定9 ticker，不为20 ticker建设新身份。

## 验证记录（持续更新）

- 新增首批10个专项通过：47条Google列表不解析、20个解析消费者单飞及重启映射、930旧项加1新项位于头/中/尾、辅助请求不填未来周期、SQLite争锁让出、L2 16+4、浏览器等待释放、完整提交后可读。
- 扩充至15个专项通过，包含未来收据复用/复查边界、L2持久恢复、Barron’s两个频道与浏览器回退、Yahoo待核时条目保留。
- 发布前按测试名去重的最新结果：454项通过、1项跳过、0项尚未修复的失败；原失败与修复后的重跑XML均保留，汇总见 `test-summary.json`。新增专项已扩充至23项，包含SDK实际周期请求/慢ticker隔离/开关到期、publisher别名复用与调度tick的query-plan复用/失效。
- 追加生产单次探针：TrendForce Press批准直连HTTP 200、5条新闻合同通过、404ms，作为轻量主路径，保留原浏览器回退。Google旧默认源串行配置有专门迁移；query plan按真实词表revision和source version失效。
- 同工作区科技/量子/The Fly开发已先形成 `5a0b2c4c`、`e2b308bc`，本次发布包含这些提交；相应源、BODY、预览不发布与配置测试已联动执行。

## 发布与实测

正式窗口为2026-10-26 **北京时间08:22:48—09:18:17**，开关revision5开启、revision6主动关闭，原截止09:22:44，未超过一小时。范围为全部已启用订阅，生产定义77个源、248个订阅；实际周期账本覆盖70个有应执行单元的源。没有新建试验业务订阅或发布合成新闻。

- 原始计划实现：`85a97829`，包含其它会话已提交的科技/量子/硬件/The Fly项目；未覆盖其词表/订阅等工作。
- 稳态配置检查和Press选择修复：`adce8818`。
- capture候选优先级、快发布与周期证明：`ffbe2efb`。
- 并发fetch文档复用/idle冻结、调度短检查优先：`dfdd0510`。
- 配置准入预算与规范URL：`e52c947c`。
- Barron's公开列表与付费正文认证分离：`c9dd4762`。

最终实际组件：Bus=`dfdd0510`，BODY/API=`ffbe2efb`，Site Access=`c9dd4762`。按各组件真实变更构建、发布；四者实际文件哈希与预期提交一致，均running，API/Site健康，重启计数0。Supervisor没有重启，identity revision集合未改变，profile/生产数据/检查点保留。Bus新技术优先级表是增量建表，CDC更新迁移在首次发布前完成。

现存挑战场景由恢复流程继续管理；不能写成“所有target ID始终不变”：测试内Reuters旧任务被替换，另出现Seeking Alpha/public-markets场景。最终DIGITIMES、Reuters、Seeking Alpha、public-markets四个WAITING_HUMAN场景仍保留；没有通过重置profile或删除挑战页换取成功率。

验收以全部应执行周期及逐篇真实新文章为分母，保留失败、延期、未证明、窗口末在途和长尾。Reuters真实挑战未恢复时保留在总分母；证据补链不等于访问性能提升。没有足够真实新文章时，不宣称≤10秒或正文稳定80%已经达标。

证据目录：`eval/pipeline-freshness-implementation-20261026/`。

## 生产发现与实际修复

| 发现 | 证据与影响 | 本轮处理 |
| --- | --- | --- |
| 每tick重新初始化候选仓库 | 一次约45次提交、经常约1秒，再叠加写lane排队，整体dispatch受阻 | 稳态读取真实配置head和运行状态；指针变化才安装/回滚，心跳同样消除重复DDL |
| 新消息仍排在旧capture之后 | 前几篇新消息接纳前等待80—168秒；列表内新候选排序不足以解决跨列表等待 | 持久capture候选优先级；分发源也识别未见文章；候选片段结束降级，8次优先服务后公平推进旧工作，原始列表不丢弃 |
| 新发布被例行扫描写队列拖住 | BODY实际约1—6秒，正文完成到发布仍有额外等待 | 新正文/相关性决策/真实发布进入短任务优先lane，旧文章扫描不再同优先级霸占writer |
| Press轻量代码没有真正运行 | shared Site recipe把Press选成普通news适配器，仍调用Chrome | 修正选择；生产实际HTTP路径恢复 |
| 多单元/共享证明归属不完整 | producer capture未按精确shared_run_id关联部分消费者；多频道journal用了主单元相位，新共享源初次相位随后漂移 | 精确关联原物理收据、版本验证；每单元真实due相位记录，普通周期推进不生成新段。旧70.6%与新统计不能直接视为纯性能同比 |
| 并发身份仍反复冷导航 | 已有身份允许2并发，但仅1个idle文档；另一路反复创建页面和导航 | idle池跟随既有身份容量、最多2个，未提高身份并发。仅已验证业务页idle冻结脚本，借用前恢复，真实timing已出现thawed=1；维护/挑战页不进入池 |
| 人为3秒准入截断 | 配置CRAWLER允许10秒，程序强行截3秒；资源数秒后释放也被延期到下轮 | CRAWLER遵守既有配置，仍受总预算；BODY保持短等待和持久续跑 |
| InvestorsHub每轮重复重定向 | 同身份fetch原始ticker URL仍发生两次请求 | 按identity revision/出口保存验证过的规范URL；每次仍重新请求、验证ticker/合同。最后探针2次实际直取规范地址 |
| Barron's公共HTTP403后回退被登录门禁堵死 | CRAWLER出现140条AUTH_REQUIRED事件，其中包含压力探针；公开JSON不需要订阅，但seed强制付费认证 | 公开列表CRAWLER无需付费前置认证；仍使用原Chrome身份和出口，BODY付费认证、挑战识别、完整性检查保留；最终双频道6/6真实成功 |

## 周期与交付验收

所有窗口按原due和配置/相位段计算，保留逾期、延期、失败、发布取消和未完成。覆盖率为**全部应执行轮次**，另报settled结果；不以成功轮询耗时代替。共享消费者的多条证明不计成多次物理请求。完整逐必需单元账本见`final-coverage-units.csv`。

| 窗口 | 应执行 | 证明新鲜 | 全轮次覆盖 |
| --- | ---: | ---: | ---: |
| 完整测试，含启动/所有发布/压力 | 20,301 | 17,537 | 86.38% |
| 00:34—00:42 UTC，配置检查修复后 | 2,777 | 2,445 | 88.04% |
| 9 ticker临时30秒阶段，含两个20 ticker压力段 | 7,110 | 5,965 | 83.90% |
| 最后正常周期：01:12—01:18:17 UTC | 2,219 | 2,052 | 92.47% |

完整窗口11轮处于边界待定，settled覆盖86.43%；最后窗口11轮待定。最后窗口Reuters仍为0/144；其余源为2052/2075=98.89%，但这只是明确剔除真实阻塞源后的条件结果，**不是全源达标**。最后窗口仅6分17秒，也不足以宣称长期稳定。

最后窗口各重点源：Barron's113/115（98.26%），InvestorsHub56/56（100%），Investing56/56（100%），Yahoo224/228（98.25%），Reuters0/144。最后两轮不是只看RSS；各HEAD/OTHER_HEAD/QUERY/FEED分开核算。Yahoo另有5条时间待核（MU3、RKLB2），即使TAIL状态显示succeeded也不能说所有时间债务已清；其他7 ticker待核0。TAIL续页和时间补全记录另外保留，不用HEAD/RSS成功掩盖它们。

列表返回→持久capture：全窗口平均2.23秒、P95 6.04秒；最后窗口平均1.04秒、P95 2.31秒、最大6.29秒。最初小窗口平均7.91秒、P95 13.25秒。最后仍有490个PENDING/RUNNING capture、7189个剩余输入，550个旧文章recheck排队；这是保留的真实尾部。CPU压力门禁暂停可选历史/recheck，没有为了清旧任务挤占新正文，也没有把积压抹去。

### 真实新文章

按首次观测、非bootstrap、非既有文章修订筛选，再用最终URL和已持久化的ARTICLE_ONCE logical alias合并。16个新body key中有一对Yahoo/Google已被同一逻辑文章证明关联，故真实文章分母为**15篇**；不能把重复下载算成第二篇新文章。各篇及失败见`window-final-articles.json`、`article-logical-lineage.json`。

- 初次终态FULL为11/15=**73.33%**，超过80%的目标未达标。15篇已有可消费发布证明，其中4篇沿用原系统的非FULL发布语义；它们仍计正文失败，不计FULL成功。
- 完整窗口发布延迟平均76.58秒、P95/最大224.79秒，不能用最后好样本替代全窗口。
- 最后出现的3篇真实Yahoo新文章分别在 **4.30、5.67、6.66秒**取得FULL并可读取Stream；候选识别分别0.55、0.92、0.94秒，正文入队分别0.81、1.19、1.27秒。说明新候选快路径实际生效，尚不是全部来源稳定10秒的证明。
- 仍有44秒的Yahoo案例、63秒的Google案例，以及11.47秒的Investing案例，全部保留；启动阶段173/223秒的大样本也保留。
- 首次FULL时点从body execution/原job终态读取，避免被后续复查覆盖的body cache acquired_at误算。CONSUMABLE是提交后重新读到完整Stream/member/body的证明，不等于消费者已执行研究/交易。

失败四篇的真实路径：Reuters浏览器ACCESS_EXHAUSTED后Reader超时；Google→The Globe and Mail、Google→TIKR，以及HuggingNews各一篇最终empty_extract。Reader的HTTP/Site SUCCESS只说明取到了响应，不能升级为FULL。TIKR还记录HTTP超时、浏览器ERR_BLOCKED_BY_CLIENT、Reader超时；其余两篇仍需取得失败响应正文/质量候选才能确认是出版商模板、访问页还是Reader无正文，当前不能凭2xx归因或放松质量闸门。

## 30秒与20 ticker压力结果

先在INTC/MU的6个绑定验证，再于00:47:01 UTC把三源九ticker共27个绑定临时设30秒；01:05:14 UTC按本次revision和操作原因逐项恢复原60秒，**27/27恢复**，没有覆盖其他操作。并发参数没有按9 ticker固化。

两个短压力段各增加11个真实股票端点（AAPL/MSFT/NVDA/AMD/AMZN/GOOGL/META/TSLA/AVGO/ORCL/CRM），与生产9 ticker共同承载；每段6轮、264个额外采集单元，仅读取不写业务订阅/新闻。每个Barron's股票有两个频道，不能只计ticker数。

| 增量探针 | Barron's | InvestorsHub | Investing |
| --- | --- | --- | --- |
| 第一段 | 126/132成功，末轮6个HTTP403 | 33/66成功，33准入延期 | 43/66成功，22延期、1恢复待定 |
| 第二段（池/冻结后） | 117/132成功，12认证拒绝、3服务不可用 | 31/66成功，35延期 | 12/66成功，48恢复待定、6延期 |

这两段暴露真实容量和访问恢复问题，**不能宣称20 ticker/30秒通过**。第二段与第一段的访问状态/公共HTTP退避不同，不作单一变量性能同比。随后删除隐藏准入截断、修复规范URL和Barron's认证门禁；最后3轮小探针12/12成功（Barron's6、IHub3、Investing3），但不是重新跑完20 ticker稳定压力验收。

最终小探针暖执行均值约：Barron's2.27秒/频道、IHub3.09秒/股票、Investing1.76秒/股票。按20 ticker/30秒粗算平均占槽需求分别约3.03、2.06、1.17；现有每身份2槽下，Barron's浏览器回退与IHub没有可靠余量。Barron's公共HTTP本来低成本，但403后的实际回退成本必须纳入；Investing的恢复/验证待定也不能被容量算式忽略。这里是占槽估算，不把等待IO误称为CPU成本，也不把提高并发作为已完成解法。

## 资源和剩余工作

8核16G实机，Site执行预算8、BODY2，未提高身份并发或修改profile；没有旧4核CPU硬限（NanoCPUs=0）。00:48:28—01:18:20 host CPU平均busy约78.6%，其它研究/调度/投影持续占用。同期Supervisor约1.04核、研究scheduler约1.08核、Codex Worker约0.59核、projector约0.67核；Bus当前容器生命周期均值约1.16核，Site约0.98核，BODY同期约0.04核。不同容器起止时间已标注，不冒充严格前后A/B。

结束附近Site约3940MiB、Supervisor约4100MiB、Bus约678MiB、BODY约142MiB；关闭后的最终健康快照可用内存约5.6GiB。资源仍有明显争用，不能依据8核升级就继续盲目扩大并发。

剩余优先项按当前实测收敛：

1. **P0真实访问恢复**：Reuters人工挑战仍阻塞；按现有恢复流程取得实际搜索列表和FULL证明后才算恢复，保留源28个必需query的失败分母。另有DIGITIMES/Seeking Alpha/public-markets待人工场景，不能用清profile解决。
2. **P0浏览器回退成本**：继续对`runtime.py`的Barron's冷导航→原生JSON、IHub规范URL→实际传输/序列化/Worker占槽分段取证。最终暖占槽算式已经说明2槽的边界；先验证更轻的合法同源列表路径，再据实测CPU决定容量，而不是直接把identity cap调到20。
3. **P0日常旧输入开销**：`polling.py`/`distribution_repository.py`旧列表处理、550个recheck和7189个剩余输入尚未全消。候选优先级已使最新真实文章不再等待这批工作，但仍需以每轮变动量和短事务计数继续压低旧扫描成本；不能通过不保存原列表或停止新请求“优化”统计。
4. **P0 Yahoo时间债务**：MU/RKLB共5条仍待核；沿HEAD→TAIL的checkpoint、预算和准入续跑逐条完成证明，分别验收消息发现与可发布时点。
5. **P1四篇失败**：保留上述publisher和终态路径，补失败响应/提取候选证据后针对性修模板或访问路径；当前不把Reader成功响应当正文成功，不把失败样本移出分母。
6. **验收补足**：下一个正常交易窗口用全部应执行轮次和新文章验证；本轮没有持续负载下的全20 ticker复验，不能给出稳定80%/全源99%/普遍10秒结论。

## 测试与可重放证据

最新按测试名去重：469 passed、1 skipped、0 unresolved failures。重要代码改动均追加`changelog`；原失败XML和修复重跑保留。包含配置激活/回滚、开关、重启恢复、ARTICLE_ONCE、未变收据、SQLite争锁、公平调度、20 ticker分批、真实物理请求/共享关联、单元相位、2个并发文档冻结/恢复、既有10秒Crawler准入，以及公开列表/付费正文认证隔离。

证据入口：`test-summary.json`、`final-metrics.json`、`final-coverage-units.csv`、`window-final-articles.json`、`article-logical-lineage.json`、`new-article-failures.json`、`resource-summary.json`、`final-verification.json`、`periods-restored.json`、`control-closed.json`。原始大窗口及两段探针JSONL保留在同目录；采样器/汇总脚本一起保存。所有临时控制已关闭、周期恢复，下一交易日仍用原日历、去重、检查点及ARTICLE_ONCE。

固定窗口无正文元数据已压缩归档为`window-final.metadata.json.gz`，传输阶段证据为`site-window.metadata.json.gz`，两段全部压力轮次另存`stress-results.csv`，避免只保存成功汇总。

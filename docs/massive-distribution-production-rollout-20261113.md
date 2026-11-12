# Massive Distribution 独立正则生产上线回执 · 2026-11-13

工程改造、9套词库、5源正式启用和45个真实绑定已完成。业务验收逐源记录：正文访问失败或尚未出现真实相关稿件的来源，不能据此宣称全链路通过。

## 变更与隔离

- 在既有版本化统一监测词中增加 `massive_l2`，复用原生正则匹配器，新增薄入口 `message_bus_v2/massive_relevance.py`。普通 `l2`、L1、definition、X账号不变；九ticker逐字段生产回读通过。
- Massive生产缺少专用配置即 `CONFIG_INCOMPLETE`，不回落普通L2；直接判定、L2_DUE恢复、旧批处理续跑及缓存都遵守regex-only，Decision Model不能兜底。
- 保留原Collector→Live/BODY→去重→Bus链路、完整正文门槛及交易日接纳规则。测试/正式文章身份继续隔离，没有把测试留存消息批量提升为正式消息。
- 统一监测词页面增加“Massive Distribution 监测词”，两栏独立语言、规则/JSON模式及错误状态，共用现有草稿、检查、保存与CAS。旧客户端省略新字段时保留已保存Massive规则。
- 五源从test转production，清空虚拟test_tickers，关闭JEV，每源订阅九ticker。IONQ未新增绑定，Bloomberg仍为test。
- 目录五源去除测试说明；GlobeNewswire的HTTP与RSS列为并行primary；GLOBAL和真实订阅分别处理。BODY策略没有因本次上线改成浏览器：HTTP/Reader失败仍保留NO_PUBLIC_BODY。

## 入选词库

选择口径为两轮全部候选的 `(调试P+调试R+验证P+验证R)/4` 最高值；没有因第二轮更简洁就覆盖成绩较高的旧版，也没有新增调参或改GOLD。

|Ticker|入选版|调试P/R|验证P/R|生产revision|
|---|---|---|---|---:|
|INTC|首轮attempt-02-overfit，附词边界勘误|86.67/86.67%|89.66/86.67%|6|
|MU|首轮attempt-03-overfit|86.96/100%|90.91/100%|8|
|BE|首轮attempt-02-overfit|92.59/92.59%|68.75/91.67%|5|
|COHR|首轮attempt-02|100/100%|61.54/80%|4|
|NBIS|首轮attempt-03-overfit|90.91/86.96%|100/72.22%|4|
|CEG|简化第二轮attempt-01|100/95%|86.67/86.67%|4|
|RKLB|首轮attempt-02-overfit|96.15/100%|93.10/100%|5|
|SNOW|首轮attempt-02-overfit|83.33/90.91%|86.67/92.86%|4|
|ONDS|首轮attempt-02-overfit|92.86/81.25%|93.75/93.75%|6|

INTC只修正 `intel_manufacturing_fact` 一处分支的 `Intel` 词边界，防止前缀匹配Intelligence。所有九套新入口对冻结调试/验证各50条、共900条逐条回放，与历史保存预测一致，包括该勘误。旧候选哈希、归一化部署哈希和差异清单分别保存在terms.json、historical-replay.json。

这些P/R来自旧正则召回且阳性富集的样本；BE、COHR、NBIS仍未全部达到原80%门槛。部署“最优版”不意味着三者已通过指标验收，也不代表自然全市场P/R。

## 发布与验证

代码在隔离工作树 `C:/DoxMassive`、`codex/massive-independent-regex` 完成，基于已包含目录整理的9b055706e，保留原工作区未提交内容。通过既有 `/var/lib/doxagent/releases` 锁、角色镜像overlay、健康检查和回滚所有者发布，没有全量覆盖远端旧checkout或重建无关角色。

- 初次发布：API、Web、Collector、Bus；API schema枚举修正只补发API；真实采集去重修正及恢复边界修正只补发Bus；初始读配置前清空草稿的问题修复后只补发Web。
- 发布过程中发现两项真实缺口并修复：消费者schema旧枚举不接受massive_distribution；原RawMessageInput在已发布候选别名比较时缺source_id，现注入已知绑定来源上下文。补充回归均通过。
- 词库、45绑定、模型关闭、9×5消费者schema及目录读模型回读通过；普通词库字段逐字段不变。
- 最终相关后端组合：72 passed / 1 skipped；另有最新合并后的目录测试10项通过。skip为未复制到隔离工作树的可选4078条历史研究fixture。
- 前端构建通过，浏览器共11项检查通过，布局覆盖1262px与1559px，并包含新增刷新/412/无效JSON检查，覆盖原监测词与Massive独立编辑保存；另2项浏览器原生API/临时Bus数据库联调、1项监测词unit通过；生产index引用及全部JS资源HTTP读取与部署文件SHA-256核对通过，实际bundle含新增编辑区。

### 生产观察

正式启用时间：北京时间2026-11-13 04:00:46（UTC 2026-11-12 20:00:46）。窗口包含上线初期API和去重修复的发布时间，不能当作稳定态延迟基准。

观察截止北京时间2026-11-13 05:01:43（UTC 21:01:43），距离启动约61分钟。

|来源|新增BODY记录|FULL|PARTIAL/不可用|正则阴性|正式投递|待处理|状态|
|---|---:|---:|---:|---:|---:|---:|---|
|businesswire_massive|68|0|68|0|0|8|正文失败，未验收|
|prnewswire_massive|43|24|19|195|3|20|正式闭环存在；HTTP列表降级/RSS备用，另有1误召|
|globenewswire_massive|24|24|0|216|0|0|FULL可用；尚无正式正例|
|access_newswire_massive|7|7|0|28|0|35|FULL可用；尚无正式正例|
|tmx_newsfile_massive|5|0|5|0|0|0|正文失败，未验收|

BODY按新增文章记录计；判定/正式投递按文章×ticker计。首小时末63条PENDING来自异步处理快照，不擅自归为阴性。NO_PUBLIC_BODY（BW604、PRN169、TMX45）不计入正则阴性或漏召。PRN的HTTP列表失败由RSS备用承接；其他来源的列表单位持续成功，GNW为HTTP/RSS两路并行。

固定首小时截止前已建立的投递再于北京时间05:03:04跟进：PRN已收敛为213阴性、171正文不可用、3发布；GNW216阴性；TMX45正文不可用；BW仍有8条、ACCESS仍有30条PENDING。抽查待处理记录为BODY READY/已有result且last_error为空，属于本地分发等待；不将其归入阴性，不宣称该两源已完成正式正例验收，也未在上线任务中追加调度性能改造。见cohort-followup.json、pending-probe.json。

四项生产隔离检查均为0：模型分数、测试文章进入正式流、非FULL正文发布、无正则命中的发布。45/45绑定启用，完整正文门槛开启，9/9词库匹配，IONQ绑定0、Bloomberg仍test。

PR Newswire已出现的新鲜Positron AI稿件：INTC命中processor_technology_fact、MU命中memory_business_fact。稿件虽以获奖为标题，正文有下一代定制芯片、生产计划、多TB内存系统及降低HBM/先进封装依赖的具体事实；按现行GOLD业务圈层与体裁规则，两项放行合理。稿件原始发布时间为UTC 19:45，正式采集20:01:09，属于启用时重新发现的当前交易日新鲜稿，而非启用后才发布的新稿；没有把旧test_publications搬入生产。两条都有raw、standard、唯一stream_member及下游admission_plan；inbox均SUCCEEDED。不是把“获奖”或泛AI词本身当作相关证据。

新增放行质量问题：INTC还放行了Regal Intel的罕见病研究资助稿。完整正文确认其是非营利医学研究组织，没有Intel公司或CPU/半导体业务事实，属于intel_subject组的同名实体误召。该稿在UTC 20:43发布、20:45:25正式采集，也完成下游接纳。首批3个放行文章×ticker人工复核为2相关、1不相关；这暴露了明确词库缺口，不能宣称质量验收通过。该问题不同于已修复的Intelligence前缀勘误；本轮保留批准的最优版本，不追加针对单一机构的黑名单或重新利用旧验证集调词。后续需以新的自然流量样本验证实体消歧。见positive-review-summary.json。

Business Wire与TMX的有界探测：既有HTTP路径分别超时/401与202空正文；已有Site Access受管浏览器分别返回net::ERR_HTTP与render_required，没有可用正文。没有创建新Identity、修改网站访问全局策略或降低FULL门槛。BW/TMX因此保留正文与全链路未验收；后续若继续修复，应在既有Site Access中解决访问能力，再观察新稿，不重灌测试历史。

有限正文抽查还覆盖6个正则阴性（见negative-review.json）：合同管理、IT服务商荣誉、医疗交易认证、软件企业会计诉讼、电池储能企业诉讼及水务企业公益资助。按文章×目标ticker复核维持阴性；其中储能/data center背景与NBIS的边界条目保留观察，不为个别样本继续修改已选规则。这只是有界质检，不是自然流量P/R估计。

## 证据与后续维护

公开回执目录：`eval/massive_rollout_20261113/`。核心文件为terms.json、historical-replay.json、activation.json、config-verification.json、catalog-verification.json、web-verification.json、audit-final.json和downstream-verification.json。界面截图在 `frontend/v2/test-results/massive-1262.png`、`massive-1559.png`。

服务器完整配置备份只保存在 `/var/lib/doxagent/releases/massive-regex-3f09def6d/activation.before.private.json`，0600权限；未写入Git。初次release、API补发、去重补发、恢复补发和Web补发各保留独立release/state/compose回执，部署目录0700、私有快照0600。不要把这些私有备份作为普通报告附件。

后续改Massive词库只改massive_l2并使用专用入口回放，不改普通L2。若发现串用、误升测试历史、重复发布或正文门槛问题，先停受影响Massive源/绑定；词库回滚使用当前revision合并旧massive_l2，保留并发变更的其他字段。代码回滚需保留新字段读取兼容。state/compose.before只供追溯，不能整份覆盖当前部署；期间已有并行API发布，回退应以当前compose为底只改目标角色。已发生正式投递保留审计，不倒退offset或批量删除。

本功能已合入并推送origin/main（首次集成8ce986b3b）；交付时又合并保留并行Bloomberg正文改动（08949ceb8），补跑Massive/Bloomberg共同正文路径回归30通过、1可选fixture跳过。实现；不依赖临时镜像独自留存代码。远端当前角色使用窄overlay镜像，后续常规构建应以已更新的main为源码；不从旧远端checkout直接重建覆盖本功能。原DoxAgent工作区的未提交内容未改动。

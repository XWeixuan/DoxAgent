# Reuters / Bloomberg Solverify 正式接入与验收（2026-11-18）

## 实施边界

复用现有恢复Coordinator、Profile single-writer、原页所有权、付费限额、fallback和真实业务确认。保留旧DataDome类/账表接口，不新增组件或数据表；在既有job JSON中记录challenge_family和分阶段结果。只发布Site Access及宿主机Squid配置，保留Supervisor、Chrome实例和Profile。用户追加的Bloomberg现有失败在接入完成后单独诊断，不将验证码配置等同于业务恢复。

## 正式路径

- Reuters：私有配置provider_by_site.reuters=solverify，覆盖现有Main/Fallback及候选；显式identity配置优先，已提交任务始终使用其冻结Provider。2Captcha密钥/适配器保留，可将新任务切回2captcha；不在一个事故内自动重复购买另一供应商。
- DataDome：真实slider/RETRY继续原流程；自动设备检查先保留60秒免费自解窗口，停滞后Solverify才可使用当前已加载interstitial。提交前验证完整cid/initialCid/hash，拒绝旧网络fixture那种空参数URL，且验证在付费reservation前完成。
- Bloomberg：以实际注册的bloomberg_latest_lab / publisher:bloomberg_latest_lab / bloomberg-latest-lab-1为准，不把generic站点全局开启。启用该Site既有recovery并配置对应Solverify和同出口受认证代理。
- PerimeterX：从原页window._pxAppId或已加载captcha脚本识别App ID，要求实际可见PX容器，提交type=perimeterx、websiteKey、真实Chrome UA与同出口proxy。缺App ID保留原页并记录INPUT_CONFIGURATION，不购买。
- PX返回Cookie只接受_px命名空间/pxcts并校验原Chrome UA；限定.bloomberg.com/Path=/，保留账号、订阅、同意管理等其他Cookie。写后通过原Chrome读取核对；Bloomberg原页如果已跳/tosv2.html，返回原始文章URL，而非不断刷新验证页。原始target/instance和Profile writer检查全部沿用。
- Bloomberg正文可见快照遇到PX覆盖层时保留完整验证文档，不把背后的正文当FULL。站点实际正文与列表的HTTP会话仍分别治理。

## 诊断和资源

沿用付费应用时一次5秒有界被动CDP观察、最多6个文档/搜索响应、24KiB单job证据限制。记录目标Cookie的事故内HMAC、写入后/首次请求/响应及必要的阶段变化，不存原Cookie、授权头或完整验证码URL，不持续截图/抓包。

failure_stage分别支持PROXY_NETWORK、PROVIDER_DECODE、RESULT_APPLICATION、SITE_ACCEPTANCE；API传输、账号/订阅、配置缺失、证据不足另记。通用ERROR_TASK_FAILED只有PROVIDER_UNDETERMINED，不能硬分类为答案不对或网络不通。失败结果费用0也必须持久化；APPLIED仍不是原页通关或真实CRAWLER/BODY恢复。场景分类后来变UNKNOWN不改变已付费任务family/provider。

Squid保留精确来源158.180.12.56/32和旧2Captcha来源、认证、CONNECT/443、私网目标拒绝及never_direct；只加入Bloomberg与HUMAN官方依赖域.perimeterx.net/.px-cdn.net/.px-cloud.net，TLS端到端不MITM。

## 本轮有界连通性复核

手工有界网络复核没有新增解码任务、没有制造Challenge。两笔旧任务当前getTaskResult均ERROR_TASK_NOT_FOUND；不能覆盖此前保存的ERROR_TASK_FAILED/cost=0终态，也不能据此反推旧失败原因。API/profile可达、余额仍$0.05。API当前DNS为54.36.246.192，与探测所得158.180.12.56不同，不把API地址冒充worker来源。

既有网络fixture缺少真实动态验证码参数已确认，不满足当前DataDome契约；正式代码现在本地拦截。宿主机公网端口回环预检ConnectTimeout，先前容器内认证代理预检成功；该回环并非允许来源的外部worker，故不将其认定为平台连接失败。现有证据仍不能唯一区分平台未发连接、来源变化、云入站过滤及无效输入导致提前退出。后续自然真实任务依据明确Provider网络信号和已有Squid/原页证据分层，不扩大公网白名单或重复买无效样本。

## 配置及代码验收

正式接入源码 f511e939c，具体生产配置、回归和原Chrome保留证明见下节。初始网络复核没有真实Challenge，之后正常采集自然触发的一笔PX失败另列；从未将接口接入当作端到端解码成功。

官方契约：[DataDome](https://solverify.net/docs/datadome)、[PerimeterX](https://solverify.net/docs/perimeterx)、[结果和费用](https://solverify.net/docs/get-task-result)、[HUMAN依赖域名](https://docs.humansecurity.com/applications/about-enforcers)。

## 生产配置验收与 Bloomberg 现有故障修复

Site Access 已发布 f511e939c，Reuters/Bloomberg 实际 provider_for 均为 solverify，原2Captcha key及已提交job provider冻结保留。Bloomberg Site revision4启用恢复；四个Chrome instance/PID与发布前完全一致。代理只热加载配置，原绑定不变；回归241通过。接入配置阶段未主动购买测试任务；后续自然PX任务的实际失败见下文，不宣称平台已成功解码。

Bloomberg 额外故障与验证码无关：WIRE:realtime 最新持续失败为 ValueError:bloomberg_realtime_pending_overflow，checkpoint 实存703条pending、seen仅14条，包含11月13日旧稿；每轮先追加pending，再在>128时抛错，因snapshot_valid已设true又将过大pending提交，下轮永远无法走到metadata出队。WIRE:latest仍以30秒正常抓取，20:21–20:23 UTC近期十篇BODY均FULL。单次受限元数据核验返回200/两条fulfilled，不能归因于此刻网络或PX。

修复：pending按既有30分钟新闻准入窗口自然过期；真正的新ID优先，既存缺失ID走有序重试；超过128条保留最新128条、明确PARTIAL/overflow计数，同时继续执行最多10条元数据请求并提交成功进度，绝不先抛错锁死整个管线。不清数据库/身份，不改变10秒Realtime+30秒Latest节奏，不新增请求批次或后台组件。过期/overflow计数复用本轮acquisition_metadata；不伪装旧消息为新消息。定向回归38通过，覆盖703条旧积压恢复、容量溢出仍出队和新ID不被重复缺失项抢占。

后续自然运行发现第二层故障：重启HTTP会话后Artemis warm `/latest`403；有界比对direct sentry412、原DE代理sentry200但实际HTML/PX，而非JSON授权。Latest JSON仍正常。Realtime失败时现在复用既有poll_latest的正常直连路径、解析与物理请求证明；保持10秒单元调度和10秒共享cache槽，5分钟内不重复撞失败Artemis入口，届时正常单元才重新尝试。无新组件/定时器/浏览器抓列表。HTTP失败与Chrome挑战不混淆；若Latest也失败仍诚实返回失败。新增回归证明blocked入口只试一次、后续真实列表不重复发布、成功证明只来自实际Latest请求，共39通过。

注意：接入后20:23:43 UTC正常正文采集自然触发Bloomberg PX Solverify任务，返回ERROR_TASK_FAILED/cost0、PROVIDER_UNDETERMINED，没有ready或Cookie应用，不能宣称解码通过。20:23后正文存在实际PX阻塞，先前十篇FULL并不代表后续仍健康；最终验收必须分列列表恢复与该正文限制。不为此次失败主动追加购买或持续守候。

最终补充对照：20:33运行中列表会话403，但同出口新建无Cookie curl会话对30秒和10秒两个cache槽均200/JSON/age0。为避免公共列表被其他入口的会话/错误响应Cookie污染，Latest改为独立client_scope=bloomberg-public-latest、discard_cookies=True，复用已在by-ID元数据路径使用的机制，不触碰Chrome Cookie。该对照支持HTTP会话差异是内部可优化因素，未读取或归因某一具体Cookie；不夸大为已经证明所有403均由Cookie引起。


发布留痕：Site Access f511e939c健康；Collector先发布72f76329a/157e89f06，在自然回执中继续定位Artemis阻断与公共HTTP会话差异，最终目标源码b938abe8f。宿主发布脚本只缺少仓库已提交的compile_w3_imports.py构建清单，已同步该单行差异；首次b938构建因512MiB写入保留线自动取消，未替换运行容器。按既有授权清理闲置BuildKit缓存、压缩journal至128MiB并删除已轮转旧syslog，保留当前日志、业务库、Profile与发布备份。不得将构建失败期间旧版本统计混入最终版本验收。

## 最终自然业务验收（UTC 2026-11-17 20:42:29 / 北京时间11-18 04:42:29）

- Site Access f511e939c、Collector b938abe8f已健康；Collector两个修改文件与该精确Git提交SHA256一致。Chrome Supervisor和代理容器未重建；四个Chrome实例/PID保留，2Captcha回退仍在。源码及changelog已推送origin/main。
- 自最终Collector20:41:14启动，WIRE:realtime 5个独立自然物理周期CAPTURED、WIRE:latest3个周期CAPTURED，合计80个ticker回执（10ticker共享8次物理周期），没有失败。20个活动unit投影均succeeded。不能把80回执说成80次网站访问。
- 实时原生Artemis路径已自行重新可用，最新物理证明为HTTP200/age10s，Latest200/age0s；因此并非只凭fallback的存在判成功。最近耗时Realtime368ms、Latest440ms。原pending704条降为0、seen40，未手工修改checkpoint。该窗口约74秒，仅证明短窗恢复，不作长期稳定性保证。
- 20:23:43自然PX任务dd_5628840446184acea75437308dbd09ad：FAILED/ERROR_TASK_FAILED/cost0、PROVIDER_UNDETERMINED，无Cookie应用、无恢复证明。该窗口Squid只有内部预检的3个成功CONNECT，未观察到158.180.12.56的外部worker请求；仍不能区分云过滤/worker地址变化/Provider未连代理而提前失败，不能归罪解码答案。保留原Challenge现场供人工。
- 20:42:25自然恢复流程新建第二笔PX任务dd_e05af593fcb741e792ad46a1b6957268，此快照PENDING/cost未知。没有主动提交测试、没有额外Provider API调用或守候新Challenge。正文在20:23之前16篇FULL，之后存在PX阻塞；截至此快照没有新的FULL证明，正文**尚未完全恢复**。
- 下一步：由平台提供这两笔自然task的失败环节及稳定worker源IP后再精确校验18791白名单；不盲目扩大公网来源、不重复购买无效测试。当前Bloomberg可用人工Open Original处理保留的PX现场；正常恢复流程仍自然运行。

主要证据：eval/solverify_dual_family_20261118/core-acceptance.json、configuration.json、proxy-acceptance.json、proxy-logs-summary.json、bloomberg-checkpoint.json、bloomberg-listing-control.json、acceptance-isolated.json、collector-actual-hashes.json及两组回归XML。中间版本acceptance-final.json有旧失败，最终以acceptance-isolated.json窗口为准，不覆盖历史。

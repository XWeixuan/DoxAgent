# MAIN重部署后消息源故障：逐项根因与修复

日期：2026-10-15，北京时间。基线MAIN `0d1791ef`，生产Site Access仍为 `main-618fd9c5`；已合入的 `f1957b71` 浏览器修复尚未在该容器应用。不是“Git HEAD同步就等于所有服务已更新”。

## 1. TrendForce News / Press Releases：持久化Profile的临时锁跨容器残留

两入口最后成功同为UTC10/14 17:19，此后每轮在launch_persistent_context处快速失败。生产Profile的SingletonLock指向旧容器 `bad9a6d56fc5`，当前容器hostname已为 `446bab874aa2`；Socket指向旧/tmp。持久化卷保留这些链接，容器hostname和/tmp却已更换。

Chromium据外部hostname将其视为其他机器占用，拒绝启动。Managed Pool已有flock单写锁，但取得后直接launch，没有External Chrome Supervisor已有的启动前临时链接恢复。因此重新部署会反复暴露，人工删一次不是长期修复。

修复：Managed在成功取得原Profile flock后，校验三个Singleton路径全为缺失或symlink，再仅unlink这些临时链接；普通文件/目录拒绝处理，不递归删除、不杀Chrome、不删除Cookie/Local State。当前owner/不确定关闭持有flock时，第二writer仍不能进入。Chrome Supervisor不改、不重启。

## 2. GlobeNewswire Search / Semiconductors RSS：同一Managed Profile故障的双入口影响

`globenewswire-1`同样留有旧hostname锁。Search直接依赖浏览器；RSS正常HTTP403后已正确调用同官方feed的浏览器后备，但这个后备在启动阶段失败。因此不是RSS parser失效，也不是要登录。

复用上面的Managed修复，保留原60秒、XML校验、30秒总预算及源级403例外，不改站点或Profile映射。

## 3. Reuters：人工验证确实成功，但之后再次挑战；专用recipe未交接原页

UTC21:15:46有PROFILE_VERIFIED/public_access_ready，21:16仍有真实browser成功；21:17搜索再次返回401/challenge，后续恢复探测也401。不能说用户未处理或只是旧状态：已完成过，随后实际又发生。

代码缺口是Reuters recipe对HTTP拒绝抛带response_body的异常，直接跳到finally关闭页面，绕过普通External页面已有的原挑战页交接。遇到200挑战文档后selector等待失败时也会丢失原页。维护只能重新创建页面，无法处理刚才被判定的现场。

修复：Reuters异常路径观察当前实际挑战文档，与普通页面共用retain helper；保留原target、卸载导航gate、返回原HTTP状态/挑战令牌及provenance，由现有Service暂停整个共享Identity，停止fallback。非挑战错误仍原样传播，不伪造HTTP200、不自动过验证码、不追加CDP owner。

仍可能发生真实站点challenge；修复的是原页/状态交接缺口，不承诺消除网站风控。Reuters HTTP_PUBLIC正文仍曾401，但HTTP/browser健康记录独立，不把HTTP拒绝写成浏览器认证失效。

## 4. Investing / Reuters公开访问验证：UNKNOWN与VALID语义混淆

公开站点验证成功返回public_access_ready，但认证状态可以仍是UNKNOWN，因为没有声称订阅身份。原challenge_verified只有AuthState.VALID才置真，bridge也只接受VALID；UI不检查closed=False便关闭自己的Viewer/清空session。于是“挑战已人工通过”可能仍无法完成维护收尾。

修复：public_access_ready也允许标记原挑战验证完成、正常释放维护与门控；保留UNKNOWN，不伪造订阅登录。bridge接受此显式成功理由；UI在closed=False时保留窗口/会话，在公开验证成功时显示成功。

现场Investing主组合门控仍留自10/9，尚无本次PROFILE_VERIFIED；不以人工已经登录这句话盲清数据库。发布后用现有owner管理入口核实实际ticker页面，只有实际成功才解除门控；仍挑战则保留原页交给人工。

## 5. 验证及边界

- 新回归覆盖：Managed取得flock后清理、普通文件拒绝且不部分删除、Cookie保留、writer排他、Reuters带拒绝文档/无文档异常两条原页保留、public challenge完成且不冒充会员、bridge公开验证、UI失败保留窗口。
- Windows两项symlink测试因账户权限跳过；另在无生产卷、无网络的隔离Linux容器以真实headed Chromium复现：旧锁启动TargetClosedError/Singleton错误，清链接后正常启动、DOM读取和正常关闭。初次headless-shell不走同一Singleton路径，未复现；改为与生产一致headed模式后才获得上述证据。
- 隔离脚本：`eval/message_source_repair_20261015/managed_lock_smoke.py`；只使用TemporaryDirectory，未使用任何生产Profile、网站账户或Cookie。
- Profile锁冲突分类为RUNTIME_UNAVAILABLE/browser_profile_busy，不再统称网络Error。
- BE官方IR按用户要求不动。IBKR最近已随Gateway恢复出现成功，本轮不修改交易/API服务。TikHub读取断连和Barron's单次ConnectTimeout另按最新自然状态区分，不把每次偶发错误都当parser缺陷。
- 仅提交本轮文件，不包括其他人的Document2 prompt/其他审计文件。正式Dockerfile.v2及Site Access Dockerfile构建，受管三份Compose只更新Site Access；宿主机GUI/bridge安装原路径。原Chrome Supervisor/身份进程保持，其他容器不顺带重建。
- 生产验收结果另存本次release回执；不以离线测试或镜像健康宣称网站实际恢复。

Chromium机制依据：[process_singleton_posix.cc](https://chromium.googlesource.com/chromium/src/+/HEAD/chrome/browser/process_singleton_posix.cc)。只使用官方实现核对hostname/进程锁行为，没有采用stealth、随机指纹或验证码绕过。

## 6. 首次正式发布暴露的镜像权限问题

`main-02f85ad0`首次切换后，UID10001在导入client.py时PermissionError，已立即仅回滚Site Access与本轮四个发布环境值，Chrome Supervisor不变。该错误不在之前沿旧系统镜像注入runtime.py的隔离A/B覆盖范围内；后者只证明锁机制，不等于完整新镜像已可用。

实测新镜像client.py为root:root/0640，runtime.py为0644。Git仅记录普通/可执行模式，不记录这些细分读权限；Docker COPY保留检出目录的权限，不同检出/运维umask可产生不可由服务UID读取的源码。于是同一commit也可能因宿主检出权限产生功能不同的镜像。

修复进入正式Dockerfile.v2：只对构建内应用src/prompts/api_contract树补a+rX及项目元数据可读，不触碰生产卷、Profile、配置secret。Site Access Dockerfile增加构建期setpriv为UID10001实际导入CLI，失败即不能完成镜像构建。再次发布使用新的MAIN标签和独立r2回执，不覆盖首轮失败/回滚材料。

本地定向回归最终80 passed、3 skipped，Ruff/diff检查通过。发布健康检查不再替代非root导入及实际消息采集验收。

## 7. 生产复验发现的第二层External故障与延迟challenge

正式r2镜像main-483d4771成功发布，仅Site Access容器更换，Supervisor与其他容器ID不变。TrendForce两个入口、GlobeNewswire两个入口恢复自然轮询；次日UTC05:31—05:32的独立正文canary：Barron's两篇FULL（3859/5874字符）、TrendForce News两篇FULL（4342/3134）、Press两篇FULL（2550/6082）、Globe RSS两篇FULL（6207/6856）。DIGITIMES列表200/11条，两篇正文均subscription_required，不把列表可用冒充付费正文已可用。

Investing/InvestorsHub还共同依赖public-markets-nl。旧Chrome主PID164运行约10.5小时、累计CPU约6小时，HTTP调试元数据正常且仅一个about:blank页面，Playwright却在WebSocket connected后connect_over_cdp超时。说明不是调试端口拒绝、tab堆积或网站登录请求失败，而是实例CDP接入/初始化不再可用。没有足够证据断言Chrome内部具体哪个函数产生忙循环。

通过现有owner的identity:drain仅对该Identity执行SIGTERM并等正常退出，返回stopped=true；未强杀或复制/清空Profile。原Profile重新启动后CPU降到正常范围，CDP接入成功，InvestorsHub再次产生实际文章，Investing推进至article-item等待超时。因此当前Investing至少是复合问题，不能用重启成功宣称已恢复。

补修：connect_over_cdp故障单独归为RUNTIME_UNAVAILABLE/browser_cdp_attach_failed，不再误记为网站网络并立即重复访问；所有External页面/recipe异常在关闭前有限时观察实际文档，若是延迟出现的challenge则保留原页交人工，否则原异常继续传播并记录无秘密的文档类型/字节数诊断。该路径统一替代Reuters局部catch，覆盖Investing初始commit后出现challenge的漏洞。不增加自动强杀或跨Identity重启。

Reuters真实搜索probe仍401/challenge，原页已保留。Registry以revision9→10审计提交取消长期401、不能使用浏览器Cookie的HTTP_PUBLIC正文后备，仅保留browser/reader，减少无效拒绝流量；不是盲清认证状态。此为生产Registry配置，与源码commit区分记录。

## 8. 共享Identity排队压力：内容等待被当作网络重试

r3发布后Investing实际Intel列表已200且等到article-item，MU也自然成功，RKLB原vector-acquisition路径现场返回正确RKLB News/10条，不存在静态ticker映射错误。后续owner诊断却显示public-markets-nl被InvestorsHub BODY的locator等待连续占用约32秒；CRAWLER短队列及低优先级PROBE反复site_queue_timeout。此时Chrome主PID CPU正常、没有stalled任务，不能再归为原来的CDP挂死，也不能以排队延期宣称网站挑战。

根因之一是已收到页面的selector timeout被通用异常分类成TRANSIENT_TRANSPORT，_attempt立即做第二次同样访问。对缺失模板/未就绪正文，重复访问既不能证明内容存在，又使单并发共享身份的占用翻倍。补修仅对Investing/InvestorsHub专用内容selector超时返回browser_content_not_ready/EXTRACTION_ERROR，保留有限实际文档用于诊断，不进行即时第二次网络重试；若当前文档实际为challenge，仍先走原页交接。不把空页/错误模板包装成SUCCESS。

这两个站点CRAWLER队列容忍从短窗口调为25秒（仍受每请求总预算、Identity单并发与60秒轮询约束），减少正常正文操作期间的不必要延期。不增加Chrome或并发，不改全局预算框架。持续超出容量仍应明确延期，不能承诺无限吞吐。

DIGITIMES另以现有owner唯一请求检查实际HTML：主Profile digitimes-nl-1的#content只有318字符/1段，明确含The article requires paid subscription，Members only弹层也要求paid subscribers。不是仅凭错误码推测，也不是把隐藏导航登录链接误判为正文；需要人工在这个主Profile核实登录/订阅权限。

## 9. Investing ticker身份误拒绝：整页文本的伪词边界

最终自然验收又出现RKLB/BE的investing_ticker_identity_mismatch；相同配置URL通过现有owner却返回正确Rocket Lab Corp (RKLB)、RKLB News及10条新闻。精确重现发现：_text(root)内部text_content()直接连接相邻DOM文本，`</h2><p>Find ...`形成`RKLB NewsFind ...`，原整页正则的News后word boundary不存在。标题前相邻节点也能消除ticker前边界。页面排版/React hydration的DOM差异会令同一正确页面时好时坏，与IP及登录无关。

修复为对h1/h2标题节点分别fullmatch ticker+News，排除article中的推荐标题；没有放宽成整页任意ticker出现即接受。四个ticker的无分隔相邻节点badcase均覆盖，另验证错误instrument标题和article中的伪MU News不能通过。仅消息总线容器需要这项新代码，Site Access保持r4镜像、不再重建Chrome或打断维护。

## 10. 正式交付与验收边界

- 所有应用补丁均已进入origin/main：02f85ad0、483d4771、fcc4f70a、c9e321d9、6a3e19ba，无远端源码overlay或未归并修复分支。最后的文档/证据提交不要求重新构建应用。
- Site Access：UTC10/15 05:59:30发布main-c9e321d9，镜像sha256:82631bf715fba6438bbbf4fea2602cf4038a5be35b85a54c0e71f173ea8cba43，Docker健康检查通过。运行runtime.py/service.py哈希分别b48250dfb40a60ae86ebe4e3302e1558e3aaa7e62271ebba391416710d05e8a8、24ca200aa35343c67a2c8d8741f5648651af58126b6a998235e2b6b9db272e38，与Git blob一致。
- 消息总线：UTC06:09:03启动main-6a3e19ba，镜像sha256:ed7ae68d05b9d7cc491e1f764cd98ed216d68185a7e1a102dc80a46bcaea47de，market_sources.py哈希dcbe207a273a1a49f51906f0cfb39e4fd7f5122247646ea75ce5289818304c27与Git一致。该worker本来没有Docker Healthcheck，首次验收脚本误等healthy标记；已改以Running/零重启/无OOM且启动后新发生的真实成功轮询验收，不重启或回滚正常worker。
- r4/r5公开发布记录位于服务器/var/lib/doxagent/source-redeployment-20261015，各自包含精确commit、git archive SHA、镜像、前后容器ID。r5核对除消息总线外所有容器ID不变，Reuters维护会话保持不变。Chrome Supervisor全程未重启；只有故障public-markets-nl实例执行过一次保留Profile的正常drain。
- 定向回归最终105 passed、3 skipped；Ruff及diff检查通过。正文实测共10篇FULL：Barron's 2、TrendForce News 2、Press 2、GlobeNewswire 2、InvestorsHub 1、Investing 1。Investing实际文章Intel live levels为2751字符，不是用ticker列表页HTTP200代替正文验收。上述canary没有向message bus发布测试消息。
- 生产配置变更仅Reuters body access_order（revision9→10）、InvestorsHub crawler队列10秒→25秒（12→13）、Investing同项（13→14）；没有修改60秒轮询、单Identity并发、监测词、相关性或时效性窗口。
- 清理仅本轮无容器引用的失败02f85ad0镜像两项（约2.08GiB），及超过1小时未使用的构建缓存（Docker报告9.232GB）；保留可用镜像/回滚集、业务库、登录Profile及源码，失败镜像可由对应commit重建。最后构建后系统约26GiB可用，发布前20GiB门槛通过。

人工事项：Reuters digitimes-de-1原challenge已保留并在两轮Site Access更新后通过宿主桥接recover接回；session READY、VNC仅127.0.0.1:5900，桌面打开工具即可继续。DIGITIMES English需在digitimes-nl-1核实登录和订阅权限，不能把历史VALID当作当前可读证明。BE官方IR RSS按要求不处理；IBKR自然轮询已恢复，本轮没有改交易/Gateway组件。

仍需明确的限制：Chrome旧实例的CDP初始化不可用已实证并恢复，但未取得足以定位Chrome原生忙循环具体函数的证据；不宣称已永久消除该类原生故障。Investing偶发真实DOM未就绪会准确返回browser_content_not_ready，由下一轮正常采集再试，不再立即重复网络访问；25秒队列仍可能在单并发身份负载超额时延期，不承诺无限容量或零challenge。本文区分已修复的确定性代码缺陷、已恢复的实例和仍需人工/长期观察的部分。

### UTC06:15最终截面（不挑选全绿瞬间）

验收回执：eval/message_source_repair_20261015/production-acceptance.json；测试明细tests-r5.xml。

TrendForce两个入口、GlobeNewswire两个入口、Barron's、InvestorsHub以及其他RSS/API均有发布后自然成功记录；Investing四个ticker也都已在新消息总线启动后成功过，RKLB从10/6以来无成功的状态恢复。但该截面Investing MU/INTC各有一次browser_content_not_ready，DIGITIMES列表共享采集恰有一次TimeoutError（上一分钟成功）。Reuters四个ticker仍由真实challenge门控，BE IR 403按要求留置。因此不能汇报“当前所有消息源全部稳定全绿”。

同时宿主机load average为11.72/14.14/14.54，短采样CPU idle约6%—18%，内存available约5.5GiB、没有这些容器的OOM证据。CPU竞争与长页面加载会放大DOM等待/单Identity排队，但不足以断言每次Timeout都只有这一原因。本轮没有越界停业务agent或改其他并发配置；余下偶发等待需要在正常负载下继续观察，不能靠延长所有超时或重复重启掩盖。

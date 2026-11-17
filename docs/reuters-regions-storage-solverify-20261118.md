# Reuters 登录存储、地区对照与 Solverify 接入（2026-11-18）

本轮保持原 Backup 的 Chrome、Profile 和出口，保留 Main 人工登录。所有时间以证据 JSON 的 UTC 为准；北京时间加8小时。此记录持续补充实际发布和验收结果。

## 已确认登录根因与修复

Main 的 www.reuters.com localStorage 为5,242,879字符，单字符写入即 QuotaExceededError；ABTastyData占5,206,645字符，VisitedPages累计38,134条。Backup同样接近5MiB。Arc SDK共享该配额，因登录令牌不能写入而显示 misleading host-connect error；宿主机磁盘和navigator.storage.estimate不是这个配额。

只将ABTastyData.VisitedPages裁剪到最近256项，保留ActionTracking、其他字段、ArcP/ArcId及所有Cookie。Main删除37,878条历史，Backup删除37,876条，写测试恢复WRITE_OK。没有清空Profile、认证或重启Chrome。登录界面恢复后，Arc identity/public/v1/config OPTIONS204/GET200，邮箱表单正常且无原错误；用户已完成人工登录。

用户先在UI执行Verify并关闭会话，结果challenge=CLEARED、crawler=READY、health_applied=true，但其/world验证地址不是文章，body=CONTENT_ERROR/authentication=UNKNOWN不能作为订阅正文证明。之后本会话再次调用已关闭会话的Verify失败，仅为重复验收，不是登录错误复发。

持久修复：reuters_storage.py按Reuters origin识别已知缓存结构，仅在超过128Ki字符且数组超过256项时裁剪；context init script覆盖新导航，warm page最多每分钟检查一次，单次上限1秒。未知结构不删除，不拦网络、不改Storage API、UA或密码。runtime的普通采集和Open Login均接入。

## 出口和独立Profile对照

原生产Main digitimes-de-1、Backup reuters-backup保持不动。新加坡、日本、美国各两标准和两高级Flower_SS节点，在原Mihomo的独立单节点连通检测中均503/504；隔离代理复核显示上游TCP超时。未把这些结果计为Reuters挑战或证明对应地区不适用。订阅刷新HTTP400，没有覆盖生产订阅。

隔离代理初期存在CNAME解析差异：原配置hosts将aws-agent.biz转至apt-agent.com入口，隔离解析错误会连接不同地址；按现有别名解析后，德国标准1对照重新验证出口205.198.126.113。它没有改变原Clash或Backup连接。

按用户授权保留同一个reuters-backup2持久Profile，停止状态下事务变更绑定至现有Backup egress jp-standard-6（名称为历史别名，真实为德国205.198.126.114），Identity revision4。目录和Cookie未清空，Backup instance44fd29ad09e14c55a42741a656397d83不变。

旧Profile:probe把Search页面套入文章等待条件而超时（非网络/Challenge结论），后续改用正式builtin:reuters_search@1。18:39:01真实恢复探测RESOLVED，18:41:05 Micron正式CRAWLER SUCCESS/HTTP200/20条/3873ms。18:42:20同Profile的文章请求DataDome401，原页已保留。因此德国共享出口方案未通过正文和持续验收，不晋升Main。既有Main/Backup仍正常生产，不能以一次查询成功宣称此候选可接替。

## Solverify契约与边界

官方文档：https://solverify.net/docs/datadome 、https://solverify.net/docs/supported-user-agents 、https://solverify.net/docs/get-profile 。实际账户/profile成功，balance=$0.05，planType=per_1000；密钥仅存生产私有credentials/datadome-solver.json（0600），不入Git。

新增适配复用既有恢复/lease/RETRY/同出口/配额/业务确认流程。仅配置provider_by_identity[identity]=solverify时选择新服务，缺省仍2captcha；SUBMITTING前冻结provider和实际Chrome UA，已提交UUID始终到原Provider查询，配置回退不会错投旧供应商。task.type=datadome、useragent小写、proxyPort字符串；pending/processing/completed映射现有状态。返回UA必须与原Chrome完全相同，不匹配不写Cookie、不改Chrome身份。只接受已验证datadome原值，Reuters统一Domain=.reuters.com、Path=/，避免host-only遮蔽；不保存原Cookie或完整验证码URL。Provider ready/APPLIED仍需独立的真实搜索与FULL正文证明。

目前腾讯云/Squid来源ACL仅允许2Captcha 138.201.188.166/32及内部Docker网段；Solverify未公布worker出站IP。已请求其精确IP/CIDR，不盲目把API服务器IP当worker或开放公网。新Provider尚未作为生产默认，也不自动追加供应商购买链。

## 发布及最终验收

待追加本轮固定SHA窄发布、源码校验、Chrome实例和自然业务窗口。证据目录eval/reuters_regions_solverify_20261118。没有声称换地区、账号登录或Provider成功可以永久消除外部风控。


## 收尾证据（补充后续结果，不能只保留首次失败）

- 候选 BODY 首次401为DataDome AUTO_CHECK，18:42:37自动恢复RESOLVED，约17秒，未产生付费任务。18:48:48 Intel正式查询HTTP200/20条/8087ms；18:49:24原文章复测HTTP200/FULL/4,508字符/4100ms。故结论不是“德国同出口一直不能工作”，而是“可搜索并能自恢复，短窗正文已通，仍未证明生产密度下长期稳定”。只有两次查询加一次成功正文，不据此替换用户刚登录的Main；原Main和Backup继续双活。
- 查询替代公共目标google generate_204时，SG/JP/US各10个候选节点的原Mihomo延迟测试仍均503/504（30次）。没有取得可验证的新地区出口IP，不能归因于Reuters地区风控。所有结果见native-regional-alternative.json。
- Site Access已按精确提交ec5355dbbff4b652eeb67d480134d88c7e6986c4，用既有storage_release.py固定依赖和发布锁窄发布。镜像bbe7612c7fe073c96699fc5a02e2e82526fb6749d0bd10649a25447d762f0e97，UTC18:46:29启动，healthy/restarts0。datadome_solver.py、reuters_storage.py、runtime.py三个运行文件SHA256均与提交吻合。只重建Site Access，Supervisor/原Clash均未重启，Main instance8465b07e2c064676a88f19b2e4b626e8/PID1458和Backup instance44fd29ad09e14c55a42741a656397d83/PID2468均不变。
- 发布后Native只读样本：Main localStorage约168k字符且WRITE_OK；Backup约102k（ABTastyData约38k），没有原host-connect错误或localStorage配额失败。每分钟/新导航的持久保护已生效。未重新填写网站密码。
- 18:21用户完成登录后至18:50:05：真实物理ACCESS_RESULT CRAWLER成功Main559、Backup635；另外88个site_queue_timeout、32个identity_control_busy、4个access_budget_exhausted。最后5分钟成功Main101/Backup100，仍13个队列延期。跨越另一会话发布与本次发布，不能将这些计为单一版本稳定性统计，也不能宣称全部ticker均绿。无新增Reuters正式body_outcomes；候选手工FULL单列，不冒充自然业务全文验收。
- 没有新的付费作业，本轮费用未发生；只有不创建任务的账户/profile请求。Solverify接入代码已上线但未选为任何生产身份的默认provider，2Captcha原配置保留。当前自然Device Check已自解，不重放失效验证码URL制造“完整打码测试”。
- 本轮147项相关回归通过（test_solverify/test_reuters_storage/test_datadome_solver共50；browser_recovery/solver_forensics/reuters_reliability_repair/reuters_dual_active/reuters_retry_chain共97）。
- 失败试验用隔离Mihomo已检查无Registry引用后停止并删除，私有临时配置删除；没有新增长期代理服务或公开端口。reuters-backup2仍保留同一Profile，独立维护scope，automation_active=false，生产Main/Backup未替换。根盘收尾约7.4GiB，发布复用依赖层；磁盘余量偏低是另一项运行风险，不是本次localStorage配额故障的原因。

## 用户追加：从腾讯云拒绝日志识别Solverify出口IP

上一轮仅凭metadata中的ins-e5vof1l6推断为CVM不充分，这个产品类型判断错误；2026-11-18用户已确认是新加坡Lighthouse，当前无可用VPC流日志。当前无本地/服务器tccli身份、无CAM实例角色（metadata cam/security-credentials/返回404），浏览器控制工具两种入口均启动失败（kernel assets路径不存在），所以当前不能读取腾讯云账户内的流日志。宿主机内核近2小时没有DPT=18791记录，**不能推断云侧没有拒绝流量**，云侧DROP本来就可能不到达宿主机。

此外，本轮尚未创建Solverify task，没有对应的worker CONNECT尝试可归因；不能把随机扫描IP或API域名解析地址当Solver来源。下一步需要确认该CVM网卡已开启VPC流日志并投递CLS；在一次真实自然Challenge提交的精确UTC窗，筛选18791/REJECT的源IP，再用受认证CONNECT和同出口证明核对。没有历史流日志时不能事后回溯。普通操作/安全组变更日志不是逐连接拒绝日志。

已请求用户确认日志是否开启及提供日志主题或必要四列记录。在取得来源证据前不扩大公网放行，不关闭已有2Captcha。官方依据：[VPC Flow Logs](https://main.qcloudimg.com/raw/document/intl/product/pdf/682_18930_en.pdf)、[企业安全组流日志依赖CLS配置](https://cloud.tencent.com/document/product/1132/112639)。此处属于证据/访问条件未满足，不能列为Solverify实测失败或实测成功。


## Lighthouse纠正及一次性来源探测（2026-11-18）

用户明确选择18792临时TCP监听：只accept/close，完全不读取CONNECT、凭据或其他payload，不提供代理或转发。探测器非root运行，15分钟TTL、32MiB内存、5% CPU、最多64个唯一公网IP，私有输出仅IP。使用完立即停止监听，云防火墙由用户撤销。不得仅凭metadata实例ID推断产品类型。

本轮Solverify网络诊断任务006d90b3-1040-4c52-a83d-6d81b232e9ef使用明确标注的不可解码网络fixture，仅触发同一datadome任务类型的代理连接，不是真实Challenge解码验收，也禁止应用任何结果。提交UTC19:16:40，随后记录来源158.180.12.56；18792监听立即停止并核实无LISTEN。用户确认已删除18792临时规则，新增18791的158.180.12.56/32并保留2Captcha来源。后续以受认证18791连接、父代理和真实出口进一步核对，不能把一次观察当平台永久固定地址。

configure-datadome.py新增可选精确solver_source_ips，默认2Captcha来源/认证/目标域名/CONNECT/never_direct限制全部保留；bootstrap保留既有额外Profile绑定和Provider私有配置，避免重跑丢失Solverify或第三身份绑定。7项配置相关回归通过。尚需追加正常代理实测及费用终态。

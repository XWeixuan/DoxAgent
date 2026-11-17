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

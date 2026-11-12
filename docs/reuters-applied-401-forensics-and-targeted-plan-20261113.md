# Reuters APPLIED 后阻断：专项根因取证与定向修复方案

本轮为已存在事件的专项取证；不新增重试、等待成功策略、购买预算、身份或组件。所有时间先用UTC，换算北京时间加8小时。完整脱敏证据位于 `eval/reuters_applied_forensics_20261113`。

## 结论与边界

已定位到两个相邻但不同层次：原有代码完成的是Cookie写入及reload ACK，而不是站点验证成功；在这次事件中，reload后的Reuters HTML实际返回200，紧随其后的搜索API返回401并触发DataDome iframe。当前自然恢复请求的401，则已通过原始响应头与响应正文证实属于CloudFront/DataDome边缘集成层，不能当作Reuters账号或订阅权限401。

尚未证明的关键点，是历史Provider返回的那条Cookie是否随第一次搜索API请求发送、何时被哪一个响应覆盖、以及Provider私有浏览器中的验证提交是否被接受。本轮不能将这个缺口直接命名为答案质量或网站风控。证据不足处先补最小取证，再按确定的分支修，不能继续凭401笼统调等待。

## 1. 复原原有完整失败链

账本job `dd_32c207cb1d1c47f78c96bcacf0e1885c`、task `84403569365`、Recovery `recovery_4519eaad023a490bb5549f123a64c88b`：

| UTC | 证据 | 可以证明什么 |
| --- | --- | --- |
| 22:09:15 | 原页RETRY输入ACK | 已点击，不等于通关 |
| 22:09:26 | SUBMITTED，Provider taskId | 已创建同一笔付费任务 |
| 22:09:56.050 | provider_ready_at | Provider声称已有结果 |
| 22:09:56.596 | 原页Navigation Timing，reload，HTTP200，redirectCount=0 | Reuters HTML加载成功；不是完整搜索会话通关 |
| 22:09:56.608 | APPLIED，cookie_scope_verified=true | Cookie曾写入并对publisher可见，reload命令已发出 |
| 22:09:57.864—22:09:58.183 | 同一个原页留存Resource Timing，`/pf/api/v3/content/fetch/articles-by-search-v2`，fetch，HTTP401 | 第一次原生搜索API请求被拒；发生在HTML导航开始后约1.27秒 |
| 22:09:58.471 | DataDome iframe Navigation Timing，`/captcha/`，t=fe，HTTP200 | 验证控件随后加载；它自己的200不是Reuters业务200 |
| 22:10:06 | 原页BLOCKED | 真实可见验证仍存在 |
| 22:10:26 | 独立CRAWLER probe，HTTP401、STILL_BLOCKED | 独立业务证明未通过；该条历史响应头/正文未保存 |

关键新增证据来自仍存活的原生Chrome Performance/Resource Timing，不是重新访问网站，也不是从401反推。Chrome实例 `ddae8dc2d59848db80c779ce7a6c6310`、target `1E4A70F5FA8B5D641C3B5E2ADF756662` 与原job allocation、当前Registry和Supervisor一致。原页HTTP200、API401的分层此前被一个BLOCKED标签压平。

这证明“文档可以访问而业务API不可访问”的即时分层失败；尚不能证明整个Challenge曾完全解除再复发，因为API从未取得成功证明。

2Captcha官方DataDome任务返回的是Cookie，客户端写Cookie并使用原会话继续访问就是现有契约，不存在已证实的“漏发一个本地验证POST”。Provider内部是否收到验证端点的接受回执不在现有API结果字段中。一次只读 `getTaskResult` 尝试获取该旧task的历史结果，返回 `ERROR_NO_SUCH_CAPCHA_ID`；不把它改记为当时任务失败，也未重新购买或重新应用Cookie。原Cookie无法事后恢复，需前瞻指纹。

## 2. 当前401的确切归属

一次有界本地CDP被动监听（22:58:32—23:03:51）只观察既有生产自然请求。Main自身后台在23:03:49左右发起的 `/site-search/`：

- 原始HTTP401，`server: CloudFront`、`x-cache: LambdaGeneratedResponse from cloudfront`、`x-datadome: protected`、`x-dd-b`存在；正文771字节，含DataDome bootstrap及captcha-delivery域名，无登录认证提示。
- 实际请求头含DataDome和ArcP，`associatedCookies.blockedReasons=[]`，并非Chrome因为SameSite/作用域而未发送Cookie。响应又Set-Cookie了新的DataDome值，指纹与请求值不同，没有Set-Cookie被阻止。
- 未观察到重定向；HTTP/2，公共证书签发者Sectigo Public Server Authentication CA OV R40，非Service Worker、非磁盘缓存。
- 同窗Backup的相同原生搜索API为HTTP200、JSON、`server: openresty`、正常CloudFront转发，DataDome/ArcP同样实际发送，无Cookie阻止；TLS签发者和协议相同。

因此本次新鲜401具有明确DataDome边缘拒绝证据；没有证据归到Reuters登录/付费权限层，也不是仅凭状态码归因。它不是历史22:10:26那条响应的替代副本，不能回填历史网络头。历史API401与随后加载DataDome控件高度一致，但当时的逐请求头及原始响应正文仍缺。

## 3. 主备、Cookie及代理对照

- 两者均正式Chrome153.0.8010.52、External CDP、en-US、Europe/Berlin、1440×1000、相同recipe和原生页面搜索方式。Chrome元数据UA与navigator UA各自一致，未见该处UA分裂。比较是相同业务入口/原生搜索API的自然请求，不冒充同时间同关键词受控A/B实验。
- Profile、Chrome进程和Cookie彼此独立；Main session_revision=9，Backup=0，出口分别205.198.126.113/.114。现有数据无法把两者差异唯一归因为IP或Profile信誉，因为二者共同变化。
- 当前两者都只有一个 `.reuters.com` `/` DataDome Cookie、Secure=true、HttpOnly=false、无分区；ddSession localStorage均不存在，未见sessionByHeader配置。可排除当前重复Cookie/强制HttpOnly/旧ddSession覆盖的明显证据，但不能排除应用瞬间曾发生覆盖。
- Main/Backup都有ArcP；Cookie存在和Registry VALID不能证明账号服务端仍接受认证。当前拒绝先发生在DataDome边缘，不能靠重登录直接归因/修复。
- 私有配置静态核对：2Captcha认证账号绑定的Squid父代理与对应Chrome所用Clash listener一致；Main18083，Backup18080。Squid无TLS bump、never_direct开启。Chrome看到的remote_ip=172.18.0.3是本地代理端，不是公网出口；同出口依据是已有Registry观测加实际父代理绑定，而非该remote_ip。
- 历史付费窗口中，Provider来源确有多个认证成功的CONNECT/200隧道和约MB级流量，因此不是完全没有走本机代理。同时还有多条CONNECT/403，旧日志没有目标域名，不能区分被拒的是广告、验证码必要依赖还是其他目标，不能忽略，也不能认定它已造成求解失败。
- 2Captcha getTaskResult的ip按官方定义是提交任务请求的IP，不是验证码求解出口，不能用它冒充同出口证明。

## 4. 本轮落实的最小取证修补

只补可证伪的证据，原有效恢复机制、购买限制、Profile排他和原页保留均保留：

1. `NativeChallenge.apply` 仅在一次真实付费结果应用时，记录同Identity/Profile/Chrome/context/target绑定、Provider Cookie属性及事件内HMAC指纹、应用前/写后/观察后Cookie元数据。Cookie和账户值不落日志/账本。
2. 订阅既有reload的原生Network事件，5秒观察窗口，包含错误响应body读取的后置取证最多8秒；准备取证最多2秒。最多6条相关导航/搜索响应、4跳重定向、131072字节Network总缓冲和65536字节单资源缓冲；只读错误响应，健康文章/搜索正文不采集。不发额外请求，不开interception，不截图、不完整抓包。
3. 记录真实发送的Cookie指纹和blockedReasons、响应DataDome/认证/缓存头、Set-Cookie指纹及被阻止数量、TLS/来源、错误正文结构和标记/hash。可以区分“写入但没发送”“发送后被替换”“HTML200/API401”“真实业务成功后再被阻断”。原Provider验证服务器的接受回执不伪造为本地可见。
4. 仅给后续既有DOCUMENT/CRAWLER/BODY诊断附加一次响应证据及provenance；相同状态不因新的query、正文hash或HTTP细节反复写。Rendered DOM明确标注为RENDERED_DOCUMENT，不与原始WIRE响应混用。普通401、登录弹窗和未知DOM不能单独归为DataDome。
5. Provider既有ready结果附带createTime/endTime/solveCount三项数值回执；不把这些当站点接受证明。
6. Squid原有受限日志行增加目的域名和实际父代理IP/端口，沿用现有轮转，不记录路径、query、Authorization/Cookie，也不扩大目标白名单。用下一次自然任务查清403究竟拦截了什么。
7. 应用证据并入原job、单次有界写入，沿用既有GC。无新表、常驻诊断进程或状态机。诊断失败保留PARTIAL/UNAVAILABLE，不能将已经写入Cookie的行为伪报为未应用或诱发重新购买。

## 5. 定向修复决策与验收

| 下一次自然事件的证据 | 才执行的修复 | 验收 |
| --- | --- | --- |
| 写后指纹正确，请求未携带或blockedReasons非空 | 只修具体Domain/Path/SameSite/分区上下文或控制层绑定；不清Profile、不批量改Cookie | 同一Cookie实际发送，业务合同通过 |
| 应用后Cookie在第一次业务请求前被旧页面/其他响应覆盖 | 按具体覆盖来源修页面/任务所有权与会话收尾；保留原页，不全局禁用站点JS/SW | 时间线显示正确令牌送达、没有被旧任务覆盖 |
| 同一指纹已发送、无本地阻止，API仍明确DataDome拒绝 | 内部传递链已排除，整理脱敏task、时序、属性、出口证明给Provider进一步核对其验证回执/绑定；不无界重购、不默认答案错误 | Provider声明、原页、真实API和自然CRAWLER/BODY分别成功 |
| HTML200/API401但API为认证JSON/WWW-Authenticate，且没有DataDome响应证据 | 按实际Arc登录/会话过期修认证，不走验证码修复 | 登录层和搜索/正文分别通过 |
| Proxy403证实拒绝了必要验证码依赖 | 只放行已证实必需的具体域名，维持认证、来源IP、443及禁止私网；不开放代理 | 下一次自然任务依赖正常、无相关拒绝且业务验证通过 |
| 页面真正解除、API自然成功后再出现新DataDome拒绝 | 记为新复发，保留真实持续时间，再审身份/访问行为与Provider适配 | 不把短暂HTML200或APPLIED计作业务成功 |

本轮无额外createTask、无手工网站请求、无Profile/Cookie清理、无出口切换，原人工现场保留。未来证据尚未经过新APPLIED的真实成功分支验收，不能宣称已修好第三方结果被拒的外部原因。发布、回归和原页保留实证在收尾追加。

## 官方依据

- [2Captcha DataDome Cookie返回契约](https://2captcha.com/api-docs/datadome-slider-captcha)
- [getTaskResult字段含义，ip是任务提交请求的IP](https://2captcha.com/api-docs/get-task-result)
- [DataDome Cookie/localStorage及属性说明](https://docs.datadome.co/docs/cookie-session-storage)
- [Squid按域名记录而非完整URL的logformat字段](https://www.squid-cache.org/Doc/config/logformat/)

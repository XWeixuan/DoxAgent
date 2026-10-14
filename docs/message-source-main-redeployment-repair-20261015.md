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

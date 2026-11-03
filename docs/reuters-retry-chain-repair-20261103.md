# Reuters RETRY 完整链路修复与真实验收（2026-11-03）

基线本地/生产 `91824dd8`。本轮只改 Reuters/DataDome 访问恢复，保留另一会话的 Collector store/worker 未提交修改。现有主备实例分别 `003376ecd36341dba6e684dd95ee8149`、`0926eaa72cd74c388a3035228448c6c4`；原页 target `4697F6D8F7A04CA2AB1AC6E2EE106041`、`323293F935306C7791C01F3C77A8BC72`。不刷新/重建它们来代替点击验收。

## 本轮确认的失败机制

1. 两个当前事件都停在 `NOT_DISPATCHED / attempt_no=0 / preflight_failures=3`，没有关联付费任务。DOM、OOPIF、RETRY 控件及坐标均可毫秒读取；不是当前两个现场的 DOM 不可读，也不是 Provider 已付费后失效。
2. 控制变量复现：激活同 Chrome 的空白标签后，向原 Challenge 页发送 `Input.dispatchMouseEvent(mouseMoved)` 等待5秒超时；激活原标签并 bringToFront 后，相同坐标的 move/press/release均在毫秒级 ACK。原生产路径没有聚焦动作，却把这种输入前失败永久累计封顶，后续没有真实点击。修复聚焦、按钮位、有限按下时长，并记录失败阶段；只允许旧“确认未发送输入”的三次前置失败在新版本重新尝试，保留所有不确定点击与付费账。
3. 聚焦后的点击确实触发了新的 DataDome iframe 文档 GET200、JS/CSS/字体200，控件退出RETRY进入加载态；但本地canvas高度仍0，之后回到RETRY。没有观察到本地正常绘制的滑块。已知资源回执不足以断言是DataDome拒绝还是浏览器运行环境导致此加载失败，不能只凭t=fe宣称本地滑块已就绪。
4. 截图/坐标路径已做对照：独立VNC framebuffer为1440×1000，橙色RETRY屏幕框 `[593,317,867,357]`，中心 `(730,337)`；与1439×912内容视口中的 `(719.5,240)`不同，含窗口位置和浏览器工具栏。排除了顶部标签图标的橙色像素，实测VNC底层鼠标点击同样触发加载而未证明滑块就绪。因此没有把固定坐标或盲目颜色匹配做成生产全局功能。

证据位于 `eval/reuters_retry_chain_20261103/`：baseline、scenes、input-backup-background/focused、network-backup、screen-click-backup等。原始验证码URL、Cookie、密钥不输出、不提交。此次OPEN维护会话测试后均通过RELEASE_VIEWER释放，保留原页。

## 实施路径

- 优先修复现有原生CDP点击，不另建浏览器/代理/账号/通用视觉Agent。
- 物理点击ACK和截图取证分离：按下/抬起均ACK后，页面导航造成后置DOM/截图超时不能再把点击误记为未发送。
- 第三路径是**有界Provider bootstrap**：Reuters原页上有新版本已ACK的RETRY，原instance/target一致，动作5–120秒内，实际加载可见DataDome `/captcha/` iframe、`t=fe`且具有真实cid/initialCid/hash时，可由2Captcha自行加载验证码，不再强制等待本地canvas完整绘制。不得拼接、改写或猜测captchaURL；t=bv、隐藏/未知frame、DENIED、非Reuters、未确认/过期点击均不走该路径。
- 继续使用原有出口一致性校验、任务幂等、每日/身份额度、付费冷却、原页Cookie作用域验证和业务恢复验证。Provider失败不得无限重购，也不得把ready/APPLIED当作原页通过。

2Captcha官方任务合同要求实际captchaURL、同出口代理及真实UA，没有要求客户端先完整绘制canvas：[官方DataDome文档](https://2captcha.com/api-docs/datadome-slider-captcha)。本轮后续必须用真实任务与原页验证决定这条路径是否有效，不以文档或离线回归代替。

## 发布与真实结果

代码 `a7dcec8b1eb06e4627646712ee3dc7679dbf1ab7` 已推送 origin/main，UTC 2026-11-03 14:38:25 完成标准窄发布，仅重建 Site Access。两个修改模块的运行文件 SHA256 与该提交逐一一致；Supervisor 容器仍为 `37881d9a4b92`，两个原 Chrome instance 未变。发布前两个维护 Viewer 均已正常释放，没有把维护锁留给自动恢复。

| 身份/事件 | RETRY ACK | Provider task | ready / 原页应用（UTC） | 验收结果 |
| --- | --- | --- | --- | --- |
| 主身份，原事件 `recovery_7aa57ab2dcc34191b66bc2bef856408c` | 14:39:06 | `84313566657` | 14:39:47 / 14:39:50 | 14:40:00 原页 CLEARED，CRAWLER 业务证明通过，`BUSINESS_VERIFIED / VERIFIED_IN_WINDOW` |
| 备用，原事件 `recovery_ac37df62b22e413f95a56bfb1f71c147` | 14:39:16 | `84313568401` | 14:39:55 / 14:39:57 | Cookie 作用域验证通过，但确认窗内仍有可见验证码，`STILL_BLOCKED` |
| 主身份，自然复发事件 `recovery_06606fedd8864c5583b42fc7c1a9808a` | 14:41:46 | `84313587466` | 14:45:48 / 14:45:51 | 14:48:15读回确认窗结束，`STILL_BLOCKED`，不能计通关 |

三笔每笔费用 `$0.00145`，已知合计 `$0.00435`，不把旧历史费用混入。全部任务由生产 solver 自然提交及轮询，没有直接调用 Provider 手工购买、改写结果或重复使用旧付费 Cookie。

**实际打通了主身份既有原页的 RETRY→付费任务→ready→原页应用→原页通关→搜索业务验证链，但没有达到持续稳定。** 首次恢复后约34.7秒（14:40:35）真实生产采集再次触发 Challenge，形成新事件和新 target `63617B8E46A185ABCD687381DD091990`；这是恢复流程之后的新阻断，不是发布重建丢失旧现场。旧原页已完成通关，随后由正常生命周期释放。当前新主原页、备用原页均保留，不清 Cookie、不换出口。

备用付费前后场景摘要从 `303df6e4…` 变为 `d76b47a7…`；新原生读回仍有可见、可读取的DataDome iframe和RETRY。不能把后方Search DOM/页面标题当作已解锁。它的故障已越过输入、提交、返回和Cookie写入阶段，停留在“应用后站点再次要求验证/未认可结果”；现有证据尚不足以区分Provider答案被拒与新的站点风险判定。主身份在相同新路径已成功，排除了bootstrap本身必然无法解码的假设，但不足以证明当前出口/身份能持续被站点接受。

恢复后的采集同时产生多条 `site_queue_timeout / BUDGET_DEFERRED`，与随后真实Challenge分列：这些是内部排队延期，不是新的打码失败。此次未取得正式正文 FULL 证明，也未证明全部ticker自然连续轮询成功，不宣称正文或长期稳定性已通过。

## 回归与边界

四个相关核心文件回归78项通过；补充聚焦顺序和button位断言后，两个直接相关测试文件29项通过。Ruff F/E9及diff检查通过。保留另一会话的Collector/Massive开发，不改其行为。

本轮没有引入视觉模型、写死生产点击坐标、通用截图识别框架、自动移动滑块、额外浏览器或无限重购。截图路线证实输入可达，但不能解决本地滑块不绘制；实际成功来自聚焦修复和有界Provider bootstrap。局部内部阻塞已修复，后续风险是通关后极快再拦截与备用结果不被认可；不能靠继续无界购买宣称已解决稳定性。

脱敏证据：`eval/reuters_retry_chain_20261103/release.json`、`verified-release.json`、`proof-final.json`、`proof-terminal2.json`、`scenes-fingerprint.json`。最终读取时间UTC14:48:15，三笔Provider均已返回和应用，只有首笔有原页及业务通关证明，其余两笔确认窗均为STILL_BLOCKED。这些是有时间戳的读取快照，当前状态应以最新生产账本为准。

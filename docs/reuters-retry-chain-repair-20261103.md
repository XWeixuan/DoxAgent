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

待补充精确版本、主备原页留存、真实点击、付费task、费用、Provider返回、应用、原页/业务验证及仍未覆盖分支。

# Device Check 卡死与恢复门禁：根因、修复方案及验收

## 已确认根因

2026-10-22 北京时间约04:45—04:50读取生产实际现场，不以状态码或 iframe.src 猜测：

1. Reuters 父文档1566字节，iframe.src仍是 `/interstitial/`；`Page.getFrameTree`不含跨进程子框架。实际OOPIF为 `/captcha/?t=bv`，标题 `You have been blocked`，正文明确 `Access is temporarily restricted`。这是已拒绝，不是还在计算Device Check。旧识别只读父DOM/FrameTree，漏读终态；solver因此一直WAITING_SLIDER，AUTO任务又在维护列表隐藏。
2. Reuters AUTO恢复无总等待终止条件；15分钟只清理页面再新建，不能改变同事件长期AUTO状态。只有真实交互才升级人工，拒绝、未知和超时自动验证缺少可人工检查的出口。
3. InvestorsHub已返回正常页面和14条可解析新闻；请求 `/stock-market/NASDAQ/MU/news` 正常重定向到 `/stock-market/NASDAQ/micron-technology-MU/news`。业务适配器接受同ticker重定向，恢复验证却只接受字符串完全一致，造成200/CONTENT_ERROR无限循环。这不是当前Challenge。

## 可直接执行的窄修复

### 1. 精确读取跨进程验证框架

- 复用原生CDP，仅在已有、允许的验证iframe中观察；通过DOM.querySelectorAll/describeNode取得该iframe的frameId，再读取对应TargetInfo实际URL和DOM。不得从同一Chrome里随意选一个captcha target，保持精确页面归属。
- 同进程iframe兼容contentDocument/FrameTree；读取失败明确UNKNOWN，不能仅凭旧src无限宣称自动验证仍运行。
- 将同一观察方法用于DataDome solver和人工/后台原页检查，实际 `/captcha/?t=bv` 或已加载验证页明确拒绝应标ACCESS_BLOCK，不再AUTO_CHECK。
- 不构造或改写captcha URL/t参数；t=bv不向2Captcha购买无效任务。实际可处理滑块仍走现有同出口付费流程。

### 2. 自动恢复有限等待，保留人工接管和后台探测

- 复用现有BrowserRecovery/ChallengeTask，无新增控制服务。记录明确的manual_review_reason/时间，区分“需要人工检查”与“已识别为交互验证码”。
- 明确访问拒绝立即进入人工检查；AUTO/UNKNOWN持续未恢复，在3—5分钟有界等待后进入人工检查，不反复清零期限。
- 人工检查状态保持稳定，不能因父DOM旧/interstitial/再被重分为AUTO并隐藏；真实业务证明通过后才结束事件。原页存在则交接CHALLENGE并保留，丢页则创建TARGET_LOST待办，允许Reopen；普通Open Login继续独立。
- 人工检查仍每10分钟进行既有隔离业务探测；OPEN人工会话优先，不导航/刷新用户页。可用fallback继续接替；不假装不可用fallback已验证。
- 若同事件同时影响CRAWLER/BODY，先验证CRAWLER，BODY失败不能让可用采集一直等待同一篇旧正文；各能力独立提交真实证明。
- 上游仍可见延期/阻断，但附具体人工检查原因和Task，不再只有无期限site_recovery_pending且无入口。不得仅为清错误而伪造READY/FULL。

### 3. 修复InvestorsHub规范化URL合约

- 仅该source允许HTTPS、同固定host、同交易所、同ticker的short-symbol→company-slug-symbol新闻列表转换；不同ticker/交易所/路径、登录/首页/外域仍拒绝。
- 沿用实际parse_investorshub解析器，必须有真实列表证明，不以200直接解锁。补正BODY验证不应使用/news列表的通用判定。

### 4. 测试、发布和2Captcha验收

- 回归：空FrameTree且OOPIF重定向t=bv、同进程/读取失败、跨页关联拒绝、自动超时一次升级且不隐藏、丢页人工入口、OPEN用户保护、crawler/body独立推进、InvestorsHub同ticker规范化与反例。
- 仅发布相关Site Access（若确有消费者变化才发布对应组件），不重建Supervisor、清Profile、换Cookie或绕过健康证明。保留原页证据、发布基线与逐文件哈希。
- 通过真实来源轮询/恢复探测验收，不把一次HTTP200或容器healthy当完成。现成可处理DataDome滑块才尝试2Captcha；无现成滑块不制造请求、不持续盯守。若必须换出口，使用现有身份的受控换出口流程并同步solver绑定/同IP检查，不在浏览器中伪造环境。

官方依据：[DataDome Device Check](https://docs.datadome.co/docs/device-check)会放行、追加验证码或拒绝；[2Captcha DataDome](https://2captcha.com/api-docs/datadome-slider-captcha)明确t=bv为IP被拒绝，不是可直接提交的fe滑块。

## 实现与真实验收

待本轮修复后按真实回执追加，不预先声明消息源恢复或付费解码成功。

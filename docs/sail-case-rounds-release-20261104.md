# W1/W2 R2/R3 Sail DeepSeek high 主备切换

配置入口：`DOXAGENT_PERSISTENT_RUNTIME_V2_API_PROFILE=openrouter_sail_high`。

四轮主模型为OpenRouter `deepseek/deepseek-v4.1-flash-1017` high，锁定`sail-research/fp4`，禁用网关自动provider fallback。R1保持原配置。

Sail的wire Schema副本递归把所有对象properties列入required；不改业务Schema、本地跨字段和范围校验。固定Schema/Core/轮次Prompt及一致性指令在前，Case/父轮/模式/修复数据在后。Sail隐式前缀缓存，不发送Anthropic cache_control，不承诺1小时TTL；使用按轮次与固定内容哈希生成的session_id维持粘性。缓存token、原生usage继续落回执。

Haiku high完整配置冻结到models.case_fallback四个条目，等同原openrouter_haiku_high：Google Vertex Global、strict原生Schema、65536输出上限、300秒、显式1h缓存以及现有固定前缀布局。

主模型出现鉴权/余额/限流/超时/上游请求或流错误、截断、结构或本地语义校验失败，立即切换一次Haiku，由Haiku沿用现有重试预算。每次请求独立回执并记录model/endpoint/effort/usage/error；持久化切换状态，进程恢复不会重复主调用或来回切换。R3显式重放成功R2父轮，支持DS主轮与Haiku备轮组合。取消、租约、未知派发、绑定等控制错误不属于fallback。

不修改Router、业务Prompt、W3、历史Case或原R1回退；旧Case没有新冻结case_fallback时不会因当前默认配置改变而换模。识别不出的业务误判不能自动触发fallback：历史W2 #14因果条件误HIT已向用户说明，本次不以结构化输出掩盖该风险。

W2R3 wire Condition身份使用Policy/Condition格式正则，对齐Router已有复合身份合同，保留本地完整覆盖和对象身份校验，不按Case生成枚举。

W1R3日期字段在wire副本使用YYYY-MM-DD正则替代format=date，本地合法日历日期校验保留。

本地验证：191项回归通过，覆盖四节点Schema与缓存布局、Haiku参数不变、主备切换/中断恢复/R3父轮、旧R1回退和运行控制边界。真实调用、缓存与发布验收另追加于本文件。


## 发布验收（2026-11-04 北京时间）

代码提交：`ea069dff`（主备与缓存）、`d21daeb5`（Sail日期约束）、`d5eb50d5`（W2R3复合身份格式）。已推送origin/main并同步远端仓库。源checkout存在其他会话在途改动，因此使用独立worktree，不覆盖或提交他人的未提交内容。

API/Scheduler分别以各自当前生产镜像为基底，用上述已提交源码重建不可变镜像，只覆盖本次4个实现文件，避免把两角色不同的既有能力互相覆盖。镜像标签为`doxagent-v2:sail-d5eb50d5-v2-api`与`doxagent-v2:sail-d5eb50d5-v2-scheduler`。容器文件哈希与git archive源码逐项一致；保留原7层Compose配置，仅修改compose.current.json中这两个角色的镜像和profile，其他角色、凭据、卷及资源配置不变。持久配置入口以该overlay为准，未将新profile全局注入尚未重建的其他角色。

北京时间19:49—19:50重建、重启API/Scheduler；API healthy，Scheduler running，无重启循环。执行包`68898079d2e48613e3b15b958a66f24b869b6a70b60d9ce691f95a4f31eef8b9`已激活，runtime_models.json中四节点均为Sail high，四份Haiku high备用也已冻结。未回放历史失败Case或改写旧冻结配置。

### 真实调用与缓存证据

本轮为发布能力测试，不是重新统计模型准确率。初期的400、429、身份校验失败全部保留；修复后收集到下表8条成功主调用，另有1条真实Haiku high备用配置调用成功。这些是分批补测的成功回执，不能写成整批首次8/8通过。

|节点|成功样本1缓存/输入token|成功样本2缓存/输入token|
|---|---:|---:|
|W1R2|2,304/4,084|2,560/2,891|
|W1R3|2,801/3,122|2,816/4,282|
|W2R2|6,668/6,948|9,734/10,014|
|W2R3|5,481/5,678|8,933/9,130|

Haiku备用真实调用读取4,784/7,246缓存token，模型与Vertex Global端点、高档位、1h断点及65536输出上限保持原配置。表中缓存数是整个请求被provider报告的缓存读取，可能含父轮或以前重复Case，不能等同于仅固定Prompt命中率或1小时保留率；固定系统前缀在跨Case/父轮/模式/修复输入变化时保持一致已有回归验证。

生产自然流量证据：截至北京时间19:52:37，Case `case_46eb32c2667749c9ac3fb28d0caa1b3e` 的W1R2已经使用Sail FP4/DS1017 high成功完成，缓存2560/2997 token。其W1R1、W2R1保持Haiku high正常。该观察窗尚未覆盖自然流量的全部四轮，不把隔离R3探测冒充自然生产回执。

### 保留的限制

- Sail当前共享池曾返回429/no_asap_capacity并建议180秒后重试，非key失效；运行时将触发Haiku备用，不在同一provider中长时间等待限流重试。
- 高档位单次探测W2R2耗时约77秒、239秒；不能承诺切换后所有Case更快，也不会因为一次可消费成功较慢就自动换模。原300秒等待和65536输出预算保留。
- 日期format=date两次400，改wire正则后200；本地继续拒绝2026-02-31等非法日期。W2R3曾输出裸C1，加入wire复合身份正则后两条通过本地完整覆盖校验；该校验失败在真实Service链路触发Haiku备用已有新增回归。
- 本地/上游结构化校验无法证明业务因果判断正确，历史W2 #14问题没有被本次模型切换解决。

证据保存在原checkout的`eval/sail-case-rollout-20261104/`，远端`/var/lib/doxagent/releases/sail-rounds-d5eb50d5/`含原配置备份、源码hash、镜像与激活验证。初期与补测回执分目录保留；临时评估凭据文件清理。191项回归全部通过，包含真实跨字段/覆盖校验失败切到备用的测试。

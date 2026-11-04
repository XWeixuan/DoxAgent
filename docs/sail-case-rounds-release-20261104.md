# W1/W2 R2/R3 Sail DeepSeek high 主备切换

配置入口：`DOXAGENT_PERSISTENT_RUNTIME_V2_API_PROFILE=openrouter_sail_high`。

四轮主模型为OpenRouter `deepseek/deepseek-v4.1-flash-1017` high，锁定`sail-research/fp4`，禁用网关自动provider fallback。R1保持原配置。

Sail的wire Schema副本递归把所有对象properties列入required；不改业务Schema、本地跨字段和范围校验。固定Schema/Core/轮次Prompt及一致性指令在前，Case/父轮/模式/修复数据在后。Sail隐式前缀缓存，不发送Anthropic cache_control，不承诺1小时TTL；使用按轮次与固定内容哈希生成的session_id维持粘性。缓存token、原生usage继续落回执。

Haiku high完整配置冻结到models.case_fallback四个条目，等同原openrouter_haiku_high：Google Vertex Global、strict原生Schema、65536输出上限、300秒、显式1h缓存以及现有固定前缀布局。

主模型出现鉴权/余额/限流/超时/上游请求或流错误、截断、结构或本地语义校验失败，立即切换一次Haiku，由Haiku沿用现有重试预算。每次请求独立回执并记录model/endpoint/effort/usage/error；持久化切换状态，进程恢复不会重复主调用或来回切换。R3显式重放成功R2父轮，支持DS主轮与Haiku备轮组合。取消、租约、未知派发、绑定等控制错误不属于fallback。

不修改Router、业务Prompt、W3、历史Case或原R1回退；旧Case没有新冻结case_fallback时不会因当前默认配置改变而换模。识别不出的业务误判不能自动触发fallback：历史W2 #14因果条件误HIT已向用户说明，本次不以结构化输出掩盖该风险。

本地验证：189项回归通过，覆盖四节点Schema与缓存布局、Haiku参数不变、主备切换/中断恢复/R3父轮、旧R1回退和运行控制边界。真实调用、缓存与发布验收另追加于本文件。

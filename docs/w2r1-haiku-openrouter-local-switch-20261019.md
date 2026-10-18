# W2R1 Haiku 5.5 本地切换记录

本轮完成本地代码和部署配置修改，未提交、推送、连接远端部署或重启生产容器。

## 生效配置

- 仅新 Case 的 W2R1：OpenRouter `anthropic/claude-haiku-5.5`，推理 `high`。
- 端点锁定 `google-vertex/global`；`allow_fallbacks=false`、`require_parameters=true`。
- 保留 180 秒请求超时、16384 输出上限、严格 JSON Schema 和本地 Schema/Policy ID 语义校验。
- 固定指令和完整 Policy 库各设置一个显式 `cache_control`，默认 `ttl=1h`；当前消息位于缓存断点之后。
- 保留当前提示词内容和完整 Policy 输入；没有新增预热调用、缓存管理任务或业务路由。

`settings.py` 和 `.env.example` 的 W2R1 默认模型已更新。生产 Compose 的共享 environment 显式指定新模型与 180 秒超时，避免未来部署被旧环境文件中的 DeepSeek 设置覆盖。运行新配置需要现有 `OPENROUTER_API_KEY`，缺失时明确报配置错误，不自动换回百炼。

W1 各轮、W2R2、W2R3 继续使用原百炼通道与各自推理配置。W2R2 为完整显式输入调用，不把 Haiku 的响应 ID 当作百炼可继承的服务端上下文。

## 实现与历史兼容

新增 `src/doxagent/persistent_runtime_v2/openrouter.py`，作为 W2R1 专属路由包装器，复用原百炼客户端处理其他节点及历史请求。

`runtime_models.json` 和新 Case 冻结 W2R1 的模型、high、OpenRouter、端点和 TTL。服务根据冻结 provider 选择通道；历史 Case 没有这些新字段时保留原百炼模型/推理强度，不静默改写、迁移或重跑历史 Case。

`RuntimeModelTurn` 兼容新增 `openrouter`、`high`、Chat Completions transport、端点、TTL、缓存写入 token、完整 provider usage（包含费用）。成功记录以及输出/语义异常记录保留缓存用量；JSON、Policy 身份和截断错误继续由原编排重试机制处理。取消与租约等运行控制不由该适配器吞掉。

模型和回执字段保存在既有 JSON payload 中，本轮无需数据库表迁移。新增 HTTP 客户端在服务完成线程排空后关闭。

## 验证

此前缓存实验见 `dev_plan/workflow_v2/w2_r1_haiku_explicit_cache_test_20261019.md`。本轮另从本地使用**生产适配器**真实调用原冻结样本两次，调用前验证实际请求与原测试配置逐字段相等。

后续按用户要求，默认端点从 Europe 调整为 Global。下表保留端点调整前的 Europe 真实回执；Global 本轮完成离线接线验证，未新增真实调用。已冻结 Case 的端点继续保持原值。

| 生产适配器调用 | 输入 tokens | 缓存读 | 缓存写 | 耗时 | 总费用 USD |
| --- | ---: | ---: | ---: | ---: | ---: |
| INTC A 原消息 | 56,582 | 0 | 55,822 | 5.039 s | 0.01267189 |
| 同一库、更换消息 | 55,982 | 55,822 | 0 | 2.159 s | 0.000704242 |

第二次输入读取占比 **99.71%**。召回与不召回输出均正确，原始响应和用量保存在 `eval/w2_r1_haiku_cache_20261019/production_adapter_smoke.json`；复现脚本为同目录 `production_adapter_smoke.py`，不含凭据。

离线回归覆盖：请求拆块、默认配置、旧模型分流、冻结配置、SQLite 回执往返、HTTP/超时/截断/非法字段错误，以及未知 Policy 触发原语义重试。工厂测试确认执行 Bundle 实际包含新配置。

最终分进程运行：模型/缓存/持久化/执行恢复/工厂相关 **80 项通过**，线程池并发与关闭 **7 项通过**；新增模块及测试通过 Ruff 检查，Compose YAML 和本地生效默认值核对通过。一次组合运行中，线程池测试对全进程 `prv2-hot-*` 线程名的断言失败；该组独立运行全部通过，本轮未为此修改线程池业务逻辑。

扩展检查发现 `test_process_killed_between_w1_and_w2_resumes_without_repeating_w1` 的旧断言期望立即 `ADJUDICATED`，实际为 `PENDING_W3`。使用隔离临时模块剥离本轮所有 service 接线修改后，同一断言仍失败；证据为 `eval/w2_r1_haiku_cache_20261019/prior_service_process_test.txt`。该既有异步状态测试未在本轮改动，不改变 W3 行为来迎合断言。

## 后续部署边界

远端仍运行原 DeepSeek 配置。本地变更后续发布时须同步新模块、相关模型/记录结构和 Compose，并更新调度器实例，使新接收的 Case 取得包含 OpenRouter 配置的新执行 Bundle。已冻结的旧 Case 继续依照旧 Bundle 恢复。

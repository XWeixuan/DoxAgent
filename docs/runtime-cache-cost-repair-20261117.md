# W1/W2 缓存与成本优化实现记录

日期：2026-11-17。状态：已提交 origin/main 并发布生产，实际调用验证通过。

## 实现范围

1. OpenAI W1R1：为每个已经完成的 provisional 内容块保留显式缓存边界。固定 system、Known Event Index、块文本、排序、ID、缓存键与 12h TTL 不变；当前不足一块的事实与消息正文仍在最后一个缓存边界之后。
2. W1R3：`gpt-6-luna` 从 high / fast 改为 medium / default，保留 30m 固定提示词缓存、原输出 Schema、父轮回放，以及 OpenRouter Haiku 5.5 high / Google Vertex Global fallback。显式发送 `service_tier=default`，避免省略参数后落回适配器的 fast 默认值。配置 profile 继续沿用 `openai_luna_all_high` 名称，兼容现有部署配置。
3. W2R2：仅从模型输入的 Policy 投影中删除 `transmission` 和每个 Activation Condition 的 `trigger_layer`；原先排除的 `ref` 仍不注入。经用户确认，暂缓删除 `match_scope`，保留 RELATED 的现行判定依据。未改提示词、输出 Schema、原始 Policy、criterion、calibration、decision、W2R1 或 W3 的 Policy 读取。

## 缓存规则及各轮检查

依据 [OpenAI 官方缓存规则](https://developers.openai.com/api/docs/guides/prompt-caching)：显式模式查找本次请求提供的边界，当前文档规定可查最初 2 个及最近 50 个显式边界；最多 4 次写入不等于只能提供 4 个查找边界。

原实现复用了 Haiku 的“只标最新完整块和向前第 21 个块”布局，导致文本虽保持不变，旧端点却从新请求中消失。新实现只在原生 OpenAI 适配层补齐完整块边界，Haiku 自身的最多 4 个断点布局不变。服务端按规则选择写入；真实请求超过 270 个查找标记已成功，input token 与旧布局一致。

|轮次|保留的缓存策略|检查结论|
|---|---|---|
|W1R1|固定指令、日冻结事件库、完整 provisional 块；12h|已修复增量端点丢失|
|W2R1|固定指令与完整稳定 Policy projection；12h|动态消息/重试修正不进入缓存，无需改动|
|W1R2、W2R2|固定 system 前缀；30m|不为每条 Case 的全文、候选组合和修正内容创建缓存|
|W1R3、W2R3|固定 system 前缀；30m|私有 R2 父轮输入/输出位于缓存前缀之后；medium/default 的 W1R3 已实测命中|

provisional 继续采用当前每 5 行为一块的确定性分块，不增加额外预热调用、状态表或压缩模型。原问题是每次推进都可能重写整段 provisional，扩大批大小只是推迟问题；本次修复后，实测两次增长仅写入新增的 313、151 token，保留小批次可控制未缓存活动尾部。没有基于小样本宣称“5 行为全局最优”。如单次增长越过超过 50 个块，旧末端可能超出 API 查找窗口，固定事件库边界仍可查；不承诺所有输入与并发情况下 100% 命中。

## W1R1 真实增量对照

使用上一轮排查定位的三个 INTC 冻结输入，分别对旧、新布局顺序调用。每组使用独立缓存键，从冷缓存开始；消息、提示词、Schema、high/fast 参数和输入 token 数一致。

|provisional 行数|input token|旧 cache read|旧 cache write|新 cache read|新 cache write|
|---:|---:|---:|---:|---:|---:|
|1,339|73,619|0|72,807|0|72,807|
|1,345|74,243|24,498|48,622|72,807|313|
|1,351|74,394|24,498|48,773|73,120|151|

- 两次增长请求的写入合计：97,395 → 464，减少 99.52%。
- 两次增长请求的读取命中率：32.96% → 98.18%。
- 增长请求输入费用估算：$0.05012662 → $0.00359974，降低约 92.8%；含首次冷写的三次总输入费用为 $0.08669252 → $0.04016564。
- 这只验证缓存效果，不外推整日节省比例。每组 3 例没有 Gold；两组仅 1/3 召回集合完全相同，其他两例存在模型采样差异，不能包装成业务准确率不变的验证。所有返回 Event ID 都通过冻结输入身份范围校验。官方文档说明缓存不保证相同输入产生相同输出。

## W1R3 与 W2R2 真实调用

总计 11 次真实调用：6 次 W1R1 对照、3 次 W1R3 medium/default、2 次精简后的 W2R2，全部通过结构校验；W2R2 额外核验候选 Policy/Condition 身份范围。测试使用临时导入的候选适配器，没有替换运行中服务文件、切换生产模型配置或写回业务结果。

- W1R3 三次实际返回的 service_tier 均为 `default`。其中两次用于不同测试缓存键的冷写，第三次复用第二次键，在不同 Case/父轮输入下命中全部 2,761 个固定前缀 token，cache write 为 0。调用约 22.0s、23.1s、13.6s；样本不足以评价延迟分布或 medium 的业务质量。
- W2R2 两个样本分别覆盖混合 HIT/RELATED 与纯 RELATED；移除字段后，HIT 标志、RELATED 标志、Policy ID、Condition ID 与历史回执逐项一致。两份 Policy 输入字符量减少 13.7%、17.7%，这是字符量比例，不是整次 token/费用节省比例。
- W2R2 第二个不同 Case 命中 2,756 个固定提示词 token，cache write 为 0，未把变化的 Policy/正文一起写入固定缓存。
- 两次测试脚本的 SecretStr 参数预检错误均发生在 HTTP 请求发出之前；修正测试脚本取值后再执行，未计入上述 11 次真实调用，也未隐藏失败的付费回执。

## 回归及兼容边界

定向回归：224 passed、2 skipped、3 deselected。两个 skip 是“无 fallback 转换时不适用崩溃恢复”的参数组合。

覆盖块推进、跨 100 行、超过 50 块、一次增长多块、固定前缀兜底、Haiku 布局不变、动态消息及修正不进入固定缓存、四个 R2/R3 私有输入隔离、历史冻结 W2R2 输入复用、字段投影不修改源 Policy、日索引/O2 后事实可见性及 W2 路由对齐。

另有三个既存的 policy-direction 用例使用 2026-10-10 的时间 fixture，被当前时效门槛判为 `RUNTIME_DELAY_EXCEEDED`。恢复修改前的 Policy 投影函数后，同三个用例仍失败；因此未把它们报为通过项，也未扩大本轮范围去改交易时效逻辑。详见 preexisting-failures.txt/xml。

已有冻结 Case 的模型参数仍以 frozen_inputs 为准，历史 W1R3 high/fast 不静默改成 medium/default；已有冻结 W2R2 输入不重新精简，父轮回放读取原冻结版本。新输入投影仅用于尚未冻结的 R2 请求。

## 文件与发布边界

- `src/doxagent/persistent_runtime_v2/openai_platform.py`：原生缓存布局。
- `src/doxagent/persistent_runtime_v2/future_model_profiles.py`：W1R3 默认模式与 medium。
- `src/doxagent/persistent_runtime_v2/service.py`：W2R2 投影。
- `tests/test_openai_cache_boundaries.py`、`tests/test_w2_r2_compact_policy.py` 及现有 Luna 测试：回归。
- `eval/runtime_cache_cost_repair_20261117/`：真实回执、验证结果、回归结果、隔离调用与取证脚本。

发布已同步 API 与 Scheduler 的完整 main 源码并重建对应镜像；新执行 bundle 的 `runtime_models.json` 仅将 W1:R3 改为 medium/default。历史 Case 冻结参数没有批量改写。前端、Codex Worker、消息采集及交易执行容器不需要因本次改动重建。

## 生产发布验收

- 源码提交：`86b1f9864a7ad7e516702d4a17512e4e0d6a68a4`，已推送 origin/main，包含全部待提交的开发代码、测试、报告、审计脚本及回测候选提示词；原始运行捕获和批量导出数据留在本地。
- 使用既有 `storage_release.py` 发布锁、固定依赖层及完整提交构建；新镜像 `sha256:3d70ee0fffef1b5feaec4f429cf90bc805816ab4c5ee50a700bb6ae957e590e7`。
- API 于 UTC 12:44:35（北京时间 20:44:35）启动，健康检查通过；Scheduler 于 UTC 12:45:03 启动，运行正常。两者 restart_count 均为 0。其余 29 个已有容器的镜像及启动时间保持不变，包括 Worker 和维护任务容器。
- 实际激活 bundle：`cd79babe37b8678185c3443137c8d2f9034ccbf29662322e13077e929b8677cb`。与发布前比较，唯一变化的模型轮次为 W1:R3，其他模型配置、提示词和业务契约不变；fallback 仍为 Haiku high / Google Vertex Global。
- 发布后四个新 Case 共 20 条自然 W1/W2 调用全部 OK，覆盖全部六个轮次；其中 4 条 W1R3 均为 medium，provider 实际返回的 service_tier 均为 default。另两条 W2R2 的冻结输入均确认已移除 transmission / trigger_layer，保留 match_scope。
- 三份生产模块源码哈希与候选镜像离线探针完全一致。验收证据见 `production-verification-final.json`、`host-verification.json`；没有把仅配置正确当成真实调用成功。
- 生产发布归档：`/var/lib/doxagent/releases/runtime-cache-cost-20261117/`。保存构建回执、发布前状态/Compose、旧执行 bundle、清理证明；保留当前和上一有效回滚镜像。

## 授权的构建缓存及镜像清理

先按现有台账清理过期构建缓存，首轮可用空间增加 29,458,432 字节（约 28.1 MiB），没有误删受保护镜像。新版本验收后，额外确认并退休一个已被当前 main 取代、无运行/回滚/模板依赖的旧候选镜像：`doxagent-v2:storage-c5d9759d9447`。

在线数据库持续写入，镜像又共享依赖层，因此没有用镜像展示大小或 df 瞬时差值宣称独立回收量。最后核查磁盘可用约 21.7 GiB。未清理任何数据卷、数据库或用户浏览器资料；所有清理错误列表为空。证据见 `cleanup-output.txt`、`cleanup-final.txt`。

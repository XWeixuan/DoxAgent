# W2R1 DeepSeek low 与分轮次配置发布回执（2026-10-17）

已提交 main、push、同步远端，并从已提交源码按标准 Dockerfile 完整构建。只替换 Scheduler，生产新 Case 已成功使用新设置。

## 生效配置

| 轮次 | 模型 | 推理强度 | 单次请求超时 | 结构化输出 |
|---|---|---|---:|---|
| W1R1 | qwen3.8-flash | low | 120秒 | JSON Schema strict |
| W1R2 / W1R3 | qwen3.8-flash | medium | 120秒 | JSON Schema strict |
| W2R1 | deepseek-v4.1-flash | low | 180秒 | JSON Object + 本地Schema验证 |
| W2R2 | qwen3.8-flash | medium | 120秒 | JSON Schema strict |
| W2R3 | qwen3.8-flash | low | 120秒 | JSON Schema strict |

180秒仅作用于 W2R1 单次请求；保留现有编排纠错重试与退避，因此整个节点含重试时可能超过180秒。没有把全部W1/W2一起换模型、降档或提高超时。

## 实现边界

- `transport.py`：每轮请求支持独立timeout；DeepSeek使用其支持的JSON Object模式，完整业务Schema继续注入输出契约并本地验证。明确schema关键词不是输出字段，非法字段仍报错并走已有纠错重试，未静默丢弃。Qwen仍使用原JSON Schema strict模式。
- `factory.py` / `service.py`：模型、effort、timeout通过 `runtime_models.json` 随Execution Bundle发布，再随Case冻结；每轮取对应覆盖配置。已在途旧Case没有新轮次映射，继续使用原模型/medium，不改写历史判断或回执。
- `schema.py`：回执允许low和JSON Object模式，成功/失败均记录本轮实际模型、effort、transport、timeout；旧回执缺省字段仍可读取，无需数据库迁移。
- 新增配置：`DOXAGENT_PERSISTENT_RUNTIME_V2_W2_R1_MODEL=deepseek-v4.1-flash`、`DOXAGENT_PERSISTENT_RUNTIME_V2_W2_R1_TIMEOUT_SECONDS=180`。W1R1/W2R3的low作为本轮明确策略随Bundle冻结；全局模型仍为Qwen、全局超时仍120秒。
- W2R2显式注入消息/Policy上下文，不继承DeepSeek R1响应；W2R3仍继承Qwen R2并保留原有上下文恢复。没有新增跨模型response继承。
- 未修改业务prompt/skill、路由、新旧事实判断、RELATED覆盖异常处理或交易规则。

JSON Object只保证JSON语法，不保证业务结构，故保留本地Schema校验与纠错；参见[百炼结构化输出说明](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。本轮也真实验证了部署账户对此模式和low推理的支持。

## 发布身份与范围

- 实现提交：`00acd74ac0c6f5b2087269cb271c63dea86127f6`。
- 生产镜像：`doxagent-v2:main-00acd74a`。
- archive SHA256：`1f7c29fdaa15e80bba75130280d5da516f4eb2701cdde4d455e89cc7945066d3`。
- 发布时间：`2026-10-16T19:00:25.252191+00:00`（北京时间2026-10-17 03:00:25）。
- 仅重建 `doxagent-v2-v2-scheduler-1`；其他生产容器ID与发布前一致。Worker、Message Bus、Site Access及其会话未重启。
- Scheduler切换允许210秒退出窗口，原数据库/卷不变。未批量恢复历史失败Case。
- 五份部署源码的SHA256与提交Git blob逐一一致，见 `verification.json`；不是容器热覆盖。

## 验证证据

125项相关回归通过：持久化编排、旧Case配置兼容、六个轮次映射、实际请求JSON模式/timeout、非法额外字段及失败回执、W2R3上下文与覆盖校验、W3、回测。最终传输适配变更后再次通过49项核心测试。

部署镜像首先通过无网络的传输探针，随后在新Scheduler容器内执行一条原20Case中的历史W2R1隔离真实请求，不写业务Case/Effect、不触发交易：

- response_id：`resp_7ea3c334-286b-9c50-87c5-a8362a42c9cd`；耗时 `12.858`秒。
- 实际wire：`model=deepseek-v4.1-flash`、`reasoning.effort=low`、`timeout=180`、`text.format.type=json_object`。
- 输入 `33440`、输出 `467`、其中推理 `369` token；结果通过原W2R1 Schema和冻结候选Policy身份校验。
- 当前激活Bundle：`d87f7104d58b5fdc7a5a0f4d847ca09ff646d4850d3d032f303bb8106bb8ad4b`；数据库只读确认其 `runtime_models.json` 与表中配置一致。

生产首条自然Case：`case_55deff6705b44d8a9e9fce223013f098`。

| 轮次 | 状态 | 模型 / effort | timeout | 耗时 |
|---|---|---|---:|---:|
| W2R1 | OK | deepseek-v4.1-flash / low | 180.0 | 12.144秒 |
| W1R1 | OK | qwen3.8-flash / low | 120.0 | 30.434秒 |
| W1R2 | OK | qwen3.8-flash / medium | 120.0 | 13.154秒 |

该Case的W2R1无候选，不执行R2/R3。W2R3 low已通过分轮次配置/上下文回归及生产激活Bundle核对，但本轮自然验收未覆盖一次实际R3调用，不把这部分描述成自然样本已通过。

## 磁盘与留痕

完整构建前可用约6.3GiB，镜像解包后降至约330MiB。已核查并只回收本轮两个精确ID、未使用且非共享的构建上下文缓存（约1.094GB），恢复到约1.4GB可用；没有删除业务数据、镜像或其他任务缓存。磁盘余量仍较低，本轮未扩展到生产存储治理。筛选语义参照[Docker buildx prune说明](https://docs.docker.com/reference/cli/docker/buildx/prune/)。

本轮原始构建/发布/清理/真实调用/自然回执与源码校验在 `eval/w2r1_release_20261017/`。`cache_cleanup.json`保存首个带布尔filter的0B无效清理，实际回收证据为 `cache_cleanup_retry.json`（嵌入verification.json）；未将无效操作当成回收成功。

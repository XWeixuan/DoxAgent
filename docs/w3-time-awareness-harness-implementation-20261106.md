# W3 Time Awareness Harness 开发记录

日期：2026-11-06（北京时间）。本地开发，默认关闭，未部署生产。

## 实现范围

按 [实施方案](../dev_plan/w3_time_awareness_time_allocation_harness_plan_20261106.md) 完成 Python 接入与 Codex 0.159.3 原生小补丁。只对新受理的 W3 `trade_decision` 注入；Template、Operations 和其他节点均不采用此能力。

- 在确认 Trade Decision 需要模型后、准备研究与 Thread 之前记录真实 UTC 起点。使用现有 Case KV，跨重试、进程恢复沿用；业务 cutoff 和回放日期不参与运行耗时计算。
- 初始起点写入遇到 SQLite 写锁时立即放弃本次可选计时；派发只读取，不在后面补建一个偏晚的起点，也不使业务任务失败。
- 每个 runtime 使用一份小控制记录传递机器元数据。原生 Turn 开始时读取并消费，随后使用 UTC 初始偏移加 `Instant`；每轮不读文件或数据库。
- 复用原生 developer fragment 和历史追加链路，在决策采样及流式恢复请求前更新 Elapsed，按 120/180/240s 加强软提醒。没有新工具、模型请求、轮询、阶段调度或取消分支。
- 当前 Turn 的时间片段保持追加前缀；普通历史渲染和本地/remote compaction 默认排除本能力片段，防止同 Thread 的 Operations、其他 Case 或摘要继承停止调查提示。
- `≥240s` 提醒保留现行业务合同的必要提交核验，不改方向、交易门槛、PIN/WATCH、模型 profile、输出 Schema 或既有硬超时。
- 新执行协议字段为空时不序列化，保留旧冻结请求与持久化 job 哈希。显式提供不同计时元数据仍受原有幂等校验约束。

没有修改业务 `AGENTS.md`、`skill.md`、合同 JSON、模型路由、交易执行源码、`pyproject.toml` 或 `uv.lock`。29 份受保护文件的摘要与开发前一致。现有 Case 账本没有独立删除 owner，因此没有新增清理器；小起点记录与 Case KV 一同保留。

## 构建与使用

原生补丁固定于上游 `rust-v0.159.3` 的实际 commit `01fc69f4026735edfdf6789820549727a4867b11`：

- [补丁](../deploy/codex-patches/w3-time-awareness-0.159.3.patch)
- [Linux 构建定义](../deploy/codex-patches/Dockerfile)
- [构建命令封装](../scripts/build_w3_codex_runtime.py)
- [产物与开关说明](../deploy/codex-runtime/README.md)

```text
uv run python scripts/build_w3_codex_runtime.py
```

构建导出 `deploy/codex-runtime/codex`、`manifest.json`、上游 License 与 `symbols/codex.debug`，生成文件不提交 Git。调试符号单独保留并从运行镜像上下文排除；运行二进制约 266 MiB。`Dockerfile.v2` 复制到 `/opt/doxagent/codex-runtime` 并明确设置执行位，避免 Windows 导出文件权限丢失。选择自定义二进制时保留 SDK bundled helper PATH；调用者显式指定的二进制仍优先。

上游 release 标签将 workspace 版本改为 0.159.3，但 Cargo.lock 的本地 crate 版本仍为 0.0.0。构建准备仅归一化无 source 的本地包版本；归一化文件与 Cargo 自身产生的锁文件逐字节一致，第三方依赖没有变化。随后使用 `--locked` 构建；manifest 保存 commit、patch/lock/binary SHA256、架构与 rustc 版本。

两个设置由服务 env_file 提供，Compose 不覆盖它们：

```text
DOXAGENT_W3_TIME_AWARENESS_ENABLED=false
DOXAGENT_CODEX_RUNTIME_BIN=/opt/doxagent/codex-runtime/codex
```

需要开启时将第一个设置改为 true；本轮没有修改实际 `.env.v2`。离线 curated 回测增加 `--time-awareness`，开关进入 run identity，旧 manifest 缺少字段按 false 处理。

**回退先关闭开关并保留补丁二进制的历史过滤。** 含有本能力历史记录的 Thread 不应直接切回不认识该分类的旧二进制；待这些 Thread 按原生命周期退役后再撤换二进制。不删除原生历史，不取消在途 Turn。

## 定向验证

证据保存在 [本轮审计目录](../artifacts/audits/w3_time_awareness_20261106/)。

| 验证 | 结果 |
| --- | --- |
| Python 编排、合同、Ready Slot、Thread 归属、回执、时间边界、模型路由及回测 | 169 passed，1 项已复现的改前失败单独排除；`python-regression.xml` |
| 最新计时与旧 job 哈希兼容专项 | 9 passed；`python-time-awareness.xml` |
| Rust 新计时/历史专项 | 3 passed；`native-unit-tests.log` |
| Rust 历史处理回归 | 90 passed，包含新增历史专项；`native-history-tests.log` |
| Rust 原有 CurrentTimeReminder / 相关 Fork 回归 | 28 passed；`native-current-time-tests.log` |
| 真实 app-server + 官方 Python SDK + 本地模拟 Responses | 2 passed；覆盖真实工具续轮、流式恢复、阈值、追加前缀、Fork/Resume、压缩与 Operations 隔离；`native-sdk.xml` |
| 最终 stripped release 二进制的相同 SDK 链路 | 2 passed，4.59s；`native-release-sdk.xml` 和 `native-release-receipts/` |
| Linux 打包烟测 | 版本 0.159.3、二进制摘要、0755 执行位与 symbols 排除均通过；`package-smoke.json` |
| 新代码 Ruff 与两份新增运行模块的严格类型检查 | 通过；涉及的旧大文件仍有与基线相同的 9 项诊断，没有引入新诊断；`ruff-comparison.json` |

原生执行了仓库要求的 `just fmt`；Rust 格式化通过，整仓其他格式组因容器未安装无关的 uv/dotslash 未完成，另完成 `cargo fmt -p codex-core`。`just fix -p codex-core --lib` 完成；它修改的上游无关 warning 修复未纳入交付补丁。未运行整套 Codex workspace 测试。

唯一排除的旧回测是 `test_replay_uses_production_two_stages_with_isolated_evidence`：期望 READY，实际 RUNTIME_DELAY_EXCEEDED。在改前 HEAD 的独立源码副本中同样失败，未为通过测试修改业务时效规则。见 `baseline-reproduction.json`。

## 真实模型配对烟测：不构成提速验收

使用当前业务 Prompt 的隔离副本、同一冻结证据与行情，`gpt-6-luna/max`，Web 关闭，Operations 不运行。共四例、八次实际研判成功；时间开关两组方向全部一致，均为 NO_TRADE，理由仍围绕 ticker 传导、预期差或已有基线。没有真实订单或生产 Case 写入。

| 样本 | 关闭：全流程秒 | 开启：全流程秒 | 开启组在请求前实际注入的 Elapsed 秒 |
| --- | ---: | ---: | --- |
| COHR / NVIDIA-SPIL 扩产 | 77.81 | 70.93 | 2, 5, 8, 10, 13, 16, 20 |
| COHR / Amazon Q3 | 80.51 | 77.59 | 2, 9, 13, 23, 43 |
| COHR / MPWR Q3 | 79.60 | 73.08 | 3, 7, 12 |
| MU / HBM 2027 价格预测 | 108.17 | 165.34 | 2, 11, 15, 18, 25, 28 |

全流程秒包括回测准备/Template 等开销，不能与单条 Elapsed 直接相减。关闭组没有原生时间片段；开启组采得 21 条片段，每条按 o200k_base 为 18 tokens。原始结果、业务 Prompt 摘要、debug binary 摘要及时间片段见 `quality-receipts.json`。这不是单次真实请求的缓存命中率或费用测量。

**没有证据证明整体提速。** 四例均未在下一次模型请求前跨过 120s，因而没有收到动态行为提醒；MU 在最后一个请求之后耗时较长，也没有新边界可以更新提醒。其开启组更慢，不能只报告其他三例的下降。真实 LONG/SHORT、Sol profile 及动态提醒档位的投研效果仍未由本组样本覆盖；目录中的 LONG 标注不等于当前基线模型实际输出 LONG。

动态档位及不中断请求由模拟 Responses 的真实原生链路验证；四例实际模型只证明真实 Elapsed 注入贯通和有限样本方向一致。不要把机制通过、两个方向值相同或较低的中位数称作“质量不下降且时效目标通过”。后续效果验收仍需要更多代表性配对样本。

## 交付状态

业务代码、原生补丁、Linux release、构建/打包接入、回测开关与定向验证已完成。默认关闭，未部署生产，也未完成真实动态提醒的提速/质量验收。

最终 x86_64 产物使用 rustc 1.95.0：

- binary SHA256：`b1db9192311ecd1516723a0f81aceae1c3fd95f01b1afa3ce909aee35a00b695`。
- patch SHA256：`56106b8fad08dd32b579dd54fbf21b124edb6716ba011f0fbdea8ec330ebb975`。
- 完整摘要与符号文件摘要见 [manifest](../deploy/codex-runtime/manifest.json)。

标准 release 构建含上游完整 CLI 与 thin-LTO，本机用时约 37 分钟；这是一次性构建成本，不是本功能运行时开销。已经保留编译日志、独立调试符号与最终 stripped 产物。最小打包烟测基于本机既有后端镜像验证二进制加载、权限和目录规则，没有构建或发布完整生产版本。

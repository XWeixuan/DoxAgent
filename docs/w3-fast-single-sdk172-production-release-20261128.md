# W3 快速单任务：SDK/Native 续接与生产发布回执

日期：2026-11-28（北京时间）。承接 `docs/w3-fast-single-implementation-20261127.md`。已完成 SDK/Native 配对升级、Worker/Scheduler 正式发布和快来源启用；技术与最小真实调用验收通过，真实快来源业务质量/速度仍需自然样本评估。

## 业务目标及冻结边界

| 来源 | Trade | Operations |
| --- | --- | --- |
| `sec_edgar`、`corporate_ir_press_releases` | 一个真实 W3 Task，gpt-6.2-sol / high / fast | 一次 gpt-6-luna / max / default，同一 Case Thread |
| 其他来源 | 原 dual_consensus，两个 gpt-6-luna / max / fast | 一次 gpt-6-luna / max / default，原共识代表 Thread |

来源名单只通过 Scheduler 的 `DOXAGENT_W3_FAST_SOURCE_IDS` 配置。已冻结 Case 不重分类；全局双任务值保留。Template、Capsule、私有 READY 继续使用 Luna 准备 profile，不建设 Sol 池，不因 Trade 模型变化重建 Case Thread。Prompt、Skill、输入/输出合同、交易规则、隐藏 W2 的配置及 Operations 职责均沿用实际生产版本。

## 本轮补齐的实现

- 原路由/单双任务代码已在生产，只差新版 SDK/Native 和重新开启来源名单。本轮 SDK 与 CLI 包都固定为0.172.0，Native固定到上游 `3d86e43b075adeba2193ec5c22ac92f55afd82e6`，同版编译 `codex` 与 `codex-code-mode-host`。
- 将原0.159.3的 Elapsed 逐轮提醒、Operations/压缩历史过滤、诊断日志减量及有证明的孤立根定向清理迁移到0.172。保留旧构建文件和补丁，禁止混搭新 SDK 与旧 Native。
- 原迁移稿遗漏了新版定制V8资产配置：默认Deno下载地址返回404。采用同一固定Codex提交 `MODULE.bazel` 的 OpenAI ptrcomp+sandbox产物及SHA256，不修改V8版本、不关闭沙箱。[固定上游配置](https://github.com/openai/codex/blob/3d86e43b075adeba2193ec5c22ac92f55afd82e6/MODULE.bazel)
- 正式构建检查Native版本与项目SDK pin一致，核对两个可执行文件的SHA及配套patch。镜像增量保留原运行用户。
- 发布脚本基于每个角色**实际运行镜像**构建；升级依赖和Native，不替换业务源代码。对全部 `src/doxagent/**/*.py` 计算发布前后摘要，避免回退刚上线的正常O2、边界事实和精确历史引用修复。角色镜像/配置若在候选构建后变化，重新检查，不盲目覆盖。

关键文件：`deploy/codex-patches/Dockerfile`、两份0.172补丁、`prepare_build.py`、`deploy/Dockerfile.codex-sdk-upgrade`、`pyproject.toml`、`uv.lock`、`scripts/ops/storage_release.py`、`scripts/ops/w3_sdk172_release_20261128.py`。

## 构建及回归证据

本地C盘清理后可用约28GiB。Docker恢复后发现旧Rust缓存中的动态库损坏；改用独立本地BuildKit重新拉取固定工具链。Rust编译全部在本地，未向生产服务器引入Rust构建。续编采用单构建并行降低链接峰值内存，保持同一release优化参数；这不改变生产实时并发。最终主二进制release构建耗时38分06秒并成功完成；随后本地调试符号导出耗尽剩余空间，使导出命令失败。运行产物已完整落地，分别独立核对manifest SHA通过；只删除本轮导出的调试符号副本释放空间，没有重编译或替换为官方未定制二进制。发布不含符号文件，manifest中的符号摘要不能解释为本地符号副本完整可用。

运行产物：codex SHA256 `75a118a3041b7fa948695132e726ba05dc8e7b8d3dcf2309e27439db3cc0ffac`；同版host SHA256 `989024d723f0c0e6c6afe0b0b296e2f6206719df90ba4b10f1863bd0708d94bc`。上传代理速度异常后改用已验证的SSH直连，仅传送两个可执行文件、manifest和LICENSE，共约159MB压缩包。

Python测试明确使用隔离树源码及临时安装的0.172 SDK，主开发虚拟环境未改动：

- 路由、共识、时间提醒、发布和共享SDK组：96通过；另2项Document3 Pilot归档断言失败。在0.159.3重跑同两项仍失败，均为归档中找不到预期 `document3.json`，不算本轮SDK回归或通过。
- Worker、恢复、权限、时序和付费回执组：164通过，1项旧公共准备夹具缺 `running_mode`。只给夹具补齐FULL，保留“不重复写marker”的原断言，失败单例随后通过；生产公共准备逻辑未改。
- 正式发布组17项通过，含SDK/Native新旧版本配对及混搭拒绝。此组与上一组存在重叠，不累加冒充独立测试量。
- 最终自定义Native机制组6项全部通过，10.01秒。使用最终Worker候选镜像、隔离CODEX_HOME和本地合成HTTP/MCP服务，禁网、无生产数据挂载、无付费模型调用。覆盖时间提醒/重试/阶段过滤、Fork/Resume/压缩、同版code-mode-host、本地MCP往返及精确根退出。新版Native实际暴露namespace形式的MCP工具，合成SSE夹具据真实工具目录调整；生产MCP合同没有改动。

## 发布与验收状态

代码已提交至origin/main，发布代码提交 `636d56e73861dec8f741ef961aeb965f3849ab89`。正式owner分别完成Worker、Scheduler发布；生产运行Python SDK、CLI依赖和自定义Native均为0.172.0，双可执行文件SHA及所有业务Python源码摘要再次核对通过。远端Git引用已更新，Gitignored Native构建输入也同步到已验证的0.172配对，避免后续main构建继续捡到0.159 Native；没有重置远端或主开发区的并行修改。

| 角色 | 实际镜像SHA256 | 容器启动（北京时间） |
| --- | --- | --- |
| Worker | `b5d728aa7451e8bc767eaf04d22a702d0fd9ae58001933d353b2885a90b50d3d` | 11月28日03:17:09 |
| Scheduler | `26191e309aa087986f475e577f81e9268af31560994b82901a60ca30fc75824e` | 11月28日03:19:33 |

先前后台D1任务持续运行时，临时暂停初始化容器派发，Worker上的付费研究与实时W3继续运行。等原Job `9ad8f6fb…` 自然成功且Worker无在途任务后，正式owner协作停止Scheduler并切换Worker；没有取消付费Job。Worker验证后恢复原Scheduler与初始化容器，随后完成暖线程canary，再发布Scheduler。初始化容器已恢复、原Job保持succeeded且无error_code，两角色重启计数均为0。正式发布窗口内保留原租约/Job/Thread恢复机制。

### 真实暖线程canary

实际Worker认领Luna READY为 `POOL_HIT`，42ms；同一物理Thread `01a21e71-f085-7a52-8554-0e27b78c2771`，没有Sol池或按模型冷准备。

| 阶段 | 模型/推理 | 持久回执中的实际Tier | 整轮墙钟 | 结果 |
| --- | --- | --- | ---: | --- |
| Trade探针 | gpt-6.2-sol/high | fast | 3.020s | succeeded |
| Operations探针 | gpt-6-luna/max | default | 2.220s | succeeded |

两个Job分别为 `8c7478d3381d4146ea7ec434d4d7a71f08a0e272488c9741cf9144d20982acab`、`4c997576ce89366209358e97b5aab71e482dc8ed4e27b9a98fc2323121c64eb3`。Tier来自实际Job的 `resource_receipt.capsule_startup_timing.sdk_start.milestones`，不是仅查看环境变量。两轮只返回 `ready=true`，不调研、不使用业务工具、不提交交易；已ACK及释放resident。

### 路由、资源与既有业务复核

- 实际Scheduler配置仅开启 `sec_edgar,corporate_ir_press_releases`；hide_w2仍为true，全局仍dual_consensus。隔离内存journal验证四来源矩阵及冻结后重入：SEC/IR为Sol/high/single，IBKR/Yahoo为Luna/max/dual；准备和Operations均为Luna/max。测试没有写生产Case。
- 新Worker恢复后真实库存补齐为6份Luna READY，没有Sol库存；初始化服务已恢复。没有为本次发布降低实时并发或新增长驻服务。
- 发布后四个自然普通来源样本均已完成Trade和Operations提交：INTC `case_e05cc370b1264fc5a73c4019e6fd932b`、MU `case_3b73b3911bf845998ebffcbb58526c0f`、COHR `case_737139d115884b19ba77005a509bc213`、NBIS `case_2507eb8b7f7f43b79cba623a0eb1d0d2`。实际A/B回执均为Luna/max/fast，各Case只有一次Luna/max/default Operations。由16个真实付费Job的持久参数和两阶段提交状态确认普通通道未被改变，不将其当成SEC/IR质量样本。
- 前轮HISTORY恢复的56份Operations仍全部COMMITTED，原Job身份及applied标记56/56；953个SUPPLEMENTAL仍收敛、无剩余补充任务。复核窗口未发现新HISTORY_RETIRED；未改变未处理Fact义务、语义日边界或正常维护机制。

本次没有等到新的SEC/IR自然研判样本，不能把最小canary等同于真实快通道业务速度、质量或完整Runtime P95达标。固定来源矩阵、真实暖线程调用、实际逐轮Tier已验证；后续质量与时效应按自然快来源Case另行评估。

本轮开始时的真实基线：Worker `543c6014d830…`；Scheduler `f5065bbc0f7c…`（be4500fef）；现有6份Luna READY、没有Sol池；全局dual_consensus；Scheduler快来源为空，hide_w2为true。两端运行用户均root。

候选、私有compose及发布回执由远端正式release owner管理：`/var/lib/doxagent/releases/w3-sdk172-636d56e73861/`，包含build、两角色deploy、Native六项JUnit、post-verification及natural-sample回执。不把包含环境凭据的manifest放入仓库。无敏感构建/机制回执同时留在本地 `artifacts/releases/w3-sdk172-20261128/`。

本地独立Rust构建缓存仍占用Docker磁盘，删除导出符号及上传包后C盘约剩0.9GiB；未扩大清理范围到其他构建/业务卷。后续本地大构建前仍需清理或迁移本轮构建缓存。本轮正式发布不依赖本地Docker继续运行。

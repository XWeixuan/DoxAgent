# W3 Elapsed 100/150 秒阈值发布

2026-11-19。代码提交 `7b071ab92`。

## 变更

- Elapsed <100 秒：只提供耗时。
- 100—149 秒：提示评估研判是否充分，充分则尽快提交。
- 150—239 秒：提示仅继续可能实质改变结论的高价值研究，尽早提交。
- ≥240 秒：沿用停止新增调查、完成必要核验后提交的提示。

提示文本、交易标准和单次模型响应期间不能中途追加提示的能力边界保持原样。本次同时发布本会话前一轮完成测试的非阻塞上下文复用及内存兜底修复。

## 验证与发布状态

**已于北京时间 2026-11-20 01:31 完成生产切换并验收。** 阈值源提交 `7b071ab92`；发布合并版本 `a295d9584f4eb9d93e36b13c3f59e9d3099523e5`，保留等待期间已发布的 Event lineage、W1 standard tier 和 W3 独占事实编译修复，以及当前仓库的其他已发布改动。

- 新二进制原生测试 **4 passed**：真实 SDK + 本地 mock 服务检查 99/100、149/150、240 秒、Code Mode、工具续轮、重试、Fork/Resume、压缩与异常后恢复。没有真实模型回放或交易操作。
- 合并当前 Runtime 改动后的定向回归 **63 passed**。此前非阻塞修复的 221 项回归结果仍保留在实施记录中，不冒充本次重跑。
- Worker 和 Scheduler 均运行镜像 `sha256:446311f9d7c8ca13a281c2f7676000a0044289f36250d19ba0d0860b52b7dd43`，源码四文件、发布 commit、开关与二进制 hash 全部核对一致，Worker healthy，Scheduler running，restart count 均为 0。
- Harness SHA256：`e02643427c358d38fd825fdedac223b10faf4b0183f26ad1a3ec978b1fc4720e`；补丁 SHA256：`3ea16e781030712fd678f5d7208b1ea76bffe740babb5a24fb9ed484c711b6db`。
- 暂停派发入口、等待已受理 Job 与 Scheduler 结果 owner 排空后才替换，随后恢复全部原运行派发服务。没有取消已受理模型调用。
- 首个自然观察窗口中，两例 Case 的 **4 条已完成分支**均有实际原生 Elapsed；另外四条仍在准备，未计为成功或缺失。自然完成样本没有跨 100/150 秒，档位边界由原生 mock 请求验证，不能混写为生产高档位统计验收。

证据：[线上版本核验](../artifacts/audits/w3_elapsed_reliability_20261119/production-verification.json)、[自然分支回执](../artifacts/audits/w3_elapsed_reliability_20261119/production-natural.json)、[原生测试](../artifacts/audits/w3_elapsed_reliability_20261119/threshold-qa/qa/native.xml)、[合并回归](../artifacts/audits/w3_elapsed_reliability_20261119/merged-regression.xml)。

本地第一次原生编译造成 C 盘空间耗尽，Docker 停止。已清理仅属于本次构建的 target/registry 缓存、执行文件系统 trim 并恢复 Docker；临时移到 D 盘的原始 runtime 两个文件已移回。随后改在服务器低并发、低优先级构建，设置 8 GiB 磁盘余量保护。该事故发生在本地，当时未切换生产。另遇 V8 150.4.0 预编译归档 404；主 Harness 完整重编译，未修改的 Code Mode companion 复用生产已验证的同版本官方 0.159.3 wheel，SHA256 `fe64bd08c7844ca1fca91d8cb825e62012a03a05ae8040c635bbb61e5be0b4c5` 保持不变，并通过实际 Code Mode 原生测试。构建 recipe 和回执留存在服务器发布审计目录。

## 空间清理

先为构建余量清理超过24小时和1小时的未使用旧缓存，Docker 分别报告 1.264 GB、9.229 GB；验收后清理全部未使用构建缓存，再报告 23.58 GB。累计工具报告约 **34.07 GB**，不等同于净文件系统增量。

最终清理阶段实际可用空间由 12.09 GB 升至 **34.44 GB**，净增加 **22.35 GB**；删除了重复导出可执行文件及本次临时构建目录，保留当前 canonical 原生构建输入、调试符号、QA 与发布审计回执。当前镜像、回滚镜像、所有业务卷及数据库均保留，清理后确认生产容器仍运行原已核验镜像。

[清理回执](../artifacts/audits/w3_elapsed_reliability_20261119/server-cleanup.json)。服务器审计目录：`/var/lib/doxagent/releases/w3-elapsed-a295d9584f4e`。

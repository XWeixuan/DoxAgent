# Bus 休市临时采集与 8 核配置恢复发布回执

北京时间 2026-10-26，代码提交 `450c17d4b33e185456c18351759fba766cccbc84`。本地 main、origin/main 与远端已统一同步；生产从精确提交重新构建 backend/site 应用镜像，通过标准 storage_release manifest/deploy 滚动四个受影响组件。最终检查约 01:54，不做长时间观察或性能目标验收。

## 整合范围

除本功能外，一并审查并提交其他会话未提交的 W3 AGENTS/skill 修改、BE 重初始化 7 个运维脚本和审计文档、原 changelog 条目。基线 `575bca85` 中的 SEC 等其他开发全部保留，Git 无分叉和冲突。未修改输出 schema 或重跑/激活 BE 研究；历史 eval、exports、模型工作区和私有生产配置保留原地，没有覆盖或丢弃。

本轮只涉及后端与 Site 客户端接线、生产容量及 scheduler 加载的 W3 资产；前端、Chrome Supervisor 和其他无代码变更角色沿用原镜像，不做无关容器重建。

## 实际部署

| 组件 | 应用镜像 SHA-256 | 结果 |
| --- | --- | --- |
| Bus、BODY、scheduler | `991ea503fffc84987ed30c6e714a30c2871c52a8170525321f6eafbfa2fc5966` | 代码均为 450c17d4，running，重启计数 0 |
| Site Access | `78d00dcb5e2abb220313619acccee7b35246b29d7858ca0ca37c928271ddb0da` | 450c17d4，running / healthy，重启计数 0 |

原数据卷、NativeContent、配置、Cookie、profile、出口与物理 Chrome 保留。Supervisor 容器 ID/启动时间完全一致，两个 CHALLENGE 受保护 target 均保留；Site 和身份配置 revision 列表一致，source/binding 配置摘要一致。所有检查到的 Compose 组件均 running，有 healthcheck 的组件未出现 unhealthy。release-resources.timer 为 active。

W3 新执行资产已由 scheduler 发布，execution 为 `1e140cd16d588450c1fae255ba6780be978d1feb4a028e1d5c05a5c943930284`，AGENTS/skill 与新镜像文件逐项一致。检查时 w3_entry.pending 指向该 execution；没有强行绕过 ready/旧 Case 冻结门禁，也没有为验收制造交易 Case。这证明新资产已发布，不代表新 W3 真实业务 Case 已验收。

## 恢复 8 核容量

实际主机 8 核、RAM 15,977,200KiB（约 15.24GiB），最终 MemAvailable 8,904,404KiB（约 8.49GiB）；swap 总量约 2GiB、剩余约 1.10GiB。先前发布 overlay 中明确全局浏览器 4 为旧 4 核适配，已改为 **8**，且实际容器环境与持久 release Compose 均生效，生产源 Compose 默认也为可覆盖的 8。

BODY 常规 2、无条件借第三槽 false 属于 8 核方案中的 P0/BODY 公平预算，继续保留；Site/source 原先自适应上限已为 8，无额外旧 4 核阈值待恢复。具体身份仍使用各自已验证能力，未借 CPU 升配提升站点访问能力。消息相关角色 NanoCpus 为 0，没有旧 4 核 CPU quota；Bus/Site/Supervisor/BODY 等相对权重保留。

单次 docker stats（100% 表示一个逻辑核）记录：scheduler 226.85%、Bus 54.57%、Site 52.26%、BODY 6.14%、Worker 103.38%、projector 97.14%。同次 CPU PSI some avg60 9.99%、full 0；这是动态瞬时检查，不作为持续负载改善或时效目标达成证明。

## 开关与采集链路检查

默认关闭。生产对 `MU / finnhub_company_news` 做了一次有两分钟截止时间的真实持久控制测试：revision 1 开启、重新创建 repository 后读取 ACTIVE，再于约 65ms 后以 revision 2 提前关闭。最终状态 DISABLED / effective=false，关闭原因和原期限/范围完整保留；没有留下持续临时采集授权。生产仅验证控制接线，真正休市发请求/截止停止的覆盖依离线固定时钟和真实 scheduler/transport 回归，不将这次短开关描述成端到端性能样本。

发布前已完成 capture 135,129；最终 135,953，当前快照均为 COMPLETED，无 pending/running capture。Bus 与 Read 游标继续推进，12 个 IR 内部端点最近均 succeeded。正常休市日历继续生效，IR/SEC 保持原 7×24；没有注入历史生产新闻或改共享日历来制造成功。

仍有日志边界：Bus 历史 failure_since 字符串的 Pydantic datetime 序列化警告；Runtime 有约 0.2–3.8 秒事务等待；W3 housekeeping 仍输出 failed=True 周期标记。它们未阻止本次基础发布/控制检查，本轮未展开独立根因排查，也不宣称全部后台业务健康或 P0/P1 已达标。

## 测试与证据

- [最终相关回归](../eval/bus-collection-override-20261026/regression-final.xml)：122 通过，包含开启、提前关闭、到期、范围、共享分发名单、重启读取、下一交易日衔接、资源等待后停止新请求、持久接纳不继承关闭门禁、checkpoint 与正文任务复用、既有去重/SEC/IR。
- [W3 整合](../eval/bus-collection-override-20261026/w3-integration.xml)：28 通过；[资源/客户端](../eval/bus-collection-override-20261026/resources-client.xml)：22 通过；RPC 重试门禁补充后[客户端复核](../eval/bus-collection-override-20261026/client-final.xml) 6 通过，后者与前者部分重叠，不重复累计。
- Ruff F/I、Git diff check、Compose 8 页配置解析和 BE 脚本编译检查通过。未修改前端，不执行无关前端构建。
- [脱敏运行与保留检查](../eval/bus-collection-override-20261026/release-summary.json)、[生产控制测试](../eval/bus-collection-override-20261026/production-control-smoke.json)、[W3 资产检查](../eval/bus-collection-override-20261026/runtime-assets-smoke.json)。[使用说明](message-bus-temporary-collection-20261026.md)。

远端私有回滚/部署回执位于 `/var/lib/doxagent/bus-collection-20261026/`；构建和部署日志为 `/tmp/bus-collection-build-20261026.log`、`/tmp/bus-collection-deploy-20261026.log`。按标准 release previous 镜像和保存的 Compose 回滚对应组件，不整库恢复、不删除 checkpoint/文章/正文/修订/Stream。新增 Bus 控制表为兼容增量；旧版本忽略该表。

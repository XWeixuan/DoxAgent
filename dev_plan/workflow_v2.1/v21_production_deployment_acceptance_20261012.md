# V2.1 生产部署与研究资产迁入交付记录

日期：2026-10-12，北京时间。依据 `v21_production_deployment_and_research_adoption_plan_20261012.md` 实施。用户在收尾阶段明确要求停止过细验收；本记录区分已完成事项和保留限制，不把未执行项目写成通过。

## 结果

V2.1 已进入新加坡现行生产部署，继续采用原账户和 PAPER_TRADING。MU、BE、INTC 的 D1/D2/D3 已通过正式四节点 activation DAG 整体激活，三套运行记录均 SUCCEEDED，Bus 与 Runtime 的 ACK 与各自新 revision 一致。没有重跑 D1/D2/D3、O4 或 Event Library，没有整库替换生产数据。

| Ticker | 新 D3 版本 | Policies | 保留的生产 Event 版本 | 激活结果 |
|---|---:|---:|---:|---|
| MU | 22 | 262 | 39 | Bus / Runtime 已确认 |
| BE | 16 | 124 | 21 | Bus / Runtime 已确认 |
| INTC | 19 | 173 | 32 | Bus / Runtime 已确认 |

正式 run 为 `adopt-v21-<ticker>-20261012-<源 SHA 前12位>-d3`。MU 来自已完成的 resume1；INTC 使用经四项最终编辑修复的派生 release。源库、源发布文件及原研究版本保持不变。研究来源 Event v1 仍保留在 PolicySet 中，与生产活动 Event 引用分别记录。

资产验收、INTC 四项具体修改与研究质量边界见 `v21_research_adoption_quality_acceptance_20261012.md`。三套仍保留 PARTIAL 和实际剩余研究缺口，没有改成无缺口的 COMPLETE。

## 发布范围与实际调整

- 后端基础镜像：`doxagent-v2:v21-production-20261012-r1`，源清单 SHA `bbfac491b188ca804e0a90fc50126ed3994d1ef9aff668404a40a294b5d1dd80`。SDK 0.159.3、IBKR 依赖及既有线上交易/消息源修复一并保留。
- 前端：`doxagent-v2-web:v21-production-20261012-r1`，由同合同构建产物发布。
- API / Projector 收尾修复：`doxagent-v2:v21-production-20261012-r2`，以 r1 为父镜像，仅覆盖 `api_v2/views.py`、`v2_read/cli.py`；冻结源清单 SHA `d496d4aac72ceab35a2ee5412e965918dca4c3a1c387aa8419ee437f5d4e1952`。其他后端仍为已验证 r1，合同与业务实现一致。
- 更新 Codex Worker、初始化、Guardian、O4、API/Web、Runtime scheduler、Projector、delivery、executor、control、CDECR executor 和 Message Bus。正文补全恢复原容器，Site Access/Chrome 未换镜像和卷；独立 RKLB 测试未停止。
- 修复 activation commit CAS；研究替换显式保留现行监测配置，跳过 prepare/install 的配置重写副作用。
- 选择性迁入 D1/D2 bundle、发布正文和引用记录；为 Worker 物化被接纳 attempt 的 D1 文件及最终 D2 handoff 文件。迁入 D3 四文件、staged baseline、coverage、必要证据与维护状态，排除可再生成的巨大 materials 缓存。
- 允许 SQLite 本地大正文，保留远程 inline 限制；新版 MAINTAIN 按基线 D2 引用准备输入；revision 3 执行包冻结 atlas、W3 资产和 native 合同。
- 增量更新 CDC，仅补方向使用/WATCH 命名空间，不回放全部历史 runtime_values。此次对应历史数据补写量为 0；未重置旧消费账本、WATCH、消息游标、交易和订单。
- 同步 W3 显式 Condition、WATCH、方向消费及已有 NEW-fact 合同的必要提示；加入共享 CaseSummary 缺失的 DUPLICATE_EVENT_TRADE 枚举并重建 wire schema。
- 发布中补齐历史 Compose 缺失环境文件，使用 `env_file: !reset []` 和 `environment: !override` 冻结各容器原有有效环境，保留挂载与其他部署参数，避免现行环境文件额外注入参数。每个发布容器均核对原环境集合和挂载元组一致。
- 验收发现页面快照组装长期占写锁、Projector 在短暂 SQLite 争用后退出：改为只读组装快照、最后短事务保存令牌，并仅对 BUSY/LOCKED 在下一 tick 重试。没有扩大请求超时、吞掉其他错误或重建读库。最初关于日历冷启动的判断已撤回：代码原有预热；独立冷进程 probe 不能证明查询池超时的实际根因。

## 保留核验

`preservation-proof.json` 对照维护窗口前后的真实生产记录证明：

- MU/BE/INTC 的活动 Event root/version 与 monitoring_configuration 完全相同。
- ticker_monitoring_states、ticker_source_bindings、ticker_source_binding_revisions、v2_binding_commands 四表完整内容摘要一致。
- MU/BE/INTC/RKLB 控制 epoch、mode/requested_mode、removal epoch、removed、cutoff 和 mode effective time 一致；RKLB 全部活动 artifact 引用一致。
- Site Access、Chrome 身份/卷和现行消息源独立出口参数保留。正常运行产生的 revision、ACK、消息和历史记录允许继续推进。

## 已完成验证

1. 迁入、CAS、监测配置保留、增量升级和 MAINTAIN 针对性回归 17 项通过；页面事务并发与 Projector 争用回归 13 项通过。方向消费/维护/激活/W3、交易展示和消费者相关回归分别通过；前端 58 项测试、类型/schema 检查及构建通过。
2. 隔离状态真实模型调用：W2 约 37 秒，W3 约 66 秒；正确召回迁入 Policy，显式选择 Condition，交付合法 NEW 增量。沿用现行生产节点路由，未套用历史 Pilot 的统一模型配置。
3. 隔离真实 MAINTAIN 最终复测约 219 秒，PARTIAL；实际冻结输入包含完整 MU D2、C1/C3/C5、Future Nodes、Entity Relations/Network 和当前 Event 材料，warnings 为空。保留原 Policy、授权和研究缺口。之前失败尝试保留审计，不计为通过。
4. 三套正式 activation 的 prepare/commit/bus.ready/runtime.ready 全部成功。真实活动执行包为 revision 3，含 native Policy/projection/maintenance/W3 合同及 general + L1-01 atlas。旧冻结执行包继续保留。
5. 登录后的 MU D1 正文、12-Shell D2、新版 262-Policy D3、LONG/SHORT Condition、calibration、transmission 和 refs 可展示；BE 新六目录 Policy 内容可读。MU D2/D3 实际浏览器导出已落盘，并与迁入正文 SHA 一致，D2 为 2,386,738 字节。1262/1559 两个宽度下 MU 策略页正文无整页横向溢出，临时 viewport 已复原。
6. r2 发布后的 API/Web 健康；Projector、delivery、control、CDECR executor 在最后观察中均运行、restart_count 为 0。该结论是本轮观察窗口内结果，不等于长期稳定性保证。

未继续进行每只标的每个页面、每个 Condition、每个导出和线上自然首笔 READY/WATCH 的穷举验收；没有为验收创造生产交易意图。用户要求停止细项验收后不再扩展排查。

## 备份与恢复边界

远端备份根为 `/data/production-backups/v21-20261012`。五个核心库及 scheduler/usage/O4/Worker 库均已冻结备份，gzip 解压后的长度与 SHA 同源库一致。大库没有执行完整 PRAGMA quick_check，清单明确标记 DEFERRED_LARGE_DATABASE；不能把逐字节备份验证说成完整数据库逻辑健康检查。

文件备份覆盖 workspaces、实际 Event root、配置、运行产物、native-files 等：340,914 文件逐项核验；另备份 Event/配置相关 SQLite、WAL/SHM 共 1,287 文件。独立运行中的 RKLB/CDECR 测试库不在此次迁移范围。

异机位置：`D:/DoxAgentPilot/backups/v21-production-20261012`。文件归档、Event/配置数据库归档、research/initialization/runtime gzip 和清单回执已复制并验证 SHA；bus/read 大库 gzip 留在远端。仅在异机文件归档核验一致后，移除远端本任务创建的同一重复文件归档以释放空间；未删除原研究、历史业务数据、浏览器 profile 或旧镜像。清单和 `business-files-offhost.json` 记录该映射。

回退应保留切换后新增业务记录，优先通过正式 CAS 激活旧 D1/D2/D3 引用并沿用当时最新 Event/监测配置；不得用部署前全库覆盖回退正常消息和交易。灾难恢复时先停 writer，再按备份清单恢复数据库及对应文件；异机归档需回传，不可假设它仍在远端原路径。

## 保留问题

- 原六个历史投影缺口中，两个 DUPLICATE_EVENT_TRADE 合同问题已清除；实际正式投影仍有四个 PROVENANCE_UNVERIFIED（1240145、1240148、1240191、1240193），涉及旧维护 provenance。此前只用 DomainProjectors 的只读 replay 不能代替 FormalProjectors 的完整来源校验，因此没有声称四项已修复或伪造来源。
- 部分统计模块仍可出现 QUERY_TIMEOUT，历史统计完整性继续显示“不适用”；研究正文/列表可读与全部统计完整性是不同验收项。用户已要求停止细查，本轮不继续扩展优化。
- Site Access 原有 unhealthy 及部分外部消息源失败未纳入此次工作流迁移修复，不视为被本次发布解决。
- 根盘最后观察约剩 5.8 GiB，容量偏低；压缩备份方案完成了本轮部署，但生产增长仍需要扩容或另行安排经核验的备份转存。没有默认清理历史数据换空间。

完整可复核证据位于 `eval/v21_production_release_20261012`，包括冻结源清单、迁入/激活/保留回执、真实模型与 MAINTAIN 输入证明、导出哈希和备份清单。远端含凭据的有效环境文件仍保持仅 root 可读，未导出至报告。

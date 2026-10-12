# MU C4 本地真实工作流测试

日期：2026-10-09。测试范围是 Global Research 中四个 C4 节点，实际调用 Codex SDK `gpt-6-sol` / `high`，通过正式 `CodexGlobalResearchOrchestrator._execute_or_partial`、`_execute_after_c4_stage`、`CodexD1NodeRunner` 与 `WorkerJobManager` 顺序执行。C1/C3/C5 不重复运行，采用 D2/D3 v2.1 测试共用的 MU Document1 冻结资产。此运行不使用 Pilot case/coordinator，也不执行整条 D1 发布流程。

## 装配与权限复核

本轮修正 Global Research manifest 中 C4f skill 的路径：`skills/c4/c4f_future_node.md`，并同步 `c4f.md` 内的名称。预飞检查通过真实 `AttemptBundleSeeder.seed` 将四阶段各自的 `task.md`、`required_skills`、`output_schema_path` 写入隔离 workspace，再读取并与源码逐字及 JSON 等值对比。阶段映射如下：

| 节点 | Agent prompt | Skill | Wire schema / 主产物 |
| --- | --- | --- | --- |
| `c4_pre_scan` | `c4e.md` | `c4e_pre_scan.md` | `c4e_relations.schema.json` / 初步五字段关系 |
| `c4f_future_nodes` | `c4f.md` | `c4f_future_node.md` | `c4f_future_nodes.schema.json` / 五字段 Future Nodes |
| `c4e_formal_scan` | `c4e.md` | `c4e_formal-scan.md` | `c4e_relations.schema.json` / 完整五字段关系快照 |
| `c4e_network_build` | `c4e.md` | `c4e_network-build.md` | `c4e_network-build.schema.json` / 非空 Markdown 字符串 |

Structured C4 的 `report_markdown` 固定为空，schema 禁止其他业务字段；network-build 使用 JSON 字符串响应与 `markdown_output_path`。各 skill 的交付段落与对应 schema 一致。SDK 在四阶段 `WorkerRunRequest` 的正常请求下设置 `web_search="live"`；只有 `read_only=True` 且 `data_mcp_enabled=False` 的审计请求才关闭。13 项 SDK 权限参数测试通过，其中包括四个 Global C4 节点；C4 编排及 workspace 注入 17 项定向测试通过。测试均有一项既有 importlib-metadata 警告。

## 冻结输入和证据边界

源目录：`D:/DoxAgentPilot/cases/document2/_sources/init-mu-1f3e9130ae304b01a7fdf2991a35e028-d1/`。沿用 C1、C3、C5 完整正文和 `horizontal.json`，原文件未修改；四个文件的绝对路径、字节数与 SHA-256 见 [run_summary.json](run_summary.json)。旧正文内的 `【cite:O#】` 属于原 September run 的引用作用域，注入时替换为“历史引用需本轮核验”，提示 Agent 对本轮新论断重新查证。因源包只保留报告与横向资产，未提供完整的原 attempt observation store，本测试不能证明正式 run 的上游 O 别名重新绑定质量。

## 运行记录

首个隔离 run `c4-mu-local-20261009-01` 在 SDK 开始研究前因本地嵌入式 Worker 与直接 SDK runtime 的身份回调签名不匹配失败；原始状态存于 [run_summary_startup_failure_01.json](run_summary_startup_failure_01.json)。驱动在第二个 run 中关闭仅用于提前记录身份的回调，Worker 的终态 receipt 仍保留 thread/turn 身份。此改动只在本地测试驱动中，不改变正式 C4 编排。

第二个隔离 run `c4-mu-local-20261009-02` 于 2026-10-09 13:34 UTC 启动，原始过程和产物已保留，以下记录它暴露的问题。

首次 10 分钟检查确认 pre-scan 一次 attempt 完成 18 条关系、2 次原生 web search；C4f 一次 attempt 完成 12 个 Future Nodes、14 次原生 web search，二者共用同一 SDK thread。发现两条独立的引用链缺陷：Source Capture MCP 的工具调用因 SDK deny-all 下缺少显式工具批准而失败；Windows 下 Data MCP Observation Pack 为 CRLF，引用提升以 LF 原文逐字比较，导致 pre-scan 的 12 个被引用 O 别名虽在 canonical store 里存在却全部误判。当前运行进程已载入旧代码，不把代码修复冒称为这轮实模的成功。源码现已为受控 Source Capture 工具加入批准配置，并将 Pack 校验改为换行规范化比较，针对四个 C4 节点的权限测试及 CRLF 回归已通过；待独立新 run 验证真实闭环。

第二轮最终结果已归档在 [runs/c4-mu-local-20261009-02](runs/c4-mu-local-20261009-02)：pre-scan 18 条关系、Future Node 12 条、formal-scan 31 条完整关系，前三阶段都单次成功并使用同一 thread。network-build 两次请求均在模型研究前返回 HTTP 400：Codex 结构化响应 API 要求顶层 object，但其 Markdown schema 是顶层 string。源码已调整 SDK 调用：此阶段不向 API 发送不兼容的结构化格式参数，仍在 workspace 注入 string schema，并由 NodeRunner 对返回正文进行该 schema 的校验。相关 SDK 兼容测试通过。第二轮三项已成功输出因上述引用链缺陷不作为最终验收结果。

第二轮内容结构显示 formal-scan 确实从 pre-scan 的 18 条扩展为 31 条，并包含 13 条关系主体非 Micron/美光的外部关系；Future Node 12 条均满足五字段非空且事件正文不重复。pre-scan、C4f、formal-scan 的原生 web search 分别发生 2、14、8 次。证据方面，pre-scan 有 666 个 canonical Observation，但 12 个被引用别名全部因 Pack 换行误判未解析；C4f 的 5 个被引用别名均未在该 attempt 的 Observation store 出现；formal-scan 未建立可解析引用。这个结构性成功不等于证据完整或研究质量验收。

第三个隔离 run `c4-mu-local-20261009-03` 于 2026-10-09 13:56 UTC 启动，按约 10 分钟间隔检查。该 run 将验证上述三项修复在真实模型调用中的整体效果。

第三轮首次检查：pre-scan 18 条五字段关系且 2/2 引用解析；C4f 16 个五字段 Future Nodes，16/16 条来源含链接、发布日期非空，原生 web search 发生 17 次，并有一次 Source Capture MCP 调用获准执行（工具返回连接警告，未形成新 Source Capture Observation）。两阶段一次 attempt 成功、同一 thread，均为 `gpt-6-sol/high`。发现本地驱动仍让同一 SDK 进程跨 C4 阶段存活，C4f Data MCP 指向 pre-scan 旧 attempt；正式 C4 CapsuleRuntime 的 `release` 会在每阶段后关闭底层 SDK/MCP 进程。下次隔离 run 将使测试驱动也在每次 Worker job 结束后关闭 SDK，复用持久 thread ID 而重建 attempt 能力。第三轮运行结果仍保留作对照，不把驱动缺陷算成正式编排验收。

第三轮第二次检查：formal-scan 一次成功，40/40 条关系五字段非空，其中 12 条以非 Micron/美光为主体、40 条说明中均有直接来源 URL；原生 web search 9 次。它识别了上游 9 月材料对 FY26 Q4 的过时未来表述，并用较新资料修正。因本地驱动的旧 MCP 路由，formal-scan 未形成新的可解析 O 别名而改用直接链接。network-build 随后一次成功，交付 8,903 字符的独立 Markdown 网络研究，含六个研究主题和 29 个不同来源链接。四阶段同一 thread，原始记录和产物保留在 [runs/c4-mu-local-20261009-03](runs/c4-mu-local-20261009-03)。这轮证明 Markdown schema 的真实 SDK 兼容修复有效，但跨阶段 MCP 路由问题仍使它不适合作最终证据链验收。

最终隔离 run `c4-mu-local-20261009-04` 于 2026-10-09 14:27 UTC 启动、14:57 UTC 完成。测试驱动按正式 Global C4 CapsuleRuntime 的生命周期，在每个 Worker job 结束后释放 SDK/MCP 进程，再以持久 thread ID 继续下一节点。依用户要求，启动后约每 10 分钟检查一次，没有高频轮询。四个 `model_selection.json` 均记录 requested/execution model 为 `gpt-6-sol`、effort 为 `high`；四次 attempt 均单次成功、无重试，thread ID 均为 `01a1210f-f512-71d2-861b-5905165488ad`。原始汇总、产物和持久 workspace 分别保留在 [runs/c4-mu-local-20261009-04](runs/c4-mu-local-20261009-04)、[outputs](outputs)、[workspaces/c4-mu-local-20261009-04](workspaces/c4-mu-local-20261009-04)。

| 节点 | 最终主产物 | 实际 web search | Source Capture 工具 completed（含软警告） | 当前 attempt 的引用解析 |
| --- | --- | ---: | ---: | ---: |
| C4e pre-scan | 16 条初步关系；Future Nodes/正文为空 | 3 | 6 | 16/16 |
| C4f Future Node | 14 个节点；关系/正文为空 | 25 | 18 | 23/23 |
| C4e formal-scan | 41 条完整关系；Future Nodes/正文为空 | 9 | 21 | 41/41 |
| C4e network-build | 6,367 字符 Markdown 正文；关系/Future Nodes 为空 | 4 | 14 | 50/50 |

四阶段 `turn_summary.json` 均显示 `schema`、`progressive`、`citation` 验证通过且无 citation warning。四个五字段产物检查通过：C4f 的 14/14 个节点均有来源 URL 和来源发布日期；formal-scan 的 41/41 条关系均有当前 attempt 引用，包含 pre-scan 的 16/16 个精确主体—对象端点对，且 19 条以非 Micron/美光为主体。network-build 的正文有六个研究主题、57 处 O 引用和少量直接 URL，研究了 AI/HBM 封装与平台、推理存储、产能合同与终端配置、汽车资格、纽约厂房/公用设施/原硅片、台湾劳资和跨境规则；它不是关系表摘要。全部 50 个不同 O 别名都能在 network-build 自己的 Observation store 找到。C4f 自己的 Data MCP 文件也落在其 attempt 下，第三轮的旧 attempt 路由不再出现。

执行中有非终态工具失败：pre-scan 曾有一次 shell 命令及两次 Observation 读取失败；C4f 有数次 Observation 读取失败、一条 shell 命令失败，以及一次 Source Capture 调用在 30 秒超时。后续 Source Capture 调用成功、相关节点完成，引用与 schema 均通过。formal-scan 和 network-build 的 `turn_summary.json` 没有记录工具失败。保留这些失败在持久收据中，不把“节点成功”解释为工具调用零故障。

## 结论与剩余边界

本地真实 C4 链路的正常路径技术验收通过：四套 prompt/skill/schema 实际注入各自 workspace；全节点具有并实际使用 live web search；一个 C4 thread 串行完成四个独立职责；三个最终产物独立且非空；当前 attempt 的引用均可解析。第三轮先暴露、第四轮解决的是**测试驱动**复用 SDK 进程导致的旧 MCP 路由；正式 CapsuleRuntime 本身已有阶段释放行为。正式代码另修复了 C4f skill 路由、Source Capture 批准、Windows CRLF Observation Pack 校验、以及 Markdown 顶层 string schema 与 Codex 结构化 API 的兼容：network-build 仍在 workspace 接收 string schema，并由 NodeRunner 验证最终正文，SDK 不把该 string schema 发送给只支持顶层 object 的 API。

2026-10-09 补充审查：上表的 6/18/21/14 是工具 completed 数，不能全算捕获成功。第四轮 Source Capture 共60次，47次返回 alias、12次软警告、1次超时；返回 alias 的分节点数为6/13/19/9。正常路径通过不代表异常恢复验收通过；现已复现失败回复覆盖有效报告、用量记录故障重复研究、C4f失败扩散、部分发布后原run不恢复等问题，动态Pack还有Windows sandbox ACL缺陷。详见[完整失败与非阻塞性审查](../../dev_plan/workflow_v2.1/c4_workflow_failure_review_20261009/README.md)，其中列出全部异常与修复方案，本次尚未实施这些修复。

最终代码回归：C4 编排/注入及 SDK 的 string-schema/web-search 定向组合 **31 passed**，仅一项既有 OpenTelemetry importlib-metadata 弃用警告；相关 Python 文件 Ruff 与 `git diff --check` 通过。测试驱动及四轮原始收据保留，未做生产部署或远端迁移。

这不是整条 D1 发布验收：C1/C3/C5 未在此 run 重新执行，使用的是 D2/D3 v2.1 共用的 9 月冻结 MU Document1 正文与横向 JSON；源包没有完整上游 Observation store，旧 O 别名必须失效并由本轮重新检索。因此本测试证明 C4 自身的装配、执行、当前证据链和三产物职责，不能证明新鲜 C1/C3/C5 或 D1 publish 的端到端质量。业务事实只作结构和少量来源抽查；例如 Future Node 中 FY27 Q1 的收入与利润率指引，与[美光 2026-09-30 正式公告](https://investors.micron.com/news/press-release/2026/Micron-Technology-Inc--Reports-Record-Fiscal-Fourth-Quarter-and-Full-Year-2026-Results/default.aspx)一致，不能据此把全部关系或未来日期视为逐条人工核实。

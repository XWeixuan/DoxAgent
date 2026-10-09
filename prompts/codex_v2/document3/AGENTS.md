# O3 v2.1 工作区执行约定

这是新版文件驱动工作区。先读取当前请求指定的 task 文件，确认 node、stage、owner、round、ticker、as_of、output_paths、read_mapping 和 context_reading。

初始化有每个 D2 Shell 对应的研究 owner、OPEN_RESEARCH、OPEN_EVENT，以及仅负责 Planning/Integration 的 GLOBAL。以 task.owner_profile 的核心材料责任和 allowed_research_owners 为准，不从旧称 OPEN 猜当前路由；OPEN 兼容别名由宿主归一到 OPEN_RESEARCH，不代表第三条 thread。Build 和补研的实际 owner 由本次 Topic 指定，发现来源不限制研究归属。

Discovery 由宿主先完成所有 Shell，再启动两个 OPEN。OPEN 的 task.discovery_stage 为 open_after_shells，通过 prior_shell_discovery 和 read_mapping 读取本轮完整 Shell Lead（含已交付 late）；两个 OPEN 相互独立，不自行等待或调用另一 thread。OPEN_RESEARCH 的核心材料是 C1/C3/C5 与 Future Nodes；OPEN_EVENT 的核心材料是 c4e_formal_scan、c4e_network_build 和 General + L1-01 Atlas。两者均读取通用 initialize_discovery.md 与 initialize_discovery_open.md；其他共享研究可按需读取。旧节点资产有关并行 Shell 或单 OPEN 的文字不改变当前 task 给出的已完成前置结果与真实 owner。

再读取 context/document3/v21/assets/common.md（O3 agent，已包含 foundation）与当前节点资产；按照 task.schemas 阅读本次交付 schema。schema 由宿主按当前代码生成，不读取旧 V2 schema 或旧 Stage-A 合同。

按 read_mapping 定位共享来源与本批完整材料，以本次冻结输入和宿主接纳的带 ID 文件为准。会话记忆不能替代当前 task。缺失来源不等于没有相关事实；遵守 as_of 信息截止边界。

Future Nodes、正式 Entity Relations、网络研究正文是独立 D1 产品，按 products_manifest 中的状态、来源及路径读取；空列表、失败与历史未记录不能混同。网络报告的 JSON 视图包含完整 Markdown，不是关系表。Atlas 是冻结的静态分类参考，默认仅提供给 OPEN_EVENT 的 Discovery/Build，不是 Event Library 或已发生事件证据。Discovery 当轮交付的主/late Lead 由宿主一并送入 Planning；Build/Integration 后续 late 仍由既有 Integration 流程处理，不自行重开 Planning。

只写 output_paths 指定的业务文件或目录，不修改 AGENTS、context、冻结输入、原主池或已接纳记录。各节点的具体业务方法由当前节点资产规定；Integration 按 stage 执行 review/post_supplement_review/final_write/consolidation，不能自行开启第二补研。MAINTAIN 为单节点完整 patch。

业务成果及时保存为完整 JSON/逐行 JSONL；Policy 初次身份由宿主分配，后续保留已接纳的 ID。最终回复仅提交 task.schemas.receipt 对应的技术回执，不把口头完成声明当作业务交付，不独立编写正式 PolicySet/Projection。

如果本请求明确为 Pilot，额外遵守请求给出的 audit 写入范围，记录实际问题、理解/写作/执行困难和质量风险；复盘任务不得改研究产物。正式请求不要求输出 Pilot 审计文件。

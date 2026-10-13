# W3 单 Skill 编排接入与上线前检查（2026-10-14）

## 结论与检查基准

按上线前完整 Git push、重新构建并部署全部相关服务的前提检查。当前远端旧镜像、旧执行包或尚未部署的本地代码，不作为上线阻塞。此次没有执行 Git 提交、推送、部署或交易。

本轮修复了提示词接入、默认启动发布、模型实际路由及行情截止参数等工程冲突。仍有一项当前已确认的外部依赖未通过：**IBKR API 握手超时，尚不能从 Worker 取得历史 K 线。**完整构建不保证恢复 TWS/API 会话，因此上线前必须重新验证这条依赖。端口连通不能代替 API 与历史数据验收。

## 已落实的编排

普通 W3 只使用用户提供的 `w3/AGENTS.md` 和 `w3/skills/skill.md`。三份现有文件（含 weekend_sweep）内容均未修改，起止 SHA-256 一致，见 `eval/w3_refactor_20261013/prelaunch_receipt_20261014.json`。

1. 启动工厂检查新的两个公共文件和独立 sweep 文件。持久化运行且启用 W3 时，默认发布 `persistent-runtime.w3.v4`，资产布局标记为 `agents-skill.v1`。不再因为默认工厂未传合同标记而误走旧单阶段 schema。
2. Template 工作区写入 AGENTS.md 和唯一 Skill；初始化 user prompt 注入一次 Skill 原文，以及工程生成的初始化身份、研究索引和 ready schema 入口。初始化只建立公共上下文，不研判消息；不把首个 Case 的 reference_view 注入母会话。
3. 子 Thread 继承母会话公共 Skill。两个业务阶段的 user prompt 只携带阶段 JSON、业务输入及实际存在的 task/schema/reference/research 路径。无需外部 task.md、template.md 或第二套业务指令。运营阶段继续同 Case 同 Thread，读取第一阶段研究入口，不重发整个业务 Skill。
4. 新 Case 冻结的公共资产仅含上述两个文件。周末 sweep 留在完整执行包内，由原 `WeekendSelection._judge` 单独附加到 selection 任务；其候选选择流程、schema 和独立工作区不变，sweep 文件变化不再改变普通 W3 Thread 兼容键。
5. 已冻结的历史旧包、旧 Case 和旧四文件 v4 包继续按各自保存的资产恢复；新发布的 v4 包要求新双文件布局。没有批量改写历史 Case 或正在执行的 Thread。

## 修复的逻辑冲突

| 问题 | 处理与边界 |
|---|---|
| 工厂仍要求 agent.md 和旧四象限 Skill | 改为新资产校验；新增真实工厂构建测试，确认默认 active 包带新 W3 合同。 |
| 全局模型升级别名把 Policy HIT 的 gpt-6-sol 换成 gpt-6.1-sol | Worker 对 W3 显式指定的 gpt-6-sol / gpt-6-luna 保留实际模型；其他节点和旧模型升级规则保留。Template、Fork 与阶段仍使用冻结配置。 |
| 开启 W3 Data MCP 后连带启用了 Source Capture | W3 只保留其现有只读 IBKR Data MCP 白名单及 live Web Search，关闭其他辅助 MCP；不改其他研究节点的 Source Capture 权限。不扩大数据工具白名单。 |
| Skill 要求历史结束时点，historical_bars 却固定查到现在 | 新增可选 end_datetime，原调用默认不变；显式传给 reqHistoricalData 并回传查询边界。提供 Case cutoff 对应 UTC 字符串。返回 bar 的起止语义、末条是否完整仍由 Skill 按合同判断，接口不冒充自动防前视过滤器。 |
| Skill 使用交易窗口，但阶段输入没有对应信息 | 增加 trading_context：运行模式、语义日、意图有效期、输出是意图还是 CLOSED 候选；存在实际执行配置时提供该配置的收盘前退出偏移及以 Case 时点计算的计划退出边界。配置未知时用 null；实际退出仍由 Executor 根据实际成交时间决定。CLOSED 未选择释放前不伪造释放期限或具体退出时点。 |
| 多 Case 直接使用同一个默认 IBKR client ID 容易冲突 | 业务输入提供按 Case 派生的只读查询 client_id，和 cutoff 对应的 end_datetime。连接 host/port 仍由 Worker 现有 settings 取得，不注入账户配置。这是调用信息，不创建会话或额外研究阶段；Agent 应采用该 ID，仍不得并行重复使用同一 ID。 |
| CLOSED 受控制层拒绝的提案仍返回 CANDIDATE，运营阶段可能误认为有效交易资格 | 保留原 output_disposition，另外传递候选实际 origin_trade_eligible 和 trade_disposition，供 Skill 区分有效候选与被拒绝提案；不改变候选释放和 WATCH 的后台资格规则。 |
| v4 公共资产筛选后丢失 runtime_models.json | 模型冻结直接读取完整执行包的模型资产，避免悄然退回只含 W1/W2 参数的默认值。 |

## 外部依赖实测

实测使用现有远端 Worker 账号与容器环境；所有市场查询均为只读，模型测试仅发送合成的 ok 请求，无业务消息、账户查询、订单或数据库业务写入。模型合成会话留存用于追溯。

| 项目 | 结果 | 证据边界 |
|---|---|---|
| Worker 账号认证与模型列表 | 通过 | 账号已认证，两种模型均列出。 |
| gpt-6-luna / max 精确模型真实调用 | completed，实际 gpt-6-luna | 不是仅检查模型列表；测试未覆盖业务研判。 |
| gpt-6-sol / medium 精确模型真实调用 | completed，实际 gpt-6-sol | 绕过全局升级别名直接验证精确模型，工程回归另验证 W3 不再被别名覆盖。 |
| Worker 的 ibapi 安装、host.docker.internal:7496 TCP | 通过 | 镜像构建也已有固定校验和的官方 ibapi 安装及 prompts/src 复制。 |
| Worker 的 IBKR API 握手及 MU 10 mins / TRADES / 非仅 RTH 历史查询 | 未通过 | TCP 打开后 nextValidId 握手等待 15 秒超时，未进入实际历史查询。 |
| Message Bus 同入口只读对照 | 同样握手超时 | 对照支持问题不局限于 W3 请求组装；没有证明具体是哪项 TWS 设置或会话状态导致。 |
| 主机监听检查 | 172.17.0.1:7496 由 socat 监听 | 开放的是转发入口，不能据此推断 TWS API 会话健康。 |

**上线前必须恢复并确认：**转发后端可用的 TWS/API 登录会话、API 访问设置与 market-data entitlement；从最终 Worker 执行环境实际完成握手、ticker contract 解析和上述历史查询。不能把这次失败改写成“行情已验证”；也不能把 10 月 13 日早前另一轮成功的历史样本当成当前通过。

这不是增加逐 Case 人工审批。新 Skill 对工具未知已有明确业务处理；依赖故障也不应被工程偷偷转换成自动 NO_TRADE、自动放行或额外研究阶段。

## 验证与上线验收边界

- 新旧 W3、v4 决策与运营恢复、模型请求、IBKR 权限、历史 API 底层及编排执行：一组 49 项通过。
- 新双文件工作区/工厂、精确模型与 MCP、历史截止参数、通用 Worker SDK：另一组 84 项通过；两组有重叠，不累加。
- 最终恢复、生命周期、Read 投影、sweep 与新双文件组合：43 项通过。随后补充 CLOSED 被拒候选资格回归，本轮双文件、历史截止和权限最终组合 13 项通过。
- 修改范围 Ruff 与 diff whitespace 检查通过；仅有依赖库的既有 DeprecationWarning。
- 实际生产业务 Case 的新两阶段完整研判尚未执行，合成模型调用不能替代这项验收。用户完整部署后，应以真实输入确认新 bundle、模型审计、单 Skill 继承、第一阶段只出四字段、第二阶段只出五字段、K 线真实查询及后置动作回执；不需要额外业务审批开关。
- 部署需让 Runtime、Worker 使用同版代码并发布新包；API/投影/UI 则随此前整体重构一并更新。研究输入仍须有有效激活的 D1/D2、原生 PolicySetV3 / ProjectionV5；缺失研究不由新提示词或镜像重建自动补齐。历史缺少 policy_origins 的包继续允许 unassigned，不新增来源齐全的入场门槛。

当前检查没有发现需要再设计第三阶段、修改决策输出 schema 或回退到旧提示词的理由。

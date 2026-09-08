# DoxAgent V2 Frontend Foundation

- 日期：2026-09-07。
- 状态：前端基础方向与业务决策已冻结；2026-09-07 已落地独立基建与 Overview Golden Page，真实后端联调待完成。
- 范围：固化前期排查、工程边界、基础设施选择、状态表达原则及待收口的契约事项。不包含页面 composition、具体排版、组件尺寸、详细实施方案或开发排期。
- 依据：[PRD Part 1](DOXAGENT_V2_FRONTEND_PRD_PART1.md)、[PRD Part 2](DOXAGENT_V2_FRONTEND_PRD_PART2.md)、[API Contract](DOXAGENT_V2_API_CONTRACT.md)（排查时 draft.2）、[wire types](api_contract/doxagent-v2-api.types.ts)、[视觉语言](../v2_design.md)、[后端实施方案](DOXAGENT_V2_BACKEND_IMPLEMENTATION_PLAN.md)。

## 1. 决策优先级与产品边界

用户明确确认的决策优先；PRD 决定功能与信息需求，API Contract 决定双方一致的数据交换协议，v2_design.md 决定总体设计语言。文档冲突须明确收口，不在前端静默换算或回退。本文件记录的 Q1/Q2 为用户最新裁定，覆盖旧文档对应条款。

V2 是 **Greenfield Rebuild**：不在 V1 上改，不复用 V1 组件，不继承 V1 视觉系统。PRD §2.1 中允许复用旧视觉的条款已被用户此项要求覆盖。V1 仅作 behavioral / information anchor；基础技术与鉴权流程可参考后重新实现。

产品范围为 Overview，以及基础投研、事件库、预期研究、交易策略、消息总线、运行状态、收益 / 成本审计七个 ticker 页面。收益审计本期仅保留“暂未开放”入口，不发起收益审计读取；Overview 收益指标仍按 PRD 展示。无 V1 入口、版本切换或回测功能。

## 2. V1 排查结论

| 范围 | 现有实现及 V2 处理 |
|---|---|
| 工程 | `frontend/dashboard` 为 React/TypeScript/Vite、Tailwind、shadcn/Radix 应用。可重新选用这些技术，不搬运旧应用源码 |
| 信息架构 | D2 混在研究页、Known Events 混在策略页；V2 按 PRD 拆出独立页面 |
| 数据读取 | `use-dashboard-query.ts` 以组件内状态保存数据，挂载读取，多页存在秒级轮询；不满足标签页共享缓存与更新范围要求 |
| SSE | `use-dashboard-events.ts` 的游标随 hook 生命周期重置，多页事件触发重新读取；需要独立范围游标和增量应用机制 |
| 鉴权 | 邮箱密码、Supabase session、服务端开发者校验可参考；旧 API token/mock 回退及全局权限错误处理不直接复用 |
| 状态 | `lib/format.ts` 按通用字符串映射颜色，混合暂停、历史、部分可用等语义；V2 按领域和维度重新建立映射 |
| 构建 | `Dockerfile.dashboard` 将旧前端打入旧 API 镜像；V2 需要独立构建和服务接线 |
| 隐含依赖 | `scripts/generate_v2_schema.cjs` 从旧前端 node_modules 加载 TypeScript；后续契约工具链需独立，避免删除 V1 后破坏 V2 |

以上为源码与文档排查，不是线上验收。后端并行修改不属于前端可复用的稳定交付证明。

## 3. 已冻结的前端基础设施

| 范围 | 决策 |
|---|---|
| 独立应用 | 新建 `frontend/v2`，独立依赖、配置、构建、测试；禁止导入 V1 页面、组件、hooks、DTO 或 CSS |
| 技术栈 | React + TypeScript strict + Vite SPA；pnpm 与锁文件。具体兼容版本在建项时核验并固定，不追随浮动 latest |
| 路由 | React Router，按页拆包；沿用 PRD URL。URL 保存可定位的模式、筛选和稳定对象身份，临时展开/编辑状态保留本地 |
| 组件原语 | 重新引入 shadcn/Radix；Tailwind + CSS 语义令牌。V2 自有基础组件与状态映射，不复制 V1 preset 或组件文件 |
| 服务端数据 | TanStack Query 管理共享缓存、请求去重、定向更新；React 管理本地交互，不复制另一套全局业务数据 |
| API | 业务请求只走 `/api/doxagent/v2`，统一认证、错误、取消、分页、SSE 和下载；禁止 V1 回退及浏览器直接读取业务数据库 |
| 类型 | 共同版本的 wire contract 为字段源，配套运行时校验；DTO 与领域显示模型分离。身份、版本或范围不匹配不得接纳，局部内容问题仅影响目标模块 |
| 鉴权 | Supabase 邮箱密码与可信开发者访问；`/auth/me` 的 can_read/can_operate、capabilities 与对象 actions 共同控制入口，服务端仍独立授权 |
| 内容 | Markdown 语义阅读、D2/D3/Event 领域结构化呈现、引用解析、分块正文、认证下载；不以通用 JSON 查看器作为正式页面 |
| 数值与时间 | 消费服务端业务值和语义时钟，统一 DecimalString、Token M 单位及时间格式化，保留原始日期精度；不在前端计算交易日、成交、策略有效性或成本 |
| 部署 | 独立静态构建产物；生产页面与 V2 API 同源接入，开发代理对应 V2 API；不引入 SSR 或额外前端业务服务层 |
| 验证 | 类型、lint、构建、协议与状态测试、浏览器关键流程；独立契约 fixtures 支持前后端并行，mock 不作为真实服务故障回退 |

### 缓存、一致性与刷新

- 普通业务数据保存在当前标签页内存，不持久化到 localStorage/IndexedDB，不设置自动过期；TanStack Query 使用无限 stale/GC 时间，关闭 mount/focus/reconnect 自动重取、默认轮询及默认自动重试，特殊重试由协议层明确管理。
- 缓存按授权会话、ticker、页面、模式、周期窗口、筛选、对象版本与分页范围隔离。登出、换用户或授权失效时清除缓存并取消请求；迟到响应不能重新写回。正常 token 续期不触发全站重读。
- 切换范围只显示目标范围缓存或加载态；刷新失败保留同范围旧数据。组件共享请求，不以挂载次数增加读取。
- ReadContext 固定页面读取范围；run、runtime_activation_id、policy_activation_revision、policy_revision_id、library_snapshot_id 各自保留身份，不混用 latest，不按名称或数组位置关联。
- 人工读取、分钟读取、SSE、操作回执追踪四套机制分开。分钟读取仅作用于 PRD 指定的可见组件，依据服务端时钟的真实分钟边界；返回页面不立即补普通刷新。
- SSE 仅用于消息流和 Runtime 图，支持 Authorization、Last-Event-ID、scope 游标、幂等与修订检查。消息首屏和图可见集合有界；正常增量不重读完整列表、图或其他组件。明确 reset/过期时只重建受影响基线。
- 普通页面无 SSE、无轮询；正文、历史、详情和后续分页按需读取。下载只在点击时请求，列表不夹带大 payload；遵守 PRD §4.6 的上游读取与 egress 约束。
- 初始化自动追踪只到操作回执结算；排队成功不代表初始化成功。六步进度通过 Overview 人工刷新查看，取得完整成功事实后隐藏，不增加常驻轮询。

### 操作与故障

- 202 仅表示接受。使用 Idempotency-Key、If-Match 和真实回执，不乐观宣称暂停、移除或初始化已完成；网络结果不明时保留原幂等身份。
- 待结算操作按服务端间隔有界追踪，最长自动追踪五分钟；页面隐藏停止，到时仍未结算显示“仍在处理”，不当作失败。
- 写入结果定向更新相关缓存，不全局刷新；412 保留草稿，取得最新目标后由用户重新保存，不自动覆盖。
- 401 走会话恢复/重新认证，403 按实际授权范围处理；单个子资源无权限或失败不能误判为整站无权限。
- 字段未知、未记录、不适用、确认空集合及真实零值分别表达；单个对象、引用或模块失败不清空其他成功内容。
- 测试重点为跨 ticker/用户竞态、缓存与请求次数、版本一致性、分钟/可见性、SSE 重放与断档、长操作/并发冲突、零值与覆盖情况；mock 完整不替代真实 API 和业务验收。

## 4. 用户已确认的业务口径

### Q1：Runtime Policy 命中比例——按契约

`最终有效判定命中的唯一 Case 数 / 已形成最终有效 Policy 判定的唯一 Case 数`。

未完成判定、跳过或无有效判定的 Case 不进分母；W3 完成后使用最终判定。100 个已接收 Case 中，40 个形成最终有效判定、10 个命中，显示 25%。没有分母样本时显示不适用/暂无数据，不补零。后续同步 PRD Part 2 §6.4.5，前端不使用“周期处理 Case 数”作为分母。

### Q2：成本审计请求数与平均 Token——按 PRD

请求数量统计取得有效用量记录的真实模型请求 / Codex turn；平均每请求 Token 使用对应有效用量样本，不能把无用量请求当零 Token 加入分母。

真实调用 10 次，其中 8 次有完整用量、合计 80,000 Token，页面请求数为 8，平均为 0.010M。全部可证明调用与缺失用量数量可由后端保留用于 coverage，不新增前端 KPI。缺失 cached input 不等于没有其他有效用量；分项与 total 各按证据判断，无法取得对应总量时不虚构均值。

后续同步 API §12.3、UsageTotals 的字段语义和示例，取消“全部真实请求都进入页面 requests，任何一条缺 total 就必然抹去已有有效样本均值”的规则。具体缺失分项组合需在契约验收样例中对齐。

### 已有后端冻结决策继续有效

- 消息监测只分析、不消费 Policy、不生成正式交易 intent，后续切换模式不补发旧监测信号。
- 同一 ticker 共用研究、策略生命周期和消费记录；Paper/Live 只决定执行环境。
- 暂停/移除停止新分析和新 intent；此前正式 intent、已接管 Entry/Exit 按原规则继续，不新增撤单或平仓动作。
- 移除持久隐藏，但允许通过原启动表单显式重加；历史和消费记录保留，不新增恢复入口。

## 5. 下一轮需收口的工程与契约事项

以下不是新增业务问卷，也不表示本轮已修改 API/PRD：

1. 同步 Q1/Q2 与 Greenfield 优先级到受影响文档、类型说明和验收样例。
2. Runtime 图补足 PRD 要求的路由判定节点；确认有界数据足以支持完整周期的真实路径关联，不能用有限最近 Case 或全局边的拼接冒充真实路径。
3. Overview 的运行状态/健康筛选明确服务端有界读取支持，不仅在第一页做本地过滤。
4. 消息排序按 PRD 的抓取时间语义，收口契约的 stream_published_at 排序差异，并保持服务端游标、分页与 SSE 一致；前端不自行重排补救。
5. 解耦 V1 构建与契约生成工具链；前后端以同一契约版本联调，能力缺失如实显示但不因此宣称业务交付完整。
6. binding 页面仅开放 PRD 的参数、启停和轮询间隔；契约可写字段不自动扩展为产品入口。

状态维度与设计语言约束见 [v2_design.md](../v2_design.md)。以上记录基础方向冻结时点的决策；后续实施与验收范围见 [V2 前端说明](../../frontend/v2/README.md)。Overview 已建立独立契约演示，其他业务页保持未开放；未执行真实 workflow 或交易。

## 2026-09-08 业务页实施补充

七个业务页已接入真实 V2 API；收益审计仅按 PRD 保留占位。Overview 筛选、普通 GET Resource 包装、Policy ALL、Runtime 按实际 Case 的节点路径聚合已在前后端同步收口。Runtime 仍采用共同契约的九节点，不由前端虚构 ROUTER 节点。当前代码与验证边界见 frontend/v2/DELIVERY.md；隔离测试成功不等于真实账户、真实数据库和部署验收完成。

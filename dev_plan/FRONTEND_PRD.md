# DoxAgent Dashboard 第一阶段产品需求方案

## 1. 产品定位

DoxAgent Dashboard 是 DoxAgent 消息面事件驱动自动交易 agent 集群的统一前端控制台，用于集中查看和管理：

1. 标的监测配置与运行状态；
2. Document 1 / Document 2 投研资料；
3. Document 3 执行策略；
4. Message Bus 消息流与配置；
5. 持久化监测执行链路；
6. 当日收益审计与 token 成本审计。

第一阶段目标不是做复杂量化交易平台，而是做一个**个人使用、功能完整、低维护、可长期盯盘的前端**。

核心原则：

* 功能要覆盖完整链路；
* 页面交互要简单；
* 前端只消费 Dashboard State API；
* 不直接耦合 workflow 内部表结构；
* 中文界面；
* UI 风格对齐 DoxAtlas；
* 第一阶段优先保证可用性、可读性和可维护性。

---

## 2. 部署与访问方案

### 2.1 部署方式

采用：

```text
子域名 + 反向代理 + Docker 服务
```

推荐访问入口：

```text
agent.doxatlas.com
```

部署结构：

```text
HK Server
├── doxatlas
├── doxagent
└── doxagent-dashboard
```

DoxAgent Dashboard 作为 DoxAgent 项目下的独立 Docker 服务部署，由反向代理转发到 `agent.doxatlas.com`。

### 2.2 前端技术栈

采用：

FastAPI + React + shadcn/ui

前端由 React 实现页面与交互，并基于 shadcn/ui 组件库快速构建统一风格的 UI（通过 pnpm dlx skills add shadcn/ui 初始化并按需引入组件）；FastAPI 提供 Dashboard State API 和必要的后端鉴权、状态聚合、SSE 推送能力。

### 2.3 鉴权

优先沿用 DoxAtlas 现有 Supabase 鉴权体系。

访问规则：

* 只有已登录用户可访问；
* 只有 dev 层级用户可访问 `agent.doxatlas.com`；
* 前端页面和后端 API 都必须校验权限；
* 不能只依赖前端隐藏页面；
* 关键操作需要后端再次鉴权。

若跨子域名复用登录态成本较高，第一阶段允许 `agent.doxatlas.com` 单独登录，但仍使用同一套 Supabase 用户与 dev 权限判断。

---

## 3. 数据刷新与状态同步

第一阶段采用：

```text
状态快照 + SSE + 轮询
```

### 3.1 状态快照

Dashboard State API 提供稳定的状态快照，作为前端展示的主数据来源。

适用内容：

* Overview KPI；
* ticker 运行状态；
* 文档状态；
* Message Bus 统计；
* Runtime 节点状态；
* 收益与成本审计结果。

### 3.2 SSE

SSE 用于实时追加事件流。

适用内容：

* 新消息进入 Message Bus；
* W1 / W2 / O3 处理事件；
* 运行异常；
* 交易意图生成；
* 审计任务状态变化。

### 3.3 轮询

不同模块采用不同刷新频率：

| 模块                | 刷新方式       | 建议频率   |
| ----------------- | ---------- | ------ |
| Overview KPI      | 轮询         | 5–10 秒 |
| ticker 状态卡片       | 轮询         | 5–10 秒 |
| Message Bus 实时消息  | SSE + 快照纠偏 | 实时     |
| Runtime Execution | SSE + 快照纠偏 | 实时     |
| 投研资料 / 执行策略       | 手动刷新或低频轮询  | 60 秒   |
| 收益 / 成本审计         | 低频轮询       | 60 秒   |

前端不做整页刷新，只更新对应卡片、列表或图表区域。

---

## 4. 页面结构总览

第一阶段包含 6 个核心页面：

```text
1. Overview
2. 投研资料
3. 执行策略
4. 消息总线
5. 运行状态
6. 收益 / 成本审计
```

### 4.1 路由结构

建议路由：

```text
/overview
/ticker/:ticker/research
/ticker/:ticker/strategy
/ticker/:ticker/message-bus
/ticker/:ticker/runtime
/ticker/:ticker/audit
```

### 4.2 顶栏规则

Overview 页面：

* 左上角显示 DoxAgent Logo；
* 点击 Logo 返回 Overview；
* 不显示其他导航按钮。

具体 ticker 页面：

* 左上角显示 DoxAgent Logo；
* 点击 Logo 返回 Overview；
* 顶栏居中显示导航按钮：

```text
投研资料 / 执行策略 / 消息总线 / 运行状态 / 收益成本审计
```

页面语言全部使用中文。ticker、uuid、trace id、model name 等必要技术标识可以保留英文。

---

# 5. Overview 页面需求

## 5.1 页面定位

Overview 是 DoxAgent Dashboard 的全局入口，用于快速查看系统是否正常、当前有哪些标的正在监测，以及启动新的标的监测。

## 5.2 顶部 KPI 区

顶部展示一组运行状态 KPI 卡片。

第一阶段建议包含：

```text
容器状态
Message Bus 状态
Dashboard API 状态
当前运行 ticker 数
今日消息数
今日 DTC 数
今日 token 成本
异常数量
```

每个 KPI 卡片需要有状态颜色：

```text
正常：绿色
等待 / 延迟：黄色
异常 / 失败：红色
未启用 / 无数据：灰色
```

## 5.3 开启新标的监测

KPI 下方展示一个简单配置区域。

第一阶段只保留必要字段：

```text
ticker 输入框
确认启动按钮
必要参数提示
启动结果反馈
```

交互要求：

* 输入 ticker 后点击确认；
* 后端校验 ticker 是否已存在；
* 已存在则提示“该标的已在监测中”；
* 启动成功后生成对应 ticker 卡片；
* 启动失败时展示失败原因；
* 不在第一阶段做复杂配置向导。

## 5.4 标的监测状态卡片

配置区域下方展示所有标的监测进程的状态卡片。

每张卡片展示：

```text
ticker
当前状态：运行中 / 暂停 / 异常 / 已停止
最近一次消息时间
最近一次 worker 处理时间
今日 DTC 数
今日成本
```

卡片操作按钮：

```text
暂停
删除
重启
```

交互要求：

* 点击卡片主体进入该 ticker 的 dashboard；
* 点击操作按钮不触发页面跳转；
* 删除操作需要二次确认；
* 操作成功后刷新卡片状态；
* 操作失败时展示错误原因。

---

# 6. 投研资料页面需求

## 6.1 页面定位

投研资料页面用于查看当前 ticker 正在使用的 Document 1 和 Document 2 的 belief state 内容。

页面只负责展示与查看，不负责复杂编辑。

## 6.2 页面内容

页面展示两大区域：

```text
Document 1：Global Research
Document 2：Expectation Units
```

每个 document 区域顶部展示必要身份信息：

```text
document uuid
生成时间
当前状态：现行 / 历史
```

右侧或右上方展示状态胶囊。

## 6.3 内容卡片

Document 内部内容以卡片方式展示。

每张卡片包含：

```text
卡片名称
更新时间
主要内容摘要
展开 / 收起按钮
```

展开后展示该板块的详细字段内容。

第一阶段要求：

* 支持展开 / 收起；
* 支持长文本正常换行；
* 支持字段按中文 label 展示；
* 支持空字段显示“暂无数据”；

## 6.4 历史记录侧边栏

页面左上角提供侧边栏按钮。

点击后展开历史记录侧边栏，显示历史 document 版本列表。

历史记录列表展示：

```text
生成时间
document uuid
状态：现行 / 历史
```

点击历史版本后，页面切换为该历史版本内容展示。

---

# 7. 执行策略页面需求

## 7.1 页面定位

执行策略页面用于查看当前ticker的 Document 3 中和持久化监测执行直接相关的策略内容。

第一阶段展示：

```text
Known Events
Monitoring Execution Policy
```

不展示 Monitoring Config。

## 7.2 Document 3 基础信息

页面右上方展示：

```text
document uuid
生成时间
当前状态：现行 / 历史
```

左上角提供历史记录侧边栏按钮。

历史记录交互与投研资料页面保持一致。

## 7.3 Known Events 展示

Known Events 采用纵向小卡片列表。

每个 event 小卡片默认展示：

```text
event 名称
最近更新时间
```

展开后分开展示：事件描述、相关expectation unit等信息

要求：

* 每个 event 可独立展开 / 收起；
* 空列表时显示“暂无 Known Events”。
* 支持按expectation unit筛选；

## 7.4 Monitoring Execution Policy 展示

Policy 采用卡片列表展示。

每条 policy 默认展示：

```text
policy id
动作类型：DTC / EBA / NULL / Irrelevant
策略标题
最近更新时间
```

展开后展示完整触发条件等信息

要求：

* policy 可展开 / 收起；
* 支持按动作类型筛选；
* 空列表时显示“暂无执行策略”。

---

# 8. 消息总线页面需求

## 8.1 页面定位

消息总线页面用于查看当前 ticker 的消息采集、标准化、去重、正文补全、相关性过滤与持久化事件流状态。

布局参考当前 Message Bus Control Plane 页面，该页面目前有的组件和功能都要有，但是不要1：1复刻，要做得更漂亮。新 dashboard 中不使用左侧切换栏。

## 8.2 页面布局

页面顶部展示一组简化 KPI：

```text
启动时长
今日原始消息数
进入事件流数量
正文补全成功率
状态正常channel数（如5/6）
```

页面主体展示 Live Message Stream。

右上角放置齿轮 icon，点击进入或切换至 Config 页面。

## 8.3 Live Message Stream

消息流以卡片列表展示。

每张消息卡片默认展示：

```text
来源
抓取时间
标题
摘要
```

点击消息卡片箭头后展开详情。

展开后展示：

```text

完整正文
原链接按钮
当前处理状态
等
```

要求：

* 支持按来源筛选；
* 支持按处理状态筛选；
* 支持关键词搜索；
* 支持时间倒序；
* 新消息通过 SSE 追加；
* 页面保留手动刷新按钮；
* 消息列表需要分页或滚动加载，避免一次性加载过多。

## 8.4 Config 页面

点击右上角齿轮 icon 后进入配置视图。Config 页面展示当前 ticker 的消息源配置，参考现有Message Bus Control Plane 页面。

---

# 9. 运行状态页面需求

## 9.1 页面定位

运行状态页面用于展示 DoxAgent 持久化监测执行链路，以及消息从 Message Bus 进入 W1、W2、O3、交易记录或其他 agent 的流向。

该页面是 runtime observability 页面，不是复杂调度后台。

## 9.2 顶部 KPI

页面顶部展示：

```text
当前队列消息数
W1 今日处理数|平均处理时效
W2 今日处理数|平均处理时效
O3 今日处理数|平均处理时效
DTC 今日数量
EBA 今日数量
失败任务数
平均处理延迟
```

## 9.3 链路图

主体展示一张运行链路图，参考用户提供的内容平台人审链路图形式。

第一阶段链路节点包含：

```text
Message Bus
W1 新旧判定
W2 Policy 判定
O3 值班专家
交易记录
委托 O1 / A2
结束 / 忽略
异常队列
```

每个节点展示：

```text
节点名称
当前状态
进入数量
产出数量
失败数量
```

节点之间的连线展示：

```text
消息流向
流量数字
```

状态颜色：

```text
正常：绿色
异常：红色
无数据：灰色
```

## 9.4 节点详情

点击节点后，右侧展开详情面板。

详情面板展示：

```text
节点名称
当前状态
最近处理时间
今日处理数量
今日失败数量
平均延迟
最近错误
最近处理记录列表
```

第一阶段不要求展示完整复杂 trace，只需要能看到关键输入、输出、状态和错误原因。

---

# 10. 收益 / 成本审计页面需求

## 10.1 页面定位

收益 / 成本审计页面用于回答两个问题：

```text
DoxAgent 当天产生的交易意图是否有效？
DoxAgent 当天运行花费是否可控？
```

页面分为两个板块：

```text
收益审计
成本审计
```

通过一个按钮切换。

## 10.2 收益审计

### 10.2.1 审计触发

交易日监测覆盖期结束后，系统自动执行当天收益审计。

默认规则：

```text
交易日 18:00 后触发审计
```

具体时间以系统配置为准。

当前退出策略：

```text
收盘前10min全仓卖出
```

收益审计需要基于：

```text
交易意图生成时间
理论买入价
实际买入价/估算滑点
卖出价
收益 / 亏损
```

### 10.2.2 收益审计概览

展示 KPI：

```text
今日交易意图数
已审计交易数
今日收益
今日收益率
胜率
审计状态：未开始 / 计算中 / 完成 / 失败
```

### 10.2.3 收益趋势图

展示可切换周期的趋势图。

周期选项：

```text
今日
近7日
近30日
```

图表内容：

```text
收益趋势
交易意图数量趋势
```

第一阶段不要求复杂归因图。

### 10.2.4 交易意图列表

以表格或卡片展示当天交易意图。

字段：

```text
时间
ticker
触发消息
触发 policy
动作
买入价
卖出价
滑点
收益
状态
```

点击单条记录后展示详情：

```text
触发原因
相关消息
agent 输出摘要
```

## 10.3 成本审计

### 10.3.1 成本概览

展示 KPI：

```text
今日 token 总量
今日总成本
按 ticker 成本
按节点成本
成本最高节点
异常重试成本
```

### 10.3.2 成本趋势图

展示可切换周期的趋势图。

周期选项：

```text
今日
近7日
近30日
```

图表内容：

```text
总成本趋势
token 使用量趋势
```

### 10.3.3 成本占比图

展示成本构成比例。

维度：

```text
按 agent / worker 节点
按模型
按 ticker
```

第一阶段至少支持其中两个维度：

```text
按节点
按模型
```

### 10.3.4 成本明细表

字段：

```text
时间
ticker
节点
模型
input tokens
output tokens
成本
是否重试
状态
```

要求：

* 支持按 ticker 筛选；
* 支持按节点筛选；
* 支持按模型筛选；
* 支持时间倒序；
* 支持查看失败或重试记录。

---

# 11. Dashboard State API 需求

## 11.1 基本原则

前端不直接读取 DoxAgent 内部 workflow 表。

前端只通过 Dashboard State API 获取数据。

Dashboard State API 负责：

```text
聚合状态
转换字段
隐藏内部复杂结构
提供稳定前端 schema
提供中文 label
提供分页和筛选
提供 SSE 事件流
```

## 11.2 API 能力范围

第一阶段需要支持以下数据能力：

```text
全局 overview 状态
ticker 列表与运行状态
启动 / 暂停 / 删除 / 重启 ticker 监测
Document 1 / 2 / 3 当前版本
Document 历史版本列表
Known Events 列表
Policy 列表
Message Bus 消息列表
Message Bus 配置状态
Runtime 节点状态
Runtime 处理记录
收益审计结果
成本审计结果
SSE runtime event stream
```

## 11.3 前端字段稳定性

为了降低维护成本，Dashboard State API 应尽量输出适合前端直接渲染的数据结构。这样 Document 1 / 2 / 3 字段变化时，前端不需要频繁大改。

---

# 12. UI 与交互规范

## 12.1 语言

前端页面语言必须为中文。但可保留部分原本就是英文的内容，如专有名词、英文消息等。

## 12.2 视觉风格

整体风格参考 doxatlas_design.md。

## 12.3 颜色规范

统一状态颜色：

```text
绿色：正常 / 成功 / 运行中
蓝色：处理中 / 信息
黄色：等待 / 延迟 / 警告
红色：失败 / 异常 / 阻塞
灰色：历史 / 停止 / 无数据
```

---

# 13. 第一阶段交付边界

第一阶段需要做成一个完整可用的前端，而不是只做页面骨架。

必须完成：

```text
可登录
可进入 agent.doxatlas.com
可查看 Overview
可启动新的 ticker 监测
可查看 ticker 状态卡片
可暂停 / 删除 / 重启监测
可进入具体 ticker 页面
可查看 Document 1 / 2
可查看 Document 3 的 Known Events 和 Policy
可查看 Message Bus 消息流
可展开消息原文
可跳转原文链接
可查看 Message Bus 配置状态
可查看 Runtime 链路图
可查看 W1 / W2 / O3 等节点状态
可查看收益审计结果
可查看成本审计结果
可展示趋势图和成本占比图
支持 SSE 实时更新关键事件
支持轮询刷新状态快照
```

---

# 14. 建议开发顺序

为了降低开发风险，建议按以下顺序开发，但最终仍作为一个完整第一阶段交付。

## Step 1：基础框架

```text
子域名访问
Supabase dev 鉴权
React 页面框架
顶栏导航
Dashboard State API 接入
基础样式系统
```

## Step 2：Overview

```text
KPI 卡片
启动 ticker 监测
ticker 状态卡片
暂停 / 删除 / 重启
点击进入 ticker dashboard
```

## Step 3：Document 展示

```text
投研资料页面
执行策略页面
卡片展开 / 收起
历史记录侧边栏
Known Events 列表
Policy 列表
```

## Step 4：Message Bus

```text
消息总线 KPI
Live Message Stream
消息详情展开
原文链接
筛选 / 搜索
Config 视图
SSE 追加消息
```

## Step 5：Runtime Execution

```text
运行状态 KPI
链路图
节点状态
节点详情面板
最近处理记录
异常状态展示
```

## Step 6：收益 / 成本审计

```text
收益审计 KPI
收益趋势图
交易意图列表
成本审计 KPI
成本趋势图
成本占比图
成本明细表
```

---

# 15. 验收标准

## 15.1 基础验收

```text
用户可通过 agent.doxatlas.com 访问 dashboard
非 dev 用户无法访问
dev 用户可正常进入
页面语言为中文
UI 风格与 DoxAtlas 保持一致
```

## 15.2 Overview 验收

```text
能够看到全局运行 KPI
能够启动新的 ticker 监测
能够看到所有 ticker 状态卡片
能够暂停 / 删除 / 重启 ticker 监测
点击 ticker 卡片可进入对应 dashboard
```

## 15.3 投研与策略验收

```text
能够查看 Document 1
能够查看 Document 2
能够查看 Document 3 的 Known Events
能够查看 Document 3 的 Monitoring Execution Policy
能够切换历史版本
卡片可展开 / 收起
长文本展示正常
```

## 15.4 消息总线验收

```text
能够看到实时消息流
新消息可通过 SSE 追加
消息可展开查看原文
消息可跳转原链接
能够按来源 / 状态 / 关键词筛选
能够查看消息源配置状态
```

## 15.5 运行状态验收

```text
能够看到运行链路图
能够看到 W1 / W2 / O3 / 交易记录等节点
能够看到节点进入量、产出量、失败量
能够点击节点查看详情
异常状态能够明确展示
```

## 15.6 收益 / 成本审计验收

```text
交易日结束后可生成收益审计结果
能够看到交易意图列表
能够看到收益趋势图
能够看到今日 token 和成本
能够看到成本趋势图
能够看到成本占比图
能够筛选成本明细
```

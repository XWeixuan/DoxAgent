# DoxAgent V2 真实数据本地联调

交付日期：2026-09-08。环境已启动，可直接使用：

- 前端：<http://127.0.0.1:5178>
- 实际 V2 API：<http://127.0.0.1:8098/api/doxagent/v2>
- 本机状态：<http://127.0.0.1:8098/__integration/health>
- 持久化数据：`C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp\v2-integration\mu`

本环境使用当前 `api_v2.create_app`、原生 Message Bus / Runtime / Control / Initialization SQLite 表、SourceOutbox、FormalProjectors 和 ReadStore。消息流及 Runtime 图均使用现有生产 SSE 实现，经过 Vite 代理。没有直接拼造前端 DTO，也没有使用 `frontend/v2/mock/data.py`。

Mock 边界是外部采集与模型执行：消息输入和已完成的模型回执从本地录制包读取；本期仅消息监测，不消费 Policy，不产生新 intent，不启动券商、爬虫、Codex 或模型执行进程。数据库和 API 的读取、配置写入、控制操作、事务、投影、游标及 SSE 都实际运行。

## 启停与回放

以下命令在仓库根目录 PowerShell 执行：

```powershell
# 首次自动构建；已有数据包直接启动，绑定 127.0.0.1。
./scripts/start_v2_integration.ps1

# 查看进度、控制状态、源 outbox 与投影位置、错误。
./.venv/Scripts/python.exe -m doxagent.integration_v2 status

# 开始自动回放，每 4 秒推进一个阶段。
./.venv/Scripts/python.exe -m doxagent.integration_v2 start --interval 4

# 停止自动推进；保持 API、数据库、SSE 可用。
./.venv/Scripts/python.exe -m doxagent.integration_v2 pause

# 单步：idle -> 消息入库/Case RUNNING -> 录制 W1/W2 结果入库。
# 完整推进一条消息需要执行两次。
./.venv/Scripts/python.exe -m doxagent.integration_v2 step

# 仅停止启动脚本记录且核对过身份的两个本机进程；保留数据库。
./scripts/stop_v2_integration.ps1
```

交付时已载入 **12 / 25** 个案例，另有 **13** 个待回放，自动回放处于暂停状态，MU 处于 `MESSAGE_MONITORING / RUNNING`。回放进度记录在 `replay.sqlite3`，进程重启继续原位置，结束后进入 `exhausted`，不会自动重复制造调用费用或新消息。

页面沿用正式的统计周期规则。历史研究可直接打开；查看这批回放消息/Runtime 时选择“全部”或当前交易日。默认“上一交易日”没有这批新入库消息属于正常结果，不能因此补造昨天的记录。消息原始发布日期、模型调用时间和 token 数保留原值；消息采集、Case 接管和本地完成时间是回放时间，Case 中同时保存原评测起止时间。

前端的标的“暂停 / 再次启动”调用正式 API，并控制回放的新分析接管。CLI 的 `pause` 仅暂停录制推进，含义不同。消息源 `enabled` 开关由实际 Binding 数据决定；默认录制源使用 IMMEDIATE 发布。改变为 BUFFERED 等尚无对应录制发布过程的配置，不属于本数据包的回放覆盖，错误会留在状态中，不伪造发布成功。

需要从头回放时，新建数据目录，不覆盖已用作联调证据的数据库：

```powershell
./scripts/stop_v2_integration.ps1
./.venv/Scripts/python.exe -m doxagent.integration_v2 build --root .tmp/v2-integration/mu-next --seed 10
./scripts/start_v2_integration.ps1 -Dataset .tmp/v2-integration/mu-next

# 对新目录操作时，同样指定 --root；停止时同样指定 -Dataset。
./.venv/Scripts/python.exe -m doxagent.integration_v2 start --root .tmp/v2-integration/mu-next
```

新包默认预载 10 条；`--seed 0..25` 可选。原始输入路径不可用时，已构建的包仍可独立启动和回放；重新构建才需要原始证据。端口已有其他进程时脚本不会将其终止。已有前端启动进程时，请先用 stop 脚本停止本环境再重启。

## 数据内容与证据

`manifest.json` 记录原始路径、SHA-256、数据类型、组装方式和覆盖限制；可复制的正文/JSON 证据保存在 `evidence/`。SQLite 来源使用在线 backup API 获取一致快照，包含当时 WAL 中已提交内容；原始数据库不安装触发器、不迁移、不写入。

| 内容 | 实际来源及联调覆盖 |
| --- | --- |
| D1 | `mu-global-formal-20260820-01`；C1/C3/C5 正文、引用及 26 个未来节点。正文与原 ArtifactRef 校验一致。 |
| D2 | `mu-document2-minimal-20260824-01`；原 Pilot 冻结文件，1 个 Shell、4 个 ExpectationUnit，供 Shell/Unit/状态/差异等按需读取。 |
| D3 | `d3-mu-real-acceptance-2026090301-sol-high-or-v2` 的真实发布数据库；42 个 Policy，原 PARTIAL 状态、coverage 与 runtime projection。 |
| Event Library | W1/W2 真实评测的冻结 v1 SQLite；185 个 Event、518 个 Event-Fact 成员行。保留旧数据缺失时间及不可证明的历史 birth provenance。 |
| W1/W2 | `mu-w1w2-real-20260908-01` 的 25 个真实模型评测回执；保留 case/turn/response ID、判断、理由、轮次、重试、token 与实际调用时间。交付时原生库中有 12 个 Case / 42 个模型 Turn。 |
| 消息输入 | 同一评测集 `messages.jsonl`。这些是明确标记 `synthetic: true` 的未来情景测试输入，不是真实市场新闻。模型输出是真实执行结果。录制 URL 使用 `.invalid` 域名，只作本地身份。 |
| W3 | 已保存 `.tmp/persistent-runtime-w3-pilot/report.json` 的真实结果证据；其 D1/D2 pin 不同，不作为本批 MU Case 的已完成 W3 结果。 |
| O4 | 已保存 `mu-o4-pilot-002` configure/deliver 回执证据；对应另一 PolicySet 哈希，且 delivery 有 `HUMAN_INTERVENTION_REQUIRED`，不冒充本批激活版本已全部交付。 |

D2 的实际输入来自 `D:/DoxAgentPilot/cases/document3/d3_o3_initialize/mu-d3-pilot-001-01-o3_initialize/context/document3/document2.json`，其 SHA-256 为 `6f7db1c39f330283d34ff324f4e927a07568636cdf676ba91dcb7006fe7ded0f`，与 D3 pin 完全一致。O3 工作目录中再序列化过的同名文件哈希不同，未用它替代原始发布正文。

`integration-mu-frozen-20260908` 是**本地组装激活版本**。它通过原生 activation 表及正式投影器注册上述真实版本，不伪装成历史上完整成功的一次初始化。D2 恢复的发布元数据来源及缺失 citation manifest 保持显式状态。没有完整初始化运行、同 pin W3、真实 Entry/Exit/Fill/PnL 的部分保持 NOT_PRODUCED / UNKNOWN / 空结果；预测 TRADE 不等于真实成交。W1/W2 的 W3 路由保持待处理，未编造后续执行或 Delta 接管回执。

## 文件与实现入口

- `src/doxagent/integration_v2/dataset.py`：真实证据校验、原生库组装及初始投影。
- `src/doxagent/integration_v2/runtime.py`：录制阶段推进、真实 Bus 接入、Case/Turn 写入、投影和控制门禁。
- `src/doxagent/integration_v2/__main__.py`：独立本地入口、回放控制及进程生命周期。
- `frontend/v2/integration/auth.ts`：仅 `DEV && MODE === integration` 使用本数据包身份；正式 Supabase 登录代码不变。
- `scripts/start_v2_integration.ps1` / `stop_v2_integration.ps1`：端口检查、隐藏启动、进程身份校验及保留数据的停止。
- `scripts/smoke_v2_integration.py`：按需验证脚本，会消费一个待回放情景。

目录中的 `research.sqlite3`、`events/US/MU/event_library.sqlite3`、`bus.sqlite3`、`runtime.sqlite3`、`initialization.sqlite3` 为原生源库；`read.sqlite3` 为实际页面投影库；`replay.sqlite3` 仅存联调推进进度。`recordings.json` 为录制输入及模型回执，`logs/` 含进程输出和归属记录。

本机 bearer token 在 `token.txt`，由启动脚本仅传入前端子进程；目录被 Git 忽略。这里验证的是正式 API 的鉴权注入边界和权限处理，**未验证真实 Supabase 登录/RLS/远端写入**。本地身份不得接入生产 API。新 ticker、重新初始化、Paper/Live 执行请求会得到明确的录制范围/外部执行不可用操作结果，不触发新研究或交易。

## 已完成的必要验证

证据文件：`.tmp/v2-integration/mu/validation.json`。

- 经 **5178 前端代理**完成 25 个针对性读取：八页 ReadContext、页面主资源、D1 正文/未来节点、D2 Shell/Unit、Policy、Event/Fact、Case/Attempt、成本。
- 实际 HTTP 流验证消息 SSE、Runtime 图 SSE，以及两者的 `Last-Event-ID` 断线续传；序列递增、源位置追平、投影 gap 为零。
- 实际重启验证持久化；正式 API pause/restart 完成，暂停时单步未接管新 Case；保持 Policy 消费记录为零。
- 前端 TypeScript 检查通过，新增 Python 入口及投影适配静态检查通过。
- 修复真实数据暴露的两个 schema 适配问题：D1 中文 FutureNode alias 转英文 DTO、旧 CanonicalFact 缺失可空字段补显式 null。正文、原始 SHA 和事件源库均不重写。
- 修复研究摘要中内嵌 run 的激活标记滞后：读取同一快照下的 run 投影，保持 current 和历史列表一致。

这不是全部前端业务验收：未重新进行逐页浏览器视觉检查，未运行广泛回归/压力测试，未连接远程 Supabase、外部模型或券商。后续联调可直接基于此环境复现缺失字段、分页、缓存及 SSE 问题。

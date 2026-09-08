# DoxAgent V2 后端交付记录

日期：2026-09-07。依据：`DOXAGENT_V2_BACKEND_IMPLEMENTATION_PLAN.md` 和已冻结 Q1–Q4。

## 已落地代码

| 部分 | 代码入口 | 内容 |
|---|---|---|
| API | `src/doxagent/api_v2` | 独立 FastAPI、Supabase 开发者认证、79 个契约路由、闭合响应 schema、控制/Binding 请求、详情/下载、分页、ETag、Message/Graph SSE |
| 控制面 | `src/doxagent/v2_control` | 幂等操作、修订 CAS、epoch gate、跨库步骤及 ACK、停止/重加隔离、ticker profile binding 和本地管理入口 |
| 读服务 | `src/doxagent/v2_read` | 同事务源 outbox、MVCC 对象、指标贡献修订、gap 隔离、正式工件索引、Policy/Event 生命周期、Reference 前后镜像及日净变化 |
| 原领域接线 | 初始化、Message Bus V2、Persistent Runtime V2 | 分析资格冻结、消息监测不消费 Policy、正式 intent 释放事务、新调用 gate、真实时间和用量回执 |
| 执行读取 | `v2_read/executions.py`、`pnl.py` | 正式 intent/执行接纳分离、有效 Fill correction、owned EXIT 成本分摊、迟到佣金归回原日 |
| 运行入口 | `runtime_scheduler/v2.py`、`persistent_runtime_v2/delivery_worker.py` | V2 调度不构造 V1 服务；全部 ticker 暂停后旧 intent 仍独立交付 |
| 运维 | `v2_read/cli.py`、`maintenance.py`、`history.py` | 显式迁移、SQLite 一致性备份、历史归属清单、有界回填、影子 generation 重建/验证/alias 切换、24h 快照保留清理 |
| 部署资料 | `docker-compose.v2-backend.yml`、[RUNBOOK.md](RUNBOOK.md) | 进程职责、路径与权限、上线次序、恢复和回退规则 |

修改已追加到仓库 `changelog`。未覆盖并行工作的前端文件；未创建提交、执行生产迁移、部署服务、启动研究或发送 broker 订单。

## 已取得的验证证据

- 契约结构：79/79 路由已注册，见 [route-coverage.json](route-coverage.json)。该报告只证明路由/OpenAPI 覆盖。
- 后端专项：最近一次完整专项运行 **37 passed**，见 [backend-tests.xml](backend-tests.xml)。之后的收尾改动没有重新跑整套测试。
- 原领域回归：**206 passed、8 failed**，见 [domain-regression.txt](domain-regression.txt)。8 个失败均在隔离 HEAD 上复现，见 [baseline-regression.txt](baseline-regression.txt)，涉及既有路由矩阵/W3 状态断言；没有为了使旧断言通过而改回旧业务规则。
- Reference 验证覆盖同 Library version 下真实成员移除、同日先增后删的净变化，保留实际 O3 输入前镜像。
- 本地规模测量：1 万/10 万消息及 Case，见 [performance.json](performance.json)。10 万规模列表返回约 20 KB，ALL messages 读取聚合桶；记录的基线/列表路径没有读取完整正文。SSE 基线仍约 247 ms，是本机观测值，不能当生产 p95 或完整压力验收。
- 根据用户随后要求，停止追加、扩大及重复测试；不将 T01–T32 全矩阵标为全部通过。

## 当前交付边界与未收口事项

本次代码覆盖了主要后端链路，但**还不能将方案 §14 的全部完成条件标为通过**。以下事项需要在继续开发/联调时明确保留，不能用“历史 coverage 缺失”笼统掩盖：

1. Policy 指标的前窗比较、部分生命周期指标完整覆盖证明尚未全部接通；部分响应仍返回不可比较/未知覆盖。计数已有真实来源，但并非所有窗口均达到完整契约语义。
2. 用量观察已接入 Runtime、初始化 API/Codex 和 CDECR 的真实调用边界；所有维护节点的采集完整性、进程中断时的缺口报告仍需进一步核对。缺失 usage 不默认 0。
3. ALL Cost trend、复杂筛选和部分大详情的有界读取仍有优化空间；本次基准没有覆盖每个接口及极端大对象。不会把小响应体当作来源读取有界的充分证明。
4. Binding 通用 JSON 编辑及参数脱敏/CAS 已实现，专用参数表单适配尚未全部提供。历史导入目前支持精确 Case、StandardMessage、初始化 run、activation 分类，未实现任意历史对象的自动归属推断。
5. 后续收尾加入的历史导入、部署 overlay、V2 scheduler 实际组合及部分 intent/Case 详情改动尚未重新进行端到端运行验证。现有 OpenAPI 导出是此前审计快照，新加入的参数描述可从当前 app 重新生成。
6. 真实前端联调、部署探针、真实模型研究、Paper 成交、Live 运行均未执行；不宣称已部署或交易可用。

本记录区分已实现、已验证与仍需收口内容。运行与恢复命令见 [RUNBOOK.md](RUNBOOK.md)，契约以 `../DOXAGENT_V2_API_CONTRACT.md` 及其配套类型为准。

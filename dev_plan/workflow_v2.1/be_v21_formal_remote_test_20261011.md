# BE 正式 v2.1 远端初始化（2026-10-11）

- 正式D1→D2→D3；跳过CDECR/O2/O4，生产v2.0不迁移。
- prefix `be-v21-20261011-01`；截止 `2026-10-11T15:54:22.932336+00:00`（23:54 CST）。
- 代码 `b20c4aca`，独立检出 `/home/ubuntu/doxagent-be-v21-20261011`；生产HEAD仍 `b0f1caf50060b34483f13020a9d173ef57933afa`。
- 独立SQLite `/data/be-v21-test/research.sqlite3`（生产在线backup，不覆盖原库）、published `/data/be-v21-test/published`、Worker `doxagent-be-v21-worker`、JobStore `/data/be-v21-test/workspaces/worker-jobs.sqlite3`。
- 新镜像 `doxagent-v2:be-v21-20261011` 包含最新/app/src、prompts、scripts；Data MCP健康检查通过。空provider不写入env，包含此前workspace GET重试修复。
- Event Library BE v1，只读；发布2026-09-21T16:34:30.976275+00:00，SHA256 `857e3d937a7c3ba921729c4f53bd1ac2c59f9fc4698565f80a4667b945b35a2a`。
- 模型逐节点对齐当前本地node_models.py：核心Sol6.1/medium；C4network Sol6.1/high；C4pre/Future/formal、O0候选审查、D3Build Luna/max。D2/D3并发4，startup3，launch wave3、间隔2秒，timeout1800、attempts2。
- 已启动正式D1容器 `doxagent-be-v21-01-d1`，ID `1a4b2469ddccdc66fff2feb386cf4cbbeddbc6af4a7b20ea19ac9c0c96676657`；首次检查处于输入准备、暂无Worker Job，后续核验实际模型请求。
- O0前15分钟轮询；D1发布后start_stage.py d2-o0，落盘停点实物核验3–6 shells，合格才同run启动d2并改30分钟；超范围停止汇报。D2发布后启动d3。错误同run断点恢复，不重做成功节点。
- 复用已验证O0门控；相关门控+传输测试11 passed，Ruff通过。启动路径拒绝同名容器重复创建。
- heartbeat `be-v2-1` ACTIVE，每15分钟，正常无变化静默。生产仓库/服务未由本线程修改。

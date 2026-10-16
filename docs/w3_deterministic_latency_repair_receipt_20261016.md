# W3 确定性耗时与 Fork 恢复：实现记录

日期：2026-10-16。依据 `dev_plan/workflow_v2/w3_deterministic_latency_fork_repair_plan_20261016.md`。

## 已实现

| 工作包 | 实现与边界 |
|---|---|
| A：Event 身份 | 删除每 Case 的全 ticker 历史扫描。当前消息解析一次复用；按 lineage/root/ticker/固定版本缓存；别名批量写入，仅同步仍活跃且未过期的 Case Thread，出版同步有版本 watermark。 |
| B：稳定资产 | 新增持久化研究派生缓存、独立 Reference 缓存和 Worker Pack 安装/绑定。研究、Reference、公共指令分别版本化；首次安装原子发布，Case 私有复制并保留 fsync。热路径单次 materialize，不再逐文件 HTTP PUT；不同 Case 不共用可写 inode。 |
| C：Template | `instructions-only-v1`，key 和 Case compatibility 均隔离旧格式，只受 AGENTS/唯一 Skill、ticker、模型和能力合同影响。预热和普通 Case 使用相同指令筛选，sweep 不进入 Template key。模板初始化关闭 Web/MCP；交易恢复当次既定权限。公共资产先准备，模板模型异步预热，不阻塞新的资产准备。 |
| D：准备/恢复 | 使用原 `w3_thread_operations` 新增 prepare/GET 查询协议；先保存 record/operation/冻结 prepare 参数再 POST。异步接受后，同一 Capsule/SDK Fork 并保留 child，首轮直接 turn。响应丢失/重启按同 key 接回；查询不调用 SDK。旧 canonical request 不覆写，已完成 legacy Fork 可直接适配。 |
| D：进程与资源 | 预热少量无 Case 的 Python Capsule，沿用 launch_wave_size/startup semaphore/安全状态。已准备好的 W3 不再等待冷进程 launch ramp；其他节点的启动规则保持。正常新 Fork 不走独立 helper；status/delete 使用请求独占的有限时 SDK 会话，无全局生命周期锁。删除和回收不持 pool 全局锁、不在 async 主循环做目录删除；清理按 20 条游标批次推进。 |
| E：初始化 | 移除 tools 包和 SDK 到全量 factory/server 的隐式导入；D3 全目录权限元数据延迟到对应节点请求时构造。W3 SDK 与 MCP 均只构建同一权威行情 contract/client。IBKR 工具全部走 TWS socket，不再创建从未使用的 HTTPX/CA 对象，session/行情调用行为保持。常驻 MCP 按签名 scope 文件刷新 attempt/cutoff/观察目录，避免运营或关联 Case 沿用旧 scope。 |
| F：入口/状态 | 路由、运营提交后合并唤醒独立 effects executor；周期补偿在 reconcile 前触发；先启动 W3，再处理同步的其他副作用。记录非空错误、phase、step、operation；失败同时更新主 Case、route、stage 和读模型。准备失败没有模型任务时，流程图仍归属 W3，而不是误归 W2。已提交交易的运营失败独立显示。 |
| 回测 | 本地 embedded adapter 接入同一 prepare/Fork 路径，仍禁用 Web/MCP/IBKR。回测 fingerprint 加入编排/Worker 源码，改变实现必须新建 run。 |
| 历史恢复 | 新增 `persistent_runtime_v2/w3_recovery.py`：核验原 Case/Effect/record/parent/workspace/child/pin，保留截止时间和 TTL，原子接回原 Effect；不重跑 W1/W2、不生成新 Fork、不延长交易有效期。 |

没有修改 W3 的业务 AGENTS/Skill、交易输出 schema、模型分类、交易资格、消费/授权/去重规则。工作区原有 Document2 提示词修改未纳入本轮变更。

## 验证证据

证据位于 `eval/w3_deterministic_repair_20261016/`。

- **204 项相关测试通过**，覆盖后端、回测、权限、Worker 资源治理、IBKR provider 和读模型；见 `tests-final.txt`。包含 16 项本轮新增修复测试及前轮 8 项探针测试。
- 两个 ticker 各 6 Case，真实 Worker API、SQLite、Pack/materialize、阶段提交，仅 SDK 执行模拟为 20 毫秒：程序非 SDK 开销 p90 **4.225 秒**，总突发完成时间 **4.387 秒**。运行于远端独立工作区和真实数据卷，未操作生产 Case。
- 上述突发实验使用小型固定研究 fixture、预先准备的公共 Template，不能替代真实 SDK/MCP 冷回退及真实研究体量的生产 SLO 验收。Windows 同一功能测试更慢，未以 Windows 文件系统的耗时代替远端指标。
- 真实生产容器中的新代码隔离导入：Runtime 构造 **13.7–23.3 毫秒**，原测量约 4.4–4.8 秒；全量 factory 不再加载。Data MCP 导入 **1.67–2.14 秒**，原测量约 4.9–6.9 秒。
- SDK 冷模块导入仍为 **4.89–5.95 秒**，由预热移出多数 Case 路径；冷回退仍计入总体 SLO，不能记为零或排除。这一残余成本尚未通过生产自然流量验收。
- 私有 CODEX_HOME、真实 SDK、零模型 turn：新 Runtime prepare/Fork **0.918 /0.212 /0.120 秒**。真实 Capsule line protocol 复测：无 MCP **0.429 秒**，启用 W3 MCP **3.176 秒**；三只 Python Capsule 的公共预热本身约 **19.24 秒**，这一成本若落入冷 Case 仍须计入统计。
- MCP 新边界探针显示该次模块导入 **2.459 秒**、Application 构造 **0.181 秒**，有完整 scope 关联；详见 `mcp_bootstrap.json`。初轮 Capsule 隔离测试未显式固定 MCP 子进程的 overlay 路径，出现过超时；固定私有配置、验证 `w3-lite-v1` 启动 receipt 后通过。没有通过增大 10/30 秒预算掩盖该问题。
- 重新用真实固定出版数据测试 context/cache/派生，结果见 `preparation_profiles.json`。其中 `mapping` 是故意调用旧式全历史集合的诊断实验，新热路径已经移除该调用，不能把这个字段算成新 Case 成本。

没有调用生产模型研判、券商 API 或执行生产 Case 恢复。远端写入只限于明确新建的隔离实验目录，未替换运行中的生产程序。额外核验并清理的进程仅为明确命名的实验 Capsule，生产进程未动。

## 发布与历史恢复

代码完成与生产修复生效是两个阶段。本轮未 git push、构建或部署；历史 38 条尚未实际接回。部署仍按方案：Worker 兼容端点先发布，Scheduler 后发布，随后验收真实流量并恢复历史记录。

上线后在 Scheduler 相同配置环境中执行恢复工具，输入为之前导出的 `orphans_verified.json`：

```bash
# 导出并核验当前恢复 manifest，不提交模型任务
python -m doxagent.persistent_runtime_v2.w3_recovery \
  --evidence /path/to/orphans_verified.json --manifest /path/to/w3-recovery.json

# 只接回首条，交给现有 Scheduler 领取原 Effect
python -m doxagent.persistent_runtime_v2.w3_recovery \
  --evidence /path/to/orphans_verified.json --manifest /path/to/w3-recovery-first.json \
  --apply --limit 1
```

先核验首条原 child、Worker job、阶段结果和最终 disposition，再按剩余清单处理。不恢复已有决策或交易，不更换冲突记录的 key；原交易有效期、授权与去重仍由现有提交事务处理。恢复工具不自行广泛 pump 其他生产 Effect。

最终上线验收仍需覆盖真实普通/HIT/RELATED、Fork/Resume、热/冷 Capsule、资产切换和突发；保留失败/partial 数据，按原 Effect 创建到交易提交减去实际 SDK 交易 turn 区间并集计算。预热失败按既有启动节奏和短退避补充，不能出现持续重启风暴；真实请求仍可按资源治理走冷启动。

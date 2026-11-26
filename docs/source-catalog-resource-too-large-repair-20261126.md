# 消息源目录 RESOURCE_TOO_LARGE 修复与验收

## 问题与根因

2026-11-26 北京时间，真实 DoxAgent MCP `doxagent_get_overview(section="sources")` 首页复现 `RESOURCE_TOO_LARGE`，请求 ID `9d721a9c27d64d589fcbf41ea8500617`。

直接检查当前生产目录得到 **108 个消息源、286,048 字节**（`catalog_data` JSON，尚不含响应 envelope）。全局目录 API 沿用完整快照协议，却套用了普通列表 **262,144 字节**预算；最近目录扩容后已越过上限。API 在返回完整目录时即拒绝，因此 MCP 尚未进入自己的分页逻辑。真实 API 客户端也返回同一错误，排除了“仅工具结果过大”或减少 MCP limit 即可解决的解释。

这是序列化响应预算不匹配，不是本次磁盘耗尽或目录数据库不可读取。观察时根盘约 38 GiB 可用；没有执行清理，也没有变更采集/Runtime 数据。

## 修复

实现提交 **`379a0eb65`**：仅为现有完整 `SourceCatalog` 快照使用 **1 MiB** 的有界对象预算，并在 API 合同中明确记录这个例外。普通 API 列表仍为 256 KiB，MCP 每页仍为 128 KiB；超过目录独立上限继续显式拒绝，不截断字段或伪报完整。若未来超出该上限，应实施端到端分页协议，不继续提高全局预算。

保留当前 24 小时指标、所有消息源、鉴权、只读执行器、延后读取时间与容量限制，以及 MCP 对同一快照的既有续页。没有修改 MCP/前端代码，没有新增表、索引或后台预热任务。

## 验证与上线

**33 项定向回归通过**，覆盖真实 HTTP envelope 到 MCP 分页、完整 108 源/无遗漏无重复、中文 UTF-8 字节、相同快照、续页不重读目录、目录硬上限，以及普通列表仍受 256 KiB 限制。并运行既有目录、延后读取、MCP 只读与字段适配回归。新增测试/发布脚本 Ruff 与 `git diff --check` 通过；保留已有依赖弃用提示。

仅更新 API 一个容器，在实际在用 Biopharma API 镜像之上覆盖 `src/doxagent/api_v2/app.py`。覆盖前核对源文件与父提交逐字节一致，采用既有 canonical 发布锁、状态登记、健康检查与回退记录；部署后核对新源文件 SHA256。其余 **20 个**在运行容器身份均未变化，包括 MCP、Web、Collector、Bus、Scheduler、Worker。

- API 基线镜像：`sha256:acb8277f958f06ecd0d48aea591b4a656e3ef1a32ea74bd98e45ed46e76ead98`
- 新 API 镜像：`sha256:1e10473f02792d538d241633355a7f23dadff82d9304af5b31a224486caff277`
- 新源文件 SHA256：`930a81d88f93780fd1536b55e4f59550a46413e7e2a244b7f091a6591f6f9bef`
- 最终 API 状态：running、healthy、restart_count=0。
- 私密 canonical 回退资料保留在 `/var/lib/doxagent/releases/catalog-budget-379a0eb65/`；没有提交凭据或完整 Compose 配置。

### 真实巡检通道

北京时间 **2026-11-26 07:18**，通过当前连接的真实 DoxAgent MCP 通道重新调用默认首页，再按原 cursor 读取全部续页：

| 页 | 消息源数 | 工具结果 UTF-8 字节 |
| --- | ---: | ---: |
| 1 | 20 | 67,781 |
| 2 | 20 | 34,679 |
| 3 | 20 | 36,030 |
| 4 | 20 | 29,488 |
| 5 | 20 | 30,267 |
| 6 | 8 | 14,255 |

共 **108 个唯一源**，无重复/遗漏，六页保持同一 `snapshot_id`，末页 `has_more=false`，每页均低于 128 KiB。首页约 12.13 秒，`refresh_error=null`，没有再次出现 `RESOURCE_TOO_LARGE`。

另用生产 MCP 容器内既有只读 ApiClient 和**唯一 URL**做独立冷读：**6.060 秒**返回 108 源，完整响应 JSON 为 **286,539 字节**，高于原列表上限而低于独立目录上限。源 ID 集合与真实 MCP 六页完全一致，`refresh_error=null`。

保留实际元数据的 `freshness=STALE`；本次证明目录可完整访问，不把它改写为全量业务数据实时新鲜，也不替未来所有负载签署延迟保证。没有启动/安排 Dot 巡检，验收使用相同数据工具的真实调用。

## 证据

[`artifacts/audits/catalog_budget_20261126`](../artifacts/audits/catalog_budget_20261126/) 包含初始错误、33 项测试结果、实际 MCP 六页统计/源身份、独立冷 API 读取、镜像/源哈希发布回执及上线前后容器身份核对。

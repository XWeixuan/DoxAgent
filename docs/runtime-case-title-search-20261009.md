# Runtime 最近处理记录标题搜索（2026-10-09）

## 实现

- 最近处理记录工具栏按搜索图标、结果、来源、导出的顺序显示；导出选择模式下确认导出也在最右。
- 无查询词时搜索框默认收起，点击搜索图标展开/收起。输入后按 Enter 或点击搜索按钮提交，清除只清除标题查询，保留其他筛选。
- `GET /tickers/{ticker}/runtime/cases?q=...` 支持标题字面子串查询，去首尾空白、最多 200 字符、Unicode casefold 大小写无关；不搜索正文。
- 从 CaseSummary 的 `title.value` 搜索，兼容没有 search_text 的历史 Case，无迁移、无回填。先按周期/结果/来源/标题过滤再分页；查询词纳入 cursor scope，无搜索时维持原 cursor scope。
- 分钟读取与续页携带查询词；搜索变更退出旧导出选择。来源名称在当前 ticker 会话内保留，避免空列表使选择框错误显示“全部消息源”。图、节点详情和 KPI 不受搜索影响。

## 必要验证

- `tests/v2_backend/test_trade_outcomes.py`：9 passed。新增真实 API 回归覆盖筛选组合、跨页、冻结水位、查询词与 cursor 隔离、Unicode/大小写、百分号和下划线字面匹配、不搜正文、空白和长度校验。
- 前端 Vitest：57 passed；运行页 ESLint、TypeScript、生产构建通过。
- Chrome 生产真实 BE 记录：HYDROGEN 搜索返回两条标题匹配记录，来源/事件发现组合过滤有效；清除恢复原范围；无匹配时提示空记录并禁用导出；搜索变更清除旧选择。导出/确认导出均在工具栏最右。
- 1262px、1559px 桌面检查无页面横向溢出，控件无重叠；截图保存于 `eval/runtime_title_search_20261009/runtime-search-1262.jpg`、`runtime-search-1559.jpg`。
- 生产库只读探测 BE/MU 全历史匹配与无匹配搜索，单次 15.12–451.43ms（六个样本，非负载测试）。

## 发布与回滚

- 发布目录：`/home/ubuntu/doxagent-runtime-search-20261009`。
- API 基于原运行镜像 `doxagent-v2:store-recovery-20261002`，仅覆盖 `api_v2/runtime.py` 和 `v2_read/repository.py`；与生产原文件相比仅有本次搜索改动。
- Web 基于 `doxagent-v2-web:gateway-viewfix2-20261003`，覆盖本次生产构建的 dist，保留原 nginx 配置。
- 新镜像：`doxagent-v2:runtime-title-search-20261009`、`doxagent-v2-web:runtime-title-search-20261009`。API/Web healthy；前后容器 ID 对照确认只更新这两个服务，其他业务容器未重建。
- 本地与部署的两份 Python 文件和前端 index.html SHA256 一致。
- 当前生产 Compose 使用原 base + server + store-recovery override + 本次 override。Git 仅提交本次修改，保留工作区其他在途改动。

回滚时使用原 override，省略本次 override，仅重建 API/Web：

```sh
sudo -n env DOXAGENT_V2_ENV_FILE=/home/ubuntu/doxagent/.env.v2 docker compose \
  --project-name doxagent-v2 --env-file /home/ubuntu/doxagent/.env.v2 \
  -f /home/ubuntu/doxagent-case-release-d11f7fa1/docker-compose.v2-production.yml \
  -f /home/ubuntu/doxagent-case-release-d11f7fa1/deploy/docker-compose.server.yml \
  -f /home/ubuntu/doxagent-store-recovery-20261002/compose.override.yml \
  up -d --no-deps --no-build v2-api v2-web
```

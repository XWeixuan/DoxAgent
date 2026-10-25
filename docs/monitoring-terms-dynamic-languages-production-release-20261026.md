# 统一监测词动态语言生产发布验收

日期：2026-10-26。功能代码提交：`fd8886646c729d30ba654c01795bef41b1517a86`，已推送 origin/main 并同步新加坡仓库。

## 发布范围

从精确提交构建 backend/web，使用标准 storage_release 的构建、发布及回收函数和同一发布锁，仅更新 `v2-api`、`v2-web`，保留现有 Compose 参数与环境配置。API/Web 镜像标签、实际镜像和生产发布台账均核对通过。资源回收 timer 已恢复。

- API：`sha256:5374ae67eaa0a4881613fdbd5a10935ea9248489f52ac8d4d51967a86c35358e`
- Web：`sha256:c325a67893d3979f2b7ec4d0c36c086e47120ba0220642b90673f6d601a326b1`
- 两组件健康状态均为 `healthy`，重启次数均为 0。
- 发布前后对比，其他组件的 current 发布记录没有变化。

不变更生产 ticker 绑定或词表，不增加轮询任务、常驻进程或数据表。

## 功能验收

在生产 API 容器内，用只读事务调用正式 MonitoringTermsControl，并用独立 SQL 从源与绑定表重新计算语言及 source_ids；9 个 ticker 的返回结果与实际启用绑定完全一致。

| Ticker | 语言 | 已保存词表版本 |
| --- | --- | --- |
| BE | en、ko、zh-Hant | 2 |
| CEG | en | 未配置 |
| COHR | en、ko、zh-Hant | 1 |
| INTC | en、ko、zh-Hant | 2 |
| MU | en、ko、zh-Hant | 5 |
| NBIS | en、ko、zh-Hant | 1 |
| ONDS | en、he | 2 |
| RKLB | en | 1 |
| SNOW | en | 未配置 |

生产 Web 首页、healthz、反向代理 auth/config 均返回 200；监测词受保护接口未认证访问返回 401。实际 HTTP 返回的 `message-bus-Bq96MoM8.js` 已包含动态语言界面与回到页面重读逻辑，SHA256 为 `30efeedcee0498e0f643e12c23d273c50dbd6b89f3a92f03004fd6c4c6b7724d`。

发布前已通过 48 项后端回归、1 项前端单测、5 项浏览器测试（1262/1559px，包括语言增减、希伯来文编辑、草稿保留及临时真实 Bus 保存读回）、前端构建/类型/lint。提交后又通过 9 项监测词核心/控制层回归。

生产验收使用只读数据与 HTTP/静态资源检查，没有修改线上绑定或进行登录后的浏览器保存操作；绑定变化交互与保存链路由上述浏览器及临时真实后端测试覆盖。

主机验收回执：`/var/lib/doxagent/monitoring-terms-languages-20261026/verification.json`。包含生产环境变量的发布前快照和 manifest 留在主机私有目录，没有导出或提交。

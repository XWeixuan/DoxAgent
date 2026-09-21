# Site Access 浏览器访问体系远端验收记录

日期：2026-09-22  
环境：`doxagent-sg` / `/home/ubuntu/doxagent`  
结论：**开发、迁移、安全边界与首轮生产技术验收完成；需订阅的站点仍等待用户人工登录，24 小时业务效果不在本次瞬时验收中预先宣布通过**。

## 1. 发布与回滚点

- 生产 Git HEAD：`99452e5de266a5a31fed8699ed8323984b808741`。主实现提交为 `802acc2c`，远端还暴露并修复了数值 UID 迁移、荷兰节点失活、rollout `combination_id` 契约三个问题。
- 后端镜像：`doxagent-v2:server` = `sha256:37c17739667b4cbc13259bc61609d84e510e98a7cab83be47444ad63130e2cc0`。
- Site Access 镜像：`doxagent-site-access:server` = `sha256:2304c020a8cecbdbb24ebf95ee6cf2039a3b8725cd79c0dae10c3941e0ab0793`。
- 冷备份：`/home/ubuntu/doxagent-backups/site-access-20260921T184045Z`；`site-data.tar.gz` SHA-256 为 `34b05810a48f167824def96b9019c9daa1eb1e070b10b0a08cda841aba2f707e`。备份包含升级前完整 Site Access 卷、旧容器/镜像元数据和目标提交。
- 已为 25 个实际创建过目录的 Profile 生成 CfT 153 基线快照，共 78 MiB；快照与 Profile 同级受限，未写入仓库或桌面目录。

## 2. 运行环境与安全边界

- 实际版本：Playwright `1.63.0`，Chrome for Testing `153.0.8010.12` / revision `1243`。
- Site Access、Xvfb、x11vnc、Chrome 均以 UID/GID `10001` 运行。renderer 实测 `NoNewPrivs=1`、`Seccomp=2`、AppArmor `doxagent-site-access (enforce)`，使用独立 user namespace；主进程与 renderer 均无 `--no-sandbox`。
- VNC 宿主机仅监听 `127.0.0.1:5900`；Chrome 内部调试端口未映射到宿主机。
- 真实 Profile 探针确认原生页面网络可用；通用策略对 `127.0.0.1` 和 `169.254.169.254` 均在发送前返回受控失败，未读取内网内容。
- 美国代理和直连对 HTTPS 站点返回站点原证书并通过校验，未观察到 Clash TLS MITM/HTTP 重建。
- 优雅重启完整出现 Uvicorn application shutdown/finished/startup；Barron's `Cookies` 文件 SHA-256 重启前后一致，重启后 Profile 可再次打开 200。

## 3. 固定出口

| Egress | 实际节点 | 最终状态 | 观测公网 IP |
| --- | --- | --- | --- |
| `jp-standard-6` | 日本标准 IEPL 专线 6 | READY | `212.107.30.197` |
| `us-standard-5` | 美国标准 IEPL 专线 5 | READY | `38.181.82.188` |
| `nl-standard-2` | 稳定 ID/18082 槽位，底层已替换为荷兰专线 1 | READY | `104.28.251.139` |
| `de-standard-1` | 德国标准 IEPL 专线 1 | READY | `205.198.126.113` |
| `server-direct` | 新增直连槽位 | READY | `43.163.67.97` |

原“荷兰专线 2”被 Mihomo 标记为失活，三个独立端点均失败；替换为同订阅专线 1 后恢复。保留 egress ID 是为了不破坏已有 Profile 的不可变绑定，Registry 节点引用/指纹和 Clash manifest 已同步。

## 4. 14 项站点首轮结果

| 站点 | 直连首页/Profile probe | 既有代理组合 | 真实文章/业务路径 | 当前处置 |
| --- | --- | --- | --- | --- |
| Yahoo Finance | 429 | JP 200 | 真实文章 Browser 200，907089 bytes；MU latest-news recipe 20 条 | 保留 JP 主组合，直连禁用 |
| Reuters | 200 | NL 200 | `/world/` Browser 200；search recipe 20 条 | 启用直连低优先级 fallback，保留 NL 主组合 |
| Barron's | 200 | 未用首页 200 代替订阅验证 | 真实文章正确返回 `AUTH_REQUIRED` | 直连候选已启用，等待人工登录 |
| WSJ | challenge | US 401 challenge；JP 401 challenge | 真实文章正确返回 `AUTH_REQUIRED` | 不启用直连，等待人工 challenge/登录 |
| Seeking Alpha | 200 | 未用首页 200 代替订阅验证 | 真实文章正确返回 `AUTH_REQUIRED` | 直连低优先级 fallback，等待人工登录 |
| MarketWatch | 200 | 未用首页 200 代替订阅验证 | 验证文章正确返回 `AUTH_REQUIRED` | 直连低优先级 fallback，等待人工登录 |
| TheStreet | challenge | JP 403 challenge，无第二既有 Profile | 真实文章 `ACCESS_EXHAUSTED` | 保持单组合，不反复探测，待人工 challenge |
| Finnhub | 200 | 不需要额外候选 | API news URL 返回受控 302 `REDIRECT_REQUIRED` | 直连 fallback 已启用；不把 API 跳转冒充文章正文 |
| CNBC | 200 | 不需要额外候选 | 生产最新为视频页，HTTP 200，不冒充普通文章识别 | 直连 fallback 已启用 |
| 24/7 Wall St. | direct challenge | JP 200 | 真实文章 HTTP 200，327686 bytes | 保留 JP 主组合，直连禁用 |
| Motley Fool | 200 | JP 200 | 真实文章直连 429 后自动 fallback JP 200，379665 bytes | 保留已验证 fallback；直连低优先级 |
| ChartMill | direct 403 | JP 200 | 真实文章 HTTP 200，125468 bytes | 保留 JP 主组合，直连禁用 |
| Benzinga | direct challenge | JP 200 | 真实文章 HTTP 200，815134 bytes | 保留 JP 主组合，直连禁用 |
| generic | 不创建全局共享直连 Profile | 未注册域 `example.com` 200 | 通用内容 559 bytes；loopback/metadata 地址均安全拒绝 | 通用策略和安全边界通过 |

除 Yahoo Finance 与 Reuters 外，本轮 Registry 没有配置站点发现 crawler；记录为“未配置/不适用”，没有为正文站点虚构新消息爬虫。

## 5. 服务、桌面与数据库

- `v2-site-access` 使用新 Site Access 镜像并为 healthy；`v2-message-bus` 与 `v2-content-enrichment` 已强制重建到新统一后端镜像，两者 restart count 为 0。
- Message Bus SQLite `quick_check=ok`，826 条 observation、902 条 admission result；验收时正文队列为 0，无丢弃待处理 job 的迹象。
- xRDP 工具已重装更新；管理脚本 root:root `0755`，sudoers root:root `0440` 并通过 `visudo -cf`，桌面用户无密码可执行受限 `list`，不能获得 Docker 或管理 token。
- 桌面库存实测返回正确 Profile、egress、`combination_id`、认证与运维状态；VNC ready，当前无未完成维护会话。

## 6. 未被技术验收伪造为已完成的项目

1. Barron's、WSJ、Seeking Alpha、MarketWatch 的订阅权限需用户在 xRDP “Site Login Maintenance”中人工登录/处理 MFA 后验证。Reuters 为 optional/public-access 维护入口。
2. WSJ 的 US/JP Profile 都在公开首页命中 challenge；TheStreet 当前 JP 组合也命中 challenge。本轮已停止该站继续轮换探测，留给正常人工首次挑战。
3. 相隔 30 分钟的固定出口/低风险路径复测与 24 小时正文、challenge、queue wait、crawler freshness 观察为时序验收，必须在实际时间窗口结束后追加，不使用瞬时结果代替。


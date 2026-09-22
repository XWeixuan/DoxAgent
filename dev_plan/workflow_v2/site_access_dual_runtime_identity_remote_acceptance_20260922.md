# 双轨 Browser Runtime 与 Browser Identity 远端验收记录

日期：2026-09-22

环境：`doxagent-sg` / `/home/ubuntu/doxagent`

部署配置提交：`7a2d3341`
应用实现提交：`48f398fd` 至 `5d106e49`

## 1. 结论

双轨 Browser Runtime 与 Browser Identity 已部署到远端生产环境。Barron's 已完成真实登录态迁移、订阅文章权限验证、自动正文抓取、Supervisor 容器重建续存及 Site Access 单独重启后的原进程 CDP 重连。Reuters 保持 Managed Playwright，真实搜索 recipe 成功；Yahoo 已切换 External Chrome，真实新消息 recipe 成功。

WSJ、MarketWatch、Seeking Alpha 仍需操作员分别完成 challenge/login/订阅权限验证。三站不得因为共享 Chrome 进程或 Barron's 已验证而被标记为已认证。备用 Identity 保持 `STOPPED`/按需启动，不冒充主 Identity 的登录状态。

## 2. 部署与迁移证据

- 基础镜像和 Site Access/Supervisor 镜像已重建；镜像内正式 Chrome 为 `153.0.8010.52`。
- Registry 在线一致性备份：`/site-data/backups/pre-dual-runtime-20260922.sqlite3`，SHA-256 `7c377...`（现场记录的缩写）。
- 停服迁移备份：`/site-data/backups/pre-dual-runtime-live-20260922.sqlite3`，SHA-256 `467cbc6d086ae55983eebddd4152271634153926b61523490d030627bebaf393`。
- 成功 CDP 实验 Profile 的冷备：`/site-data/backups/barrons-cdp-profile-pre-dual-runtime-20260922.tar`，SHA-256 `0565b69114bbcb46939f0a81aa593f16a364c92daa469f0629322d111a840f42`。
- 冷迁移目标：`/site-data/profiles/dowjones-main`；共 1820 个文件、约 200 MiB，owner `10001:10001`。迁移前已停止实验 Chrome，未热拷 Cookies SQLite。
- 首批 Registry 迁移已实际应用并再次 dry-run；第二次结果全部为 `keep`，证明迁移幂等且未覆盖人工认证状态。
- Site revision：Barron's `5 -> 6`、WSJ `4 -> 5`、MarketWatch `5 -> 6`、Seeking Alpha `5 -> 6`、Yahoo Finance `3 -> 4`。

## 3. 当前 Identity 拓扑

| Site | 主 Identity | Runtime | 主出口 | 当前验收状态 |
| --- | --- | --- | --- | --- |
| Barron's | `dowjones-main` | External Chrome | `us-standard-5` / `38.181.82.188` | `VALID`，订阅正文成功 |
| WSJ | `dowjones-main` | External Chrome | 同上 | `UNKNOWN`，待人工逐站验证 |
| MarketWatch | `dowjones-main` | External Chrome | 同上 | `UNKNOWN`，待人工逐站验证 |
| Seeking Alpha | `seeking-alpha-main` | External Chrome | `us-standard-5` | `UNKNOWN`，待人工登录/验证 |
| Yahoo Finance | `yahoo-main` | External Chrome | `server-direct` | 新消息 recipe 成功；登录态未宣称有效 |
| Reuters | `reuters-1` | Managed Playwright | `nl-standard-2` / `104.28.251.139` | 搜索 recipe 成功，继续 Managed |

`dowjones-backup`、`seeking-alpha-backup`、`yahoo-backup` 保持按需备用；没有从主 Identity 复制 Cookie 或认证结果。

## 4. Runtime 与安全边界验收

- 三个常驻 External Chrome 均以 UID `10001` 运行，主进程没有 `--no-sandbox`。
- Chrome 启动参数保留原生网络行为，仅包含固定 Profile、代理、窗口、CDP loopback 和首次启动抑制参数；未加入 stealth、UA override 或资源 route 拦截。
- CDP `9222/9223/9224` 只绑定共享容器 network namespace 的 `127.0.0.1`，未发布到宿主机。
- VNC 仅发布 `127.0.0.1:5900`；Supervisor Unix socket 为 `0660 10001:10001`。
- 其他生产 worker 通过 Supervisor 持有的 `v2-site-access` DNS alias 访问 `8011`，真实 ready 请求返回 `200`。
- Supervisor 持有稳定 network namespace；Site Access 是可重启控制端。单独重启 Site Access 前后三个 Chrome PID 均保持 `2400146/2400322/2400469`，CDP 仍返回 Chrome `153.0.8010.52`。
- Supervisor 在取得 OS writer lock 后才清理 `SingletonLock`、`SingletonSocket`、`SingletonCookie` 三个已知符号链接；同名普通文件拒绝启动，不删除未知文件。

## 5. 真实业务验收

### Barron's 正文

验证文章：`https://www.barrons.com/articles/annaly-capital-sports-mortgage-reit-dividend-stock-price-3972cdfb`

1. 桌面维护流程打开 `dowjones-main`，约 10.11 秒进入登录页面。
2. 自动订阅权限验证返回 `VALID`、`reason=ok`，并正常关闭维护 lease。
3. External 请求返回 `SUCCESS / HTTP 200`，`combination_id=barrons-1`、`identity_id=dowjones-main`、`runtime_kind=external_chrome`，正文响应约 0.96-0.97 MiB。
4. Supervisor/Chrome 容器重建后认证状态仍为 `VALID`，证明完整持久化 Profile 可恢复。
5. 单独重启 Site Access 后，Chrome PID 与 `runtime_instance_id=0410aa1e9511429bb84c9a1e81a1b376` 均未变化；再次抓取返回 `SUCCESS / HTTP 200`，body length `974579`。
6. 不写生产消息库的真实 `SharedContentExtractor body_v2.2` 验收返回 `succeeded=true`、`outcome=FULL`、`extraction_method=browser_article_dom`、正文长度 `7554`，策略为 `builtin:barrons@1`。因此本轮不仅验证了页面 HTML，还验证了正文识别管线。

### 新消息抓取

- Reuters `builtin:reuters_search@1`：`SUCCESS / HTTP 200`，返回 20 条；provenance 为 `reuters-1 / managed_playwright`。
- Yahoo `builtin:yahoo_latest_news@1`：`SUCCESS / HTTP 200`，按验收参数返回 10 条；provenance 为 `yahoo_finance-1 / yahoo-main / external_chrome`。
- 本轮没有为 Barron's、WSJ、MarketWatch、Seeking Alpha 临时编造新消息 crawler；只验收其已注册能力。

固定条数仅证明 recipe/runtime 合同可执行，不代表窗口覆盖率或新鲜度结论。覆盖率仍应由后续自然运行窗口中的发布、入库和 provider first-seen 数据判断。

## 6. 自动测试

最终相关回归分组执行，避免 Windows 命令会话 30 秒上限中断长套件：

- Site Strategy、生命周期、网络策略、双轨 Runtime、Crawler contracts：`48 passed, 1 skipped`。
- Content Enrichment 浏览器/原生/Hub/Pipeline/Publisher：`101 passed`。
- Message Bus 新闻源：`12 passed`。
- Message Bus 去重：`20 passed`。
- 合计：`182 passed, 1 skipped`；唯一 skip 是当前 Windows 账户无创建符号链接权限，非链接同名文件拒绝测试实际通过。
- Ruff、mypy、Compose config 均通过。

## 7. 资源与短时运行记录

部署后快照：

- Site Access：CPU `0.14%`，内存 `335.5 MiB / 6 GiB`，28 PIDs。
- Chrome Supervisor（含三个正式 Chrome）：CPU `1.64%`，内存 `822.3 MiB / 5 GiB`，370 PIDs。
- 三个 Chrome 主进程 RSS 观测值约 `328/309/337 MiB`；该值包含共享页，资源判断以容器工作集为主。
- 两个服务均为 `healthy`；最终 Site Access 日志没有 External prewarm 失败。Xvfb 有非 root `/tmp/.X11-unix` owner 提示，但三个 Display、Chrome、CDP 和业务请求均正常，不构成当前失败。

30 分钟重复窗口已完成：Barron's 保持 `VALID`，正文页面再次为 External `200` 且 instance/generation 未变化；Yahoo External recipe 再次返回 10 条；Reuters 首次复测因 NL 出口被一次瞬时 IP 探测误记为 `UNAVAILABLE` 而失败，但该代理同时进行的真实 IP/Reuters 请求分别返回既有 IP 和 `200`，显式重探后恢复 `READY`，Reuters Managed recipe 再次返回 20 条。针对该现场证据，探测治理改为已验证 READY 节点连续两次全探测失败才撤出，首次失败保留最后成功观测并继续让真实请求/fallback 判定。30 分钟资源快照为 Site Access `356.2 MiB / 0.12% CPU`、Supervisor `820.3 MiB / 0.78% CPU`。

24 小时自然运行不能在本次同步部署回合内伪造完成。生产事件/统计接口已保留 identity/runtime provenance，后续窗口应按站点记录正文成功率、challenge/auth/内容失败、crawler freshness/DEFERRED、启动/重连、资源和维护中断；未取得窗口数据前不宣称长期成功率提升。

## 8. 回滚边界与命令

正常单站回滚必须先 drain 目标 Identity，再把 Site revision 回到迁移前版本；不得恢复整个 Registry 以覆盖其他站点新登录状态。以下命令在当前 revision 未再变化时适用：

```bash
cd /home/ubuntu/doxagent
sudo docker exec -i doxagent-v2-v2-site-access-1 /app/.venv/bin/python - <<'PY'
import os
from pathlib import Path
import httpx

token = Path(os.environ["DOXAGENT_SITE_ACCESS_ADMIN_TOKEN_FILE"]).read_text().strip()
client = httpx.Client(
    base_url="http://127.0.0.1:8011",
    headers={"Authorization": f"Bearer {token}"},
    timeout=120,
)
identity_id = "dowjones-main"
response = client.post(f"/v1/identities/{identity_id}:drain")
response.raise_for_status()
for site_id, target, expected in (
    ("barrons", 5, 6),
    ("wsj", 4, 5),
    ("marketwatch", 5, 6),
):
    response = client.post(
        f"/v1/sites/{site_id}:rollback",
        json={"target_revision": target, "expected_revision": expected, "actor": "rollback"},
    )
    response.raise_for_status()
current = client.get(f"/v1/identities/{identity_id}").json()["spec"]
response = client.post(
    "/v1/identities:apply",
    json={
        "spec": current,
        "expected_revision": current["revision"],
        "actor": "rollback",
        "enabled": False,
    },
)
response.raise_for_status()
print("dowjones rollback complete")
PY
```

Seeking Alpha/Yahoo 的对应目标/current revision 为 `5/6`、`3/4`。执行前必须重新读取当前 revision；若已变化，不得照抄 expected revision。命令结束后清除 shell 中的 `ADMIN_TOKEN`。

完整 Registry 备份只用于停服灾备。恢复前必须停止 Site Access 与 Supervisor、另存当前数据库，并明确接受会丢失迁移后的全站认证/健康/运行状态；禁止在线覆盖 SQLite。Profile 回滚使用迁移前完整 tar，且只能由已停机、匹配 Chrome 主版本的唯一 writer 恢复。

## 9. 待人工完成

通过 xRDP 桌面“Site Login Maintenance”依次维护并分别验证：

1. WSJ（`dowjones-main`，只写 WSJ 的 site auth）。
2. MarketWatch（`dowjones-main`，只写 MarketWatch 的 site auth）。
3. Seeking Alpha（`seeking-alpha-main`）。

Barron's 已完成，无需重复登录。Yahoo 当前 crawler 可用；如未来需要登录可见能力，再单独进行站点认证验收。一次只维护一个 Identity，维护期间业务 lease 会被 drain，GUI/桥接层不会暴露管理令牌或网站密码。

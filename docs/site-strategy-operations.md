# Site Strategy 与 Site Access 运维手册

Site Strategy 只治理网页型正文补全和网页型新消息抓取。Yahoo NCP/RSS、标准 API/RSS、IBKR/Benzinga 原生正文、数据库和模型流量继续走原链路。唯一事实源是 `v2-site-access` 持有的独立 SQLite；YAML/JSON 仅用于导入导出。

## 部署前准备

1. 在受限目录创建两个不同的随机 token 文件，权限设为仅部署账号可读：`deploy/secrets/site_access_worker_token` 与 `deploy/secrets/site_access_admin_token`。业务 worker 只能获得 worker token。
2. 备份 Mihomo 当前配置。用 `deploy/site-egress-config.py` 将 `deploy/site-egress.manifest.example.json` 合入订阅配置的 `listeners`，先写临时文件并运行 Mihomo 配置检查，再原子替换并 reload。脚本不会修改原订阅的代理组或 7893 入口。
3. 确认 `v2-site-access` 与 `doxagent-egress-clash` 在同一 Docker 网络；从 Site Access 容器分别访问 `doxagent-egress-clash:18080..18083`。
4. 先保持 `DOXAGENT_SITE_ACCESS_ENABLED=false` 构建并启动 Owner，完成 `/readyz`、Registry 和固定出口验证；再同时切换 Message Bus 与 Content Enrichment。

示例：

```bash
python deploy/site-egress-config.py \
  /opt/doxagent-egress-clash/config.yaml \
  deploy/site-egress.manifest.example.json \
  /opt/doxagent-egress-clash/config.next.yaml
docker compose -f docker-compose.v2-production.yml \
  -f deploy/docker-compose.server.yml build v2-site-access
docker compose -f docker-compose.v2-production.yml \
  -f deploy/docker-compose.server.yml up -d v2-site-access
```

## 首次 Registry 初始化

Owner 启动时只对不存在的记录幂等写入种子，不覆盖人工 revision。种子包含 generic、六个重点站点及历史专项正文站点；六个重点站点各有两个 Profile/Egress 组合。未实测的出口初始为 `UNVERIFIED`，不能把登记成功当成网站验收成功。

```bash
python -m doxagent.site_strategy.cli --url http://127.0.0.1:8011 \
  --token "$SITE_ADMIN_TOKEN" site list
python -m doxagent.site_strategy.cli --url http://127.0.0.1:8011 \
  --token "$SITE_ADMIN_TOKEN" migrate audit
python -m doxagent.site_strategy.cli --url http://127.0.0.1:8011 \
  --token "$SITE_ADMIN_TOKEN" egress probe jp-standard-6
```

对每个重点网站分别 probe 两个组合。`READY` 只表示固定监听器可用且记录了实际出口 IP；还必须用目标网站文章/列表做真实 probe。429、明确 challenge 或 block 才会切组合；404、正文解析失败、账号/订阅失败和普通瞬时网络错误不会污染出口健康。

## Strategy 维护

`site validate` 只做 schema 检查；`site apply` 还由 Owner 检查域名冲突、Profile 归属、Profile/Egress 不可变绑定和组合引用。所有更新必须携带 `expected_revision`。正文简单 XPath、等待 selector、展开行为和 access order 放在 Registry 参数中；复杂解析代码仍以版本化 builtin adapter 随代码发布。

```bash
python -m doxagent.site_strategy.cli --token "$SITE_ADMIN_TOKEN" \
  site export reuters > reuters.json
python -m doxagent.site_strategy.cli --token "$SITE_ADMIN_TOKEN" \
  site apply reuters.json --expected-revision 1 --actor operator
python -m doxagent.site_strategy.cli --token "$SITE_ADMIN_TOKEN" \
  site rollback reuters 1 2
```

域名只在 `domains` 中取得 publisher 所有权；`support_hosts` 允许已归属页面加载登录/API/CDN 依赖，不会抢占共享 CDN。跨 publisher 跳转由 Owner 停下并返回 `REDIRECT_REQUIRED`，调用者重新 resolve，因而 Yahoo 抓到 Barron’s 链接后正文归 Barron’s。

## Profile、账号与人工登录

一个 Profile 永久绑定一个逻辑站点和一个 Egress。绑定字段不可原地修改；换出口必须创建新 Profile/Combination。账号密码仅写入 Owner 的权限受限 credential 目录，API 和普通日志只返回存在性与脱敏标签。

维护登录时设置 `DOXAGENT_SITE_ACCESS_BROWSER_HEADLESS=false`；Compose 只把容器 5900 映射到宿主机 `127.0.0.1`，再经 SSH 隧道访问。执行 `profile login-open` 后在 VNC 中登录，再用 `profile verify <profile-id> --article-url <真实文章 URL>` 让 Owner 在同一 Profile/egress 中实际访问文章并判定 `VALID`、`REAUTH_REQUIRED` 或 `ENTITLEMENT_MISSING`，不能手工写入 VALID。完成后执行 `login-close` 并恢复 headless。

## 观测与故障处理

- `stats --since ...`：正文按 site/strategy/combination/outcome/reason 聚合。
- `events`：访问结果、风险失败、组合切换和策略 revision。
- `combination status <runtime_key>`：active combination、generation、cooldown 和半开状态。
- `generic-report`：找出尚未注册且正在产生流量的 publisher。

正文结果先与消息落库在 Message Bus SQLite 的 `site_strategy_result_outbox` 中可靠提交；Owner 不可用时不阻塞消息发布，每 10 秒批量重试，确认记录保留 7 天。Crawler 的每次 Owner 访问写入 `access_events`。站点预算不足返回 typed deferred，调度器把 `next_dispatch_at` 设为建议时间且不推进 checkpoint，也不转成“poll succeeded”。

## 切换与回滚

切换时短暂停止新的网页 intake，排空既有 `body_v2.1`，将 `DOXAGENT_SITE_ACCESS_ENABLED=true` 后同时重建 `v2-message-bus` 与 `v2-content-enrichment`。验证新 job 使用 `body_v2.2`，Yahoo/Reuters acquisition metadata 含 Site Access provenance。

回滚时先把开关设为 false 并重建上述两个业务服务，Owner、Registry、profiles、events、outbox 和固定 Mihomo listeners 保留。不要删除 Profile；旧 7893 和非网页流量未被修改。观察不足 24 小时只能报告工程链路通过，不能宣称正文成功率或新消息及时性目标达成。

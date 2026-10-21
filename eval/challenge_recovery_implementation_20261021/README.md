# 安全验证分流与自主恢复：发布验收入口

正式实施记录：`docs/site-access-challenge-autorecovery-implementation-20261021.md`。

- `pre-release.json` / `before-cutover.json`：发布前历史 Task、目标页、认证和业务状态。
- `final-snapshot.json`：最终 Site Access 发布后实际容器、维护 inventory、路由、证据及状态。敏感字段已递归排除；实际请求 URL 仍按私有诊断资料处理，不整体入 Git。
- `pre-final.json` / `final.json` / `acceptance-final.json`：全时间窗聚合与当前绑定，不用最近 300 条样本代替成功总量。
- `production.py`：宿主机 root 只读 Registry/Bus/Docker 采样；`state --since 2026-10-21T17:27:49Z` 用于最终版本窗口。默认 since 为初次发布，只能作早期观察，不能冒充最终 24 小时。
- `release.py`：经批准的一次 Supervisor 重建、迁移与组件发布；`update_site.py` / `update_bus.py` 是后续窄发布辅助。它们不是自动恢复器，不应被只读验收运行。

生产 root 备份及回执位于 `/var/lib/doxagent/challenge-recovery-20261021/`；镜像/发布 manifest 在 `/var/lib/doxagent/releases/compose.current.json`。读记录不等于允许修改数据库或覆盖其他会话的组件映射。

最终窗口：UTC 2026-10-21 17:27:49 至 2026-10-22 17:27:49。验收需同时观察：

1. 所有相关 ticker 的 last_success/error/延期，真实 ACCESS_RESULT 及 FULL/SHORT_FULL 正文。
2. 新 Recovery 的供应商、题型及 UNKNOWN、模式、原页存在性、恢复方式、耗时；不能把旧 TARGET_LOST 更新当新验证码。
3. 持续 AUTO 保持后台重试、不变成人工任务；HUMAN 也允许自然业务恢复；未证明业务可用不解除门禁。
4. 页面/截图/事件数量、内存/CPU/磁盘的增长趋势，清理不触及 Profile/Cookie。
5. Reuters、InvestorsHub 的未恢复事实单列，缺少自然正文/验证码样本的分支标为未观察，不作成功推断。

禁止验收脚本额外访问网站、操作验证码、填写凭据、修复、重启、改出口或删除数据。短窗已有成功不代表 24 小时稳定。

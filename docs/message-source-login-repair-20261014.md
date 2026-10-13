# 代理恢复后消息源与登录维护：实施回执及剩余边界

日期：2026-10-14，北京时间。主要新取证 UTC 10/13 18:38–19:12（北京时间02:38–03:12）。后续证据以 JSON 自带时间为准。

## 结论

已修复并上线确定性的维护入口、延期状态和批准采集后备；并非所有网站已恢复授权。原先全代理故障已经恢复，本轮没有据旧事故默认换出口或重建Chrome。

更新需求/根因方案：`dev_plan/workflow_v2/message_sources_and_login_maintenance_root_cause_and_repair_plan_20261013.md`。其10/13原始事实保留为历史，本次顶部修订优先。

## 实际修改

| 范围 | 做法及边界 |
| --- | --- |
| 维护入口 | `site_strategy/runtime.py` 创建维护Page/gate/会话后立即返回，现有owner raw-CDP连接后台导航；网络失败保留窗口、令牌和独占。取消/关闭只清理所属任务/页面，不控制其他Chrome。 |
| 创建预算 | `browser_runtime.py` 将Identity锁及adapter分配纳入原总deadline；创建失败清理已分配lease。API结构化报告维护失败stage；ERR_*分类不泄露账号URL。 |
| 桌面工具 | root桥接保留token隔离；UI显示真实egress健康/导航状态；切换、验证、取消只关闭自己的Viewer，再连接新维护display。继续ASCII英文，避免中文字体问题。 |
| 延期状态 | 两条scheduler分支以既有last_error_code/message、next_dispatch_at/updated_at表达延期。保留历史status/last_attempt/success/checkpoint，不新增持久化PollState字段。API current_error/health与前端Badge区分上次结果与当前延期。 |
| HTTP后备 | 新增小型批准route目录，primary+至多一个fallback，只传输错误触发，保持总预算；403/429不轮换普通feed出口。Hugging/Yahoo配置DE1+TR1，各条线路均真实验证。HTTP会话不继承浏览器Cookie。 |
| Yahoo | 对确定404/405/410 endpoint设600秒circuit；浏览器失效recipe与独立NCP/RSS分离，RSS仍可承载。没有宣称已找到替代NCP/browser接口。 |
| Globe RSS | 仅该source显式feed_browser_fallback；403或HTTP断连时用现有owner浏览器读同一官方feed，严格拆包装/验证XML，非XML拒绝；HTTP和browser共用30秒，不叠两轮HTTP预算。 |
| 订阅目录 | root-only同步器+单个小时systemd timer；两provider命名空间、hash/endpoint fingerprint、可取得的usage/expiry和stale状态。仅候选目录，不自动改活跃绑定/国家/Chrome进程。同步失败保留上一份可解析快照。 |

没有修改60秒轮询、30分钟实时准入、原闭市sweep、Taiwan免费限制、正则/Jev判定、登录账号、浏览器环境、MFA/CAPTCHA处理或交易组件。消息源维护Codex Worker仍未启用。

## 发布记录

基于逐服务实际运行镜像叠加15个修复文件，不整体发布本地脏树。按各容器自身Compose chain做`--no-deps --no-build`窄更新，保留现有磁盘/生命周期/v2.1基线。

| 服务 | 当前标签 | 实际Docker image ID前缀 |
| --- | --- | --- |
| Site Access | `doxagent-site-access:login-feed-20261014-r1` | aa398f4ebdea |
| Message Bus | `doxagent-v2:login-feed-bus-20261014-r1` | 04e08cfc73a5 |
| API | `doxagent-v2:login-feed-api-20261014-r1` | 2c3f67f28010 |
| Web | `doxagent-v2-web:login-feed-20261014-r1` | 258d234ad80e |

完整实际ID、原镜像/容器、Compose chain、15个文件hash匹配在 `eval/message_source_repair_20261014/release-final.json`。Docker build的config digest和运行中的image ID不是同一种摘要，以inspect回执为准。

Chrome Supervisor、Enrichment、Projector容器ID与发布前完全相同。Supervisor仍是9/24实例，已有Profile/Cookie未搬迁或重建。Site Access/API/Web健康；Bus没有定义Docker healthcheck，不能写成“Docker healthy”，以自然完成轮询验收。远端根盘发布后约34GiB空闲，本轮未删除业务/缓存/镜像。

root桥接和GUI更新至原安装路径。订阅地址仅存在 `/opt/doxagent-egress-clash/providers/sources.json`，目录0700、文件0600，不写入仓库/日志/本回执。快照和状态也root-only。

## 真实验收

- 六个固定listener的真实HTTPS全部200、READY。18080/18082同DE2，18081/18083同DE1，不能冒充四个独立故障域。DIRECT/TR也通过，但本轮不把高风控账号无声改国家。
- 真实桌面用户免密sudo调用bridge成功。Reuters原Identity打开1.27/1.94秒；Investing原Identity6.91秒。xRDP上Tk窗口、TigerVNC进程及窗口均真实存在；两个Identity依次打开并安全关闭，最终session=null。不是仅验证VNC端口监听。已有GUI需关闭重开才能加载新代码。
- `bus-after.json`保留真实自然轮询结果；Yahoo、Hugging、Globe RSS、TrendForce两路、DIGITIMES列表、InvestorsHub和多数原稳定源有新成功，Globe RSS三ticker失败计数归零。Barron's多ticker有新成功，排队期间出现SOURCE_DEFERRED时保留历史成功但当前错误不再隐藏。
- Globe官方RSS浏览器路径解析20条，Hugging Atom50条。Globe两篇最终publisher正文FULL，长度5362/8843；TrendForce News两篇FULL3618/3804，Press两篇FULL6082/4418。
- Barron's独立可用列表11条，两篇当前正文FULL4285/5382。诊断过程中并发额外列表canary也触发过site_queue_timeout，不隐去此项；同一个单并发Identity额外探测会竞争资源，故不提高并发来伪造稳定性。
- DIGITIMES当前列表11条，两篇付费正文均`subscription_required`；缓存auth VALID不是订阅权限证明。
- BE IR普通HTTP经DE/TR都是403。现有owner浏览器返回200/19941bytes，但严格XML解析失败，因此没有启用假fallback或把别的新闻源替代其官方IR feed。
- IBKR7496 relay正常监听，但127.0.0.1:4002无监听、Gateway/TWS无进程；四ticker新闻连接超时。从15:44 UTC后开始失败，是独立服务缺失，不是同时对所有网站的风控。

部分旧调查canary使用固定operation_id，复用可能重读历史幂等回执；本轮新 `body_canary.py` 使用随机nonce，不能把旧固定ID重试当成新的网络验证。真实自然任务保持原幂等协议。

## 回归

- 首轮跨维护、runtime、service-busy、行业源、Yahoo及10/12修复：93 passed / 1 skipped。
- 后续API/消息总线及Yahoo组合：41 passed。
- Globe传输错误后备补测后的源/桌面/Yahoo组合：56 passed。
- provider+service-busy+dual-runtime+lifecycle：31 passed / 1 skipped。
- 新文件及核心修改Ruff通过，前端TypeScript检查与生产构建通过。上述集合有交叉，不能加总称为独立221项。
- 本地C盘不足1GiB触发StorageMonitor，验收测试改用D盘唯一临时目录；未删除用户本地数据。
- 最后新增延期历史不改断言及provider失败保留/脱敏测试后，最小收尾集合10 passed；核心runtime/feed/桌面及新文件Ruff再次通过。

最终北京时间03:17快照 `bus-final.json` 中，Barron's各ticker已清除诊断竞争期间的延期错误；仍有采集错误的仅IBKR、Reuters、Investing、BE官方IR。Globe RSS、Hugging、Yahoo、TrendForce及其余已启用源此刻成功。此结论只对应快照，不外推全天持续成功。

## 仍未闭环，不能称为全部恢复

1. **Reuters**：维护工具选Reuters / `digitimes-de-1`，完成人工challenge后点击验证。不能在DIGITIMES行验证来解除Reuters门控。
2. **Investing**：选择Investing / `public-markets-nl`，完成人工challenge后验证。该Identity同时承载InvestorsHub，站点健康仍分别治理。
3. **DIGITIMES English**：选择 `digitimes-nl-1`，重新登录并验证订阅正文；若仍无权限需确认账号会员权限。备用未认证，不复制Cookie。
4. **IBKR**：需人工在远程桌面恢复IB Gateway/TWS Paper登录和4002 API监听；本轮不擅自改变交易服务或账户状态。
5. **订阅维护**：两条实际订阅从服务器刷新尚未成功：SSRDOG原链接200但非节点YAML，clash格式尝试403；Flower转换及原订阅都403。小时任务已部署并标STALE，但没有有效新候选，现有129节点不变。不能据此断言账号过期，仍需有效可拉取订阅或供应商排障；未验证跨provider且已登录的真实备用。
6. **BE IR**：当前官方feed仍不可用；未替换范围。**Yahoo**实际恢复靠RSS，失效browser/NCP适配仍待真实接口调查。
7. 自然轮询有多时点成功，但未逐源留齐10次目标周期完整序列；24–48小时和下一次闭市sweep也未完成。当前技术验收不等于长期SLA。
8. UI沿既有单个current_error展示，没有实现全原因聚合的新协议。provider候选目录也未接自动应用，必须受控验证/应用，避免活跃登录身份自动换IP。

## 回滚与接续

旧镜像均保留，按 `release-final.json.services.<service>.compose_files` 为每个服务构造原独立chain，末尾附仅该服务image override，恢复对应旧image；只up该服务`--no-deps --no-build`。禁止把四个不同chain拼成全站up或重建Supervisor。回滚前确认无人维护，正常stop/drain。

本轮两个源配置通过MessageBusV2Service审计更新：Hugging v2→v3、Globe v1→v2。需要回滚时用审计版前值调用update_source，不直接改业务DB/倒退版本。Yahoo routes由Bus overlay注入，可单独撤销。小时timer可独立停用，不删除sources或运行Clash配置。

本地脏树没有git提交/push，本次按冻结生产基线发布的窄overlay仍需正常归并，不能以后用任意本地整树build覆盖这些修复；交付目录保留基线、staging、发布脚本、证据与摘要。新订阅目录凭据不进入归并。

# DoxAgent 本地分支内容审查与清理结果（2026-11-12）

盘点基线：`main 1471e7fcf18258f06bcdb90569048adc2bb3bf24`；结束时 main 为 `164883cc2cdfe00ef5a3d3c02a99e8596e48e5d5`。main 是唯一长期主线。

本地 55 个分支（main + 52 个 codex/* + rescue + server-wip），已普通删除 48 个，迁移 0 个，剩余 7 个。剩余包括 main 与 6 个 Git 普通删除拒绝项；没有确认仍需继续开发的辅助任务分支。

44 个辅助分支的历史已在 main 祖先链中；另 10 个非祖先分支（含 1 个同 tip 别名）按实际源码、函数与测试内容复核。未发现需要补迁的有效代码。不能把未图合并直接等同于有待迁移成果。

前轮 worktree 退役结果：18 个辅助工作树已退役、13 个失效记录已清理；本轮删除前逐次确认分支不被 worktree 使用，结束仍仅有主目录 main。B 类 484 份资料已在 D:\DoxAgentArchives\worktree-retirement-20261111\ 归档；前轮 A 类迁移仍留在主目录，不重复迁移。

## 实际执行与边界

- 每个目标删除前核对分支 tip 未变化、没有工作树占用；仅执行 `git branch -d -- <分支>`。
- 44 个已合入分支正常删除；另 4 个内容已吸收的非祖先分支，Git 按其原有且同 tip 的 upstream 允许普通删除。保留了 Git 的警告原文。没有新设/更改 upstream。
- 6 个无可用已合并上游的历史分支收到 `not fully merged`，予以保留。没有 -D、update-ref、伪造合并或改上游等绕过措施。
- 本轮没有产品代码变更、提交或推送，没有远程分支删除。无需因分支引用操作重跑产品测试。
- 另一任务在盘点期间将 main 从 1471e7fcf 推进到 164883cc2（消息源稳定性改动）；本轮未执行该提交。main 前进没有撤回本次判定的成果。最终仍有 changelog、tests/test_bus_dashboard.py 两项已跟踪修改及既有本地资料，暂存区为空。
- 未持久化删除前文件哈希收据，因此不宣称所有脏文件字节完全不变；本轮执行代码仅修改本地分支引用与本审计目录。

## 非祖先分支：实际差异及结论

| 分支 | 独有提交 | 实际内容与判断 | 结果 |
|---|---:|---|---|
| `codex/agent-readonly-mcp` | 5 | W1 日索引 fact_candidate/fact_content/frozen_fact_contents/visible_library_bridges/retain_new_library_facts、published_facts、execute_message/_visible_provisional，与 main 函数 AST 相同；CapitalLedger 配额、CLI、margin_after 及回归测试也相同。18 个私有交易审计文件已在前轮收敛时保留于主目录本地资料，不重复导入。没有遗漏的产品代码。 | 建议退役，但普通删除拒绝；本轮保留 |
| `codex/archive-agent-readonly-mcp-20261111` | 5 | 与 codex/agent-readonly-mcp 完全相同的 262536097 提交，仅为前轮退役时的分支别名；价值判断相同，无额外成果。 | 建议退役，但普通删除拒绝；本轮保留 |
| `codex/barrons-body-intake` | 3 | main industry_sources.py 已具备未标确切日期的 Barron’s/MarketWatch 卡片过滤，同项未标日期测试相同；content_enrichment 的 site_budget_deferred/site_recovery_pending 重试已进入后续实现，且有更新的等待预算处理。旧测试形态不需倒灌。 | 已普通删除；既有 upstream 同 tip |
| `codex/barrons-challenge-maintenance` | 5 | main 已包含挑战时保留原文章页、精准 profile 探针交接、完整文章探针认证及正文提取质量诊断；profile schema 与相关三项回归测试相同，其余实现已随 worker/runtime 恢复逻辑演进。 | 已普通删除；既有 upstream 同 tip |
| `codex/barrons-diagnostic` | 1 | 单项 site-access 异常诊断补丁已由 main site_strategy/service.py 的 logger.exception 覆盖，保留 operation/site/purpose/mode/combination/identity/retry 上下文及存储失败重抛。 | 已普通删除；既有 upstream 同 tip |
| `codex/luna-maintenance-recovery` | 1 | 独有 scripts/luna_recovery_parallel_20260930.py 为固定 BE/INTC/MU/RKLB、2026-09-11 至 2026-09-28 的一次性历史恢复脚本；配套测试只验证该脚本。历史恢复已完成，当前 owner/自动维护恢复实现已更新，重新引入旧日期手工补跑入口没有价值。 | 建议退役，但普通删除拒绝；本轮保留 |
| `codex/yahoo-driver-prod` | 2 | main driver_ready/driver_exit_code 与分支相同，死 Playwright driver 及页面回收回归测试相同；managed browser 页面使用次数回收已演进为可配置生命周期管理；Yahoo recipe 已只返回所需 rows、不传整页 HTML。 | 建议退役，但普通删除拒绝；本轮保留 |
| `codex/yahoo-revised-headline` | 9 | main yahoo_revised_title_verified、_choose_article 及同文章摘要验证测试相同；挑战恢复、死 driver、轻量页面载荷也已覆盖。缺失的 scripts/repair_published_yahoo_body.py 是固定 INTC、旧 raw_messages/message_observations/stream_members 模型的一次性修复，不应作为当前工具迁入。旧 TR 出口 manifest/listener 已被后续实现取代。 | 已普通删除；既有 upstream 同 tip |
| `rescue/luna-88291b93` | 1 | main maintenance.py 已有 semantic cutoff/current visibility/base runtime metadata 三方取最大值，且 test_historical_maintenance_does_not_rewind_visibility_day 相同；没有未迁移修复。 | 建议退役，但普通删除拒绝；本轮保留 |
| `server-wip/20260708-010740` | 1 | 125 文件服务器脏目录快照，混合 CRLF 变化、旧 V1 dashboard/backtest/gateway/persistent_runtime/weekly updater 与 src_upload_tmp 代码副本。providers 多为格式变化；model_usage pricing 10 个函数、schema 5 个函数与 main 相同，其余仓储/服务已演进。旧 recorder 依赖已退役 ModelGateway；V1 被 main 8651002d5 显式退役，已有现代 frontend/V2/persistent_runtime_v2/native usage ledger，无需恢复旧模块。 | 建议退役，但普通删除拒绝；本轮保留 |

源码/测试函数对照见 `function-comparison.json`；其 SAME 是函数 AST 比较，DIFFERENT/MISSING 需结合上述后续实现或过时判断，不将它们自动视为可迁移成果。原始独有补丁保存在本目录 *.diff。

## 已合入的 44 个辅助分支：逐分支内容核对

这些分支的全部提交均可从 main 到达；另检查了各分支最近一次实际变更的源码/测试/文档文件与 main 内容。下表列出文件级核对数量，详细路径及双方 SHA-256 见 `merged-content-review.json`。main_evolved 表示主线继续演进，retired_from_main 表示主线已主动退役；不将旧树内容倒灌回 main。

| 分支 | 核对提交 | 同内容 / 主线演进 / 已退役 | 内容范围 | 结果 |
|---|---|---|---|
| `codex/agent-mcp-release` | `3d8bc755e` | 7 / 1 / 0 | scripts/ops/runtime_oom_writer_followup_20261110.py, src/doxagent/runtime_scheduler, src/doxagent/ticker_initialization, src/doxagent/workflows, tests/test_codex_document3_v21_maintenance.py, tests/test_mainten | 已普通删除 |
| `codex/agent-wechat-notification` | `ace2b9607` | 1 / 0 / 0 | docs/agent-mcp-wechat-notification-20261111.md | 已普通删除 |
| `codex/bilingual-readme` | `83d030a59` | 1 / 1 / 0 | README.md, README.zh-CN.md | 已普通删除 |
| `codex/bus-metrics-reading` | `75a34f70d` | 1 / 0 / 0 | docs/by-distribution-decision-expiry-20261104.md | 已普通删除 |
| `codex/cdecr-parent-occurrence-package-v2` | `be6b164e4` | 16 / 30 / 10 | frontend/dashboard/src, scripts/backfill_codex_hybrid.py, src/cdecr/bulk_epoch, src/cdecr/cli.py, src/cdecr/cross_document.py, src/cdecr/provider_resilience.py, src/cdecr/step4_evaluation.py, src/doxagent/agent | 已普通删除 |
| `codex/cohr-initialization-recovery` | `8aa231600` | 1 / 0 / 0 | docs/ticker-initialization-final-review-20261019.md | 已普通删除 |
| `codex/hedge-account-form` | `a514a90a1` | 3 / 3 / 0 | frontend/v2/src, frontend/v2/tests, tests/test_execution_configuration.py, tests/test_trade_groups_capital.py | 已普通删除 |
| `codex/http-source-coverage-20261107` | `a8c644ffb` | 1 / 0 / 0 | docs/http-source-coverage-and-latency-followup-20261107.md | 已普通删除 |
| `codex/ib-gateway-client` | `97090a29e` | 0 / 2 / 0 | src/doxagent/trade_execution, tests/test_trade_execution.py | 已普通删除 |
| `codex/intc-ionq-relevance-20261103` | `460fda187` | 2 / 3 / 0 | src/doxagent/message_bus_v2, tests/test_google_relevance_opt_in.py | 已普通删除 |
| `codex/live-margin-main-20261111` | `f21f2435b` | 5 / 0 / 0 | src/doxagent/trade_execution, tests/test_trade_account_quota.py, tests/test_trade_delivery_failure.py | 已普通删除 |
| `codex/local-main-sync-20261015` | `618fd9c5c` | 12 / 10 / 0 | docs/remote-main-convergence-execution-receipt-20261015.md, docs/w3-active-writer-repair-20261014.md, src/doxagent/codex_worker, src/doxagent/persistent_runtime_v2, src/doxagent/w3_backtest, tests/test_w3_backt | 已普通删除 |
| `codex/luna-r1-cache-20261110` | `e93e25574` | 1 / 0 / 0 | docs/luna-r1-release-20261110.md | 已普通删除 |
| `codex/luna-r2-production-20261106` | `d225ff4df` | 1 / 0 / 0 | docs/luna-r2-production-release-20261107.md | 已普通删除 |
| `codex/luna-r3-release-20261111` | `05a6de086` | 1 / 0 / 0 | docs/luna-r3-release-20261111.md | 已普通删除 |
| `codex/main-convergence-20261014` | `8a8f60f2f` | 61 / 109 / 0 | deploy/docker-compose.initialization-repair.yml, deploy/docker-compose.server.yml, deploy/docker-compose.source-maintenance.yml, deploy/refresh-egress-providers.py, deploy/site-login-admin.py, deploy/site-login | 已普通删除 |
| `codex/maintenance-recovery` | `3206f63ac` | 2 / 0 / 0 | docs/event-library-maintenance-local-degradation-plan-20261006.md, docs/event-library-maintenance-repair-acceptance-20261006.md | 已普通删除 |
| `codex/news-poll-body-optimization-20261024` | `f2f0bcd66` | 6 / 0 / 0 | docs/adaptive-trailing-backtest-20261024.md, docs/body-completion-and-poll-latency-diagnosis-20261023.md, docs/message-source-polling-and-body-completion-optimization-plan-20261023.md, docs/sector-beta-alpha-ba | 已普通删除 |
| `codex/newswire-http-listing` | `cec187895` | 1 / 0 / 0 | docs/businesswire-http-followup-and-newsfile-rss-20261110.md | 已普通删除 |
| `codex/order-retry-rules` | `c908236c1` | 0 / 0 / 0 | 文档/记录；详见证据 | 已普通删除 |
| `codex/phase3-2-real-tools` | `42c2ab2d5` | 1 / 23 / 23 | README.md, src/doxagent/adapters, src/doxagent/agents, src/doxagent/blackboard, src/doxagent/context, src/doxagent/models, src/doxagent/settings.py, src/doxagent/skills, src/doxagent/tools, src/doxagent/workflo | 已普通删除 |
| `codex/pre-w3-main-sync-20261015` | `8a8f60f2f` | 61 / 109 / 0 | deploy/docker-compose.initialization-repair.yml, deploy/docker-compose.server.yml, deploy/docker-compose.source-maintenance.yml, deploy/refresh-egress-providers.py, deploy/site-login-admin.py, deploy/site-login | 已普通删除 |
| `codex/recover-20260810-worktree` | `b9660f008` | 0 / 0 / 0 | 文档/记录；详见证据 | 已普通删除 |
| `codex/revenue-audit` | `b36e59ba9` | 3 / 0 / 0 | frontend/v2/src, frontend/v2/tests, tests/v2_backend/test_revenue_audit.py | 已普通删除 |
| `codex/revenue-audit-original` | `5ea521a6a` | 13 / 13 / 0 | frontend/v2/src, frontend/v2/tests, src/doxagent/api_v2, src/doxagent/persistent_runtime_v2, src/doxagent/revenue_audit, src/doxagent/tools, src/doxagent/trade_execution, src/doxagent/v2_read, tests/v2_backend/ | 已普通删除 |
| `codex/rss-http-race-20261107` | `4f6802e6d` | 3 / 0 / 0 | docs/rss-http-race-implementation-and-acceptance-20261107.md, scripts/ops/configure_rss_http_race.py, tests/test_rss_http_race.py | 已普通删除 |
| `codex/runtime-failure-repair-20261109` | `d8f012ef9` | 1 / 0 / 0 | docs/runtime-failure-repair-20261109.md | 已普通删除 |
| `codex/runtime-failure-repair-20261110` | `c34712ae7` | 0 / 0 / 0 | 文档/记录；详见证据 | 已普通删除 |
| `codex/runtime-oom-repair-20261110` | `e49aa3a26` | 0 / 0 / 0 | 文档/记录；详见证据 | 已普通删除 |
| `codex/sail-case-rounds-20261104` | `e101863d9` | 1 / 0 / 0 | docs/sail-case-rounds-release-20261104.md | 已普通删除 |
| `codex/storage-lifecycle-20261111` | `607c687cc` | 1 / 0 / 0 | docs/storage-lifecycle-implementation-20261111.md | 已普通删除 |
| `codex/storage-lifecycle-v4` | `35d1057b8` | 0 / 1 / 0 | src/doxagent/w3_backtest | 已普通删除 |
| `codex/trade-execution-repair` | `7befd54b3` | 9 / 26 / 0 | frontend/v2/src, frontend/v2/tests, scripts/codex_document3_pilot.py, src/doxagent/codex_runtime, src/doxagent/codex_worker, src/doxagent/data_runtime, src/doxagent/pilot, src/doxagent/workflows, tests/test_cod | 已普通删除 |
| `codex/trade-exit-upgrade` | `a44b5fa30` | 0 / 1 / 0 | tests/test_trade_groups_capital.py | 已普通删除 |
| `codex/trading-dialog-navigation-fix` | `25446d63f` | 1 / 2 / 0 | frontend/v2/src, frontend/v2/tests | 已普通删除 |
| `codex/v1-retirement-cleanup` | `8a8f60f2f` | 61 / 109 / 0 | deploy/docker-compose.initialization-repair.yml, deploy/docker-compose.server.yml, deploy/docker-compose.source-maintenance.yml, deploy/refresh-egress-providers.py, deploy/site-login-admin.py, deploy/site-login | 已普通删除 |
| `codex/w1-bridge-hotfix-20261106` | `6caaad55f` | 1 / 0 / 0 | docs/w1-bridge-repair-release-20261106.md | 已普通删除 |
| `codex/w1-daily-known-index-20261106` | `e101863d9` | 1 / 0 / 0 | docs/sail-case-rounds-release-20261104.md | 已普通删除 |
| `codex/w3-backtest-sync-20261015` | `618fd9c5c` | 12 / 10 / 0 | docs/remote-main-convergence-execution-receipt-20261015.md, docs/w3-active-writer-repair-20261014.md, src/doxagent/codex_worker, src/doxagent/persistent_runtime_v2, src/doxagent/w3_backtest, tests/test_w3_backt | 已普通删除 |
| `codex/w3-cdc-receipt-repair-20261111` | `3348f2fcd` | 0 / 0 / 0 | 文档/记录；详见证据 | 已普通删除 |
| `codex/w3-execution-optimization-20261110` | `b4f18aca4` | 1 / 0 / 0 | scripts/ops/w3_backlog_recovery_20261110.py | 已普通删除 |
| `codex/w3-latency-implementation-20261111` | `47117b71f` | 0 / 0 / 0 | 文档/记录；详见证据 | 已普通删除 |
| `codex/w3-luna-fast-release-20261111` | `a0a249d9c` | 1 / 0 / 0 | docs/w3-luna-fast-production-release-20261111.md | 已普通删除 |
| `codex/w3-nonsdk-audit-20261111` | `706066f2d` | 0 / 0 / 0 | 文档/记录；详见证据 | 已普通删除 |

## 已删除的完整名单（48）

```text
codex/agent-mcp-release
codex/agent-wechat-notification
codex/barrons-body-intake
codex/barrons-challenge-maintenance
codex/barrons-diagnostic
codex/bilingual-readme
codex/bus-metrics-reading
codex/cdecr-parent-occurrence-package-v2
codex/cohr-initialization-recovery
codex/hedge-account-form
codex/http-source-coverage-20261107
codex/ib-gateway-client
codex/intc-ionq-relevance-20261103
codex/live-margin-main-20261111
codex/local-main-sync-20261015
codex/luna-r1-cache-20261110
codex/luna-r2-production-20261106
codex/luna-r3-release-20261111
codex/main-convergence-20261014
codex/maintenance-recovery
codex/news-poll-body-optimization-20261024
codex/newswire-http-listing
codex/order-retry-rules
codex/phase3-2-real-tools
codex/pre-w3-main-sync-20261015
codex/recover-20260810-worktree
codex/revenue-audit
codex/revenue-audit-original
codex/rss-http-race-20261107
codex/runtime-failure-repair-20261109
codex/runtime-failure-repair-20261110
codex/runtime-oom-repair-20261110
codex/sail-case-rounds-20261104
codex/storage-lifecycle-20261111
codex/storage-lifecycle-v4
codex/trade-execution-repair
codex/trade-exit-upgrade
codex/trading-dialog-navigation-fix
codex/v1-retirement-cleanup
codex/w1-bridge-hotfix-20261106
codex/w1-daily-known-index-20261106
codex/w3-backtest-sync-20261015
codex/w3-cdc-receipt-repair-20261111
codex/w3-execution-optimization-20261110
codex/w3-latency-implementation-20261111
codex/w3-luna-fast-release-20261111
codex/w3-nonsdk-audit-20261111
codex/yahoo-revised-headline
```

## 保留名单（7）与未清理原因

| 分支 | 保留原因 / 实际价值 |
|---|---|
| `main` | 唯一长期主线，当前主目录使用 |
| `codex/agent-readonly-mcp` | 无需补迁；不是活跃任务分支。`git branch -d` 因 not fully merged 拒绝；遵守不强制/不绕过保护的约束而暂留。实际内容见上表。 |
| `codex/archive-agent-readonly-mcp-20261111` | 无需补迁；不是活跃任务分支。`git branch -d` 因 not fully merged 拒绝；遵守不强制/不绕过保护的约束而暂留。实际内容见上表。 |
| `codex/luna-maintenance-recovery` | 无需补迁；不是活跃任务分支。`git branch -d` 因 not fully merged 拒绝；遵守不强制/不绕过保护的约束而暂留。实际内容见上表。 |
| `codex/yahoo-driver-prod` | 无需补迁；不是活跃任务分支。`git branch -d` 因 not fully merged 拒绝；遵守不强制/不绕过保护的约束而暂留。实际内容见上表。 |
| `rescue/luna-88291b93` | 无需补迁；不是活跃任务分支。`git branch -d` 因 not fully merged 拒绝；遵守不强制/不绕过保护的约束而暂留。实际内容见上表。 |
| `server-wip/20260708-010740` | 无需补迁；不是活跃任务分支。`git branch -d` 因 not fully merged 拒绝；遵守不强制/不绕过保护的约束而暂留。实际内容见上表。 |

迁移名单：空（0）。没有通过重新迁移旧代码、伪合并或提交来人为满足删除条件。若坚持正常删除且不新增合并/提交，这 6 个引用无法在本轮约束下继续删除；它们仍建议退役，并非需要开发保护。

## 远程 GitHub 分支：只盘点，不删除

| 远程分支 | tip | 图关系（相对审查 main） |
|---|---|---|
| `codex/agent-readonly-mcp` | `bb186ea49` | 已在 main 祖先链 |
| `codex/barrons-body-intake` | `bb894e638` | 非祖先；本轮仅盘点 |
| `codex/barrons-challenge-maintenance` | `19cf3cda6` | 非祖先；本轮仅盘点 |
| `codex/barrons-diagnostic` | `cf8b3ca37` | 非祖先；本轮仅盘点 |
| `codex/cdecr-parent-occurrence-package-v2` | `be6b164e4` | 已在 main 祖先链 |
| `codex/intc-ionq-relevance-20261103` | `460fda187` | 已在 main 祖先链 |
| `codex/live-margin-main-20261111` | `1b6131dcd` | 已在 main 祖先链 |
| `codex/main-convergence-20261014` | `21b9b7bd6` | 已在 main 祖先链 |
| `codex/maintenance-recovery` | `3206f63ac` | 已在 main 祖先链 |
| `codex/storage-lifecycle-20261111` | `7236076c8` | 已在 main 祖先链 |
| `codex/trade-execution-repair` | `7befd54b3` | 已在 main 祖先链 |
| `codex/v1-retirement-cleanup` | `8a8f60f2f` | 已在 main 祖先链 |
| `codex/yahoo-driver-prod` | `fea138e80` | 非祖先；本轮仅盘点 |
| `codex/yahoo-revised-headline` | `2d2f9de04` | 非祖先；本轮仅盘点 |
| `main` | `1471e7fcf` | main 基线 |
| `server-wip/20260708-010740` | `61666aaca` | 非祖先；本轮仅盘点 |

远程共 16 个分支；最终读取结果与清理前远程快照完全相同。

`origin/codex/agent-readonly-mcp` 的 tip bb186ea49 与本地旧 tip 262536097 不同，未将它误认为同一个提交，也未据此设置上游。

## 留痕与复核资料

- `branch-summary.json`：完整 55 个初始分支、tip、merge-base、独有提交及变更路径；`inventory.json` 是早期未完成快照，不作为结论依据。
- `merged-content-review.json`：44 个已合入分支文件核对。
- `function-comparison.json`、`*.diff`：非祖先分支实际源码与测试比较。
- `readonly-local-evidence-verification.json`：只读交易分支的 18 个审计/验收资料在主目录全部存在，归一化换行后文本均完全一致；没有遗漏或重复迁入。
- `deletion-results.json`：54 次普通删除的退出码和原始输出（48 成功、6 拒绝）。
- `execution-verification.json`、`branches-after.txt`、`status-after.txt`：最终状态与并发 main 推进说明。
- `remote-live-execution-before.txt`、`remote-live-after.txt`：远程 16 个分支的前后只读快照。

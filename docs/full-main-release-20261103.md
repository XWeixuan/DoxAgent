# 2026-11-03 本地开发整合与生产发布验收

## 发布结果

产品源码版本 `1df0118d02260402ef8fc8e81d76a586696f5376` 已推送 origin/main，远端仓库快进同步，镜像从该不可变提交构建。

本次整合全部本地产品代码、测试、配置示例及开发说明，并合入 origin/main 已有的 Google 相关性和概览失败 Case KPI 开发。未提交的历史 eval、exports、artifacts 取证与导出文件保留本地；凭据不纳入产品提交。共享工作树没有剩余已跟踪代码改动。

- Haiku High：四个 R2/R3 节点启用 `openrouter_haiku_high`，OpenRouter、google-vertex/global、原生严格 JSON Schema、300秒预算、65536输出上限、1小时固定前缀缓存及版本化一致性合同。新 Case 使用新配置，历史冻结 Case 保持原配置。
- IBKR：正式发布六个已验证 Dow Jones 频道的文章身份归一、去重、原生正文/快讯处理、真实更新及旧频道锚点兼容。
- Google：保留已经应用的 INTC/IONQ配置与相关性入口；概览失败 Case KPI 同步到完整源码镜像。

## 构建与运行核验

| 组件 | 实际发布角色 |
|---|---|
| Backend | v2-message-collector、v2-content-enrichment、v2-message-bus、v2-projector、v2-api、v2-scheduler |
| Web | v2-web |

Backend镜像：`sha256:85d4738aa789f91d8a96152afb8fb33c485ea2fbf2a6fdc85cfabf7047bbd4cd`。

Web镜像：`sha256:de37851ca0cabd739d5036a3bf248db0b0f017a0b80a4fe1a64a1fa627a88429`。

北京时间20:39的初次验收：七角色均 running，restart_count=0；有健康检查的Collector/API/Web均healthy；API `/healthz` HTTP200。六个后端角色分别核对22个本次变动源码/Prompt文件，共132次SHA256比对全部一致。API与Scheduler进程有效模型配置均为上述High、1h及新版本标记。已轮换百炼主key与OpenRouter key仅按指纹核对，六角色有效值一致。

生产自然流量已产生两条High W1R2成功回执，最新样本缓存读取4784 tokens。初次快照中的其余xhigh回执来自发布前冻结Case，不将其计作新High验收。四轮有效配置已核对；此短窗口不等于四轮自然流量完整性或长期稳定性评测。

## 检查与边界

- 本轮综合回归191 passed、1 failed，耗时253秒；另4项概览接口回归通过，前端TypeScript检查通过。
- 唯一失败 `test_prepare_failure_does_not_claim_or_mutate_pending_task` 与此前留存的基线完全一致：输入准备抛错后RuntimeJournal任务为RUNNING，而测试要求PENDING/token0。对应实现本次未改，未将此项冒充通过，也未夹带无关修复。旧证据：`eval/ibkr-channel-dedup-repair-20261103/runtime-baseline.log`。
- 更新前将Scheduler的陈旧发布台账与Compose回滚项校正为实际运行镜像及环境；私有备份限制权限。原停止宽限20分钟，本次第二次stop请求使用60秒预算完成Scheduler替换；保留持久化Case/任务，不进行历史重放或删除。
- 发布资源治理因CPU pressure按既有规则推迟镜像清理；发布无报错、磁盘可用约115GiB，不影响新镜像生效。
- 不要求重启未加载本次变动代码的其他独立角色；无数据库迁移，无来源订阅/交易规则变更。

验收明细见 `eval/full_main_release_20261103/{verification,release}.json`。远端私有发布目录：`/var/lib/doxagent/releases/full-main-1df0118d0226/`。私有清单包含有效环境，只存远端受限目录，不提交仓库。

## 最终自然流量复核（北京时间20:40）

新增High四轮回执11/11成功：W1R2 4条、W1R3 2条、W2R2 3条、W2R3 2条；未混入7条旧冻结xhigh回执。最新样本缓存读取分别为W1R2 4784、W1R3 5120、W2R2 5726 tokens，W2R3样本写入2537、读取0，属新缓存创建回执，不能宣称该生产快照已证明W2R3热读取。七角色再次核验均运行、镜像一致、restart0，错误列表为空。明细eval/full_main_release_20261103/verification-final.json。

仓库最终同步包含本发布说明提交；运行镜像仍固定产品源码1df0118d，后续说明提交不包含产品代码，无需为文档重建。

# DoxAgent Worktree 第一阶段盘点（2026-11-11）

本次只读检查 Git/目录/任务状态，仅新增本盘点材料；未 prune、repair、checkout、remove，未删除分支，未修改 Codex 内部会话数据。prune 已执行 dry-run（只读）。

全部 32 条登记：19 个目录存在，13 个目录不存在。所有存在目录 Git 链接有效；没有“目录仍存在但 Git 链接失效”的项目，无 repair 候选。

比较基线为本地 origin/main = 3348f2fcd5fc3683fa9975c30003eb810933e396；未 fetch。独有提交数是 origin/main..HEAD 的图关系，非“功能一定没有合入”。缺失分支中的16个独有提交 git cherry 均为 +，仍须保留分支；未进行逐功能审查。

## A. 可清理的失效管理记录（第二阶段重验后 prune）

|准确登记路径|保留分支|相对主线独有提交数|结论|
|---|---|---:|---|
|`C:/DoxAgentMCPRelease`|`codex/agent-wechat-notification`|0|C盘可访问，物理路径不存在，prunable；分支引用存在|
|`C:/DoxBarrons`|`codex/barrons-body-intake`|3|C盘可访问，物理路径不存在，prunable；分支引用存在|
|`C:/DoxChallenge`|`codex/yahoo-revised-headline`|9|C盘可访问，物理路径不存在，prunable；分支引用存在|
|`C:/DoxDiag`|`codex/barrons-diagnostic`|1|C盘可访问，物理路径不存在，prunable；分支引用存在|
|`C:/DoxDriverProd`|`codex/yahoo-driver-prod`|2|C盘可访问，物理路径不存在，prunable；分支引用存在|
|`C:/DoxLuna`|`codex/luna-maintenance-recovery`|1|C盘可访问，物理路径不存在，prunable；分支引用存在|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-ib-client-fix`|`codex/ib-gateway-client`|0|C盘可访问，物理路径不存在，prunable；分支引用存在|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-luna-r1-20261110`|`codex/luna-r1-cache-20261110`|0|C盘可访问，物理路径不存在，prunable；分支引用存在|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-luna-r2-production-20261106`|`codex/luna-r2-production-20261106`|0|C盘可访问，物理路径不存在，prunable；分支引用存在|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-runtime-repair-20261109`|`codex/runtime-failure-repair-20261109`|0|C盘可访问，物理路径不存在，prunable；分支引用存在|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-runtime-repair-20261110`|`codex/runtime-oom-repair-20261110`|0|C盘可访问，物理路径不存在，prunable；分支引用存在|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-sail-release-20261104`|`codex/sail-case-rounds-20261104`|0|C盘可访问，物理路径不存在，prunable；分支引用存在|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-w1-bridge-hotfix-20261106`|`codex/w1-bridge-hotfix-20261106`|0|C盘可访问，物理路径不存在，prunable；分支引用存在|

这13条均已列入 `prune-dry-run.txt`，没有仍存在的目录被列入。prune 只清理登记，不删除上述分支，不会释放原目录的数据空间；原目录已经不在。不存在目录无法恢复检查其原未提交文件。

## B/C/D. 全部实际工作树

大小为逐文件逻辑大小近似值；排除共享 .git 和嵌套登记 worktree，不代表 NTFS 实际可回收空间。部分主目录旧测试路径及前端可选依赖链接扫描报错，原始错误在 inventory.json；受影响项只作估计。

|准确路径|分支 / Detached|占用 GiB|已跟踪变更|未跟踪文件|主线外提交|分类|
|---|---|---:|---:|---:|---:|---|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent`|`main`|11.144|0|3|0|C 保护本地更改/数据|
|`C:/Users/WEIXUANXIE/.codex/tmp/doxagent-relevance-20261103`|`codex/intc-ionq-relevance-20261103`|0.569|0|0|0|B 待确认候选|
|`C:/Users/WEIXUANXIE/.codex/worktrees/bus-metrics-reading/DoxAgent`|`codex/agent-mcp-release`|1.838|0|14|0|C 保护本地更改/数据|
|`C:/Users/WEIXUANXIE/.codex/worktrees/execution-queue-robustness/DoxAgent`|`Detached`|0.569|0|5|0|C 保护本地更改/数据|
|`C:/Users/WEIXUANXIE/.codex/worktrees/main-convergence/DoxAgent`|`codex/w3-backtest-sync-20261015`|0.497|0|0|0|B 待确认候选|
|`C:/Users/WEIXUANXIE/.codex/worktrees/runtime-p0-main-integration/DoxAgent`|`Detached`|0.571|0|0|0|D 近期/关联/无法确认，保护|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent/.worktrees/live-margin-main-20261111`|`codex/live-margin-main-20261111`|0.596|0|0|0|D 近期/关联/无法确认，保护|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-luna-r3-20261111`|`codex/luna-r3-release-20261111`|0.595|0|0|0|D 近期/关联/无法确认，保护|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-w1-index-20261106`|`codex/w1-daily-known-index-20261106`|0.578|11|0|0|C 保护本地更改/数据|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-w3-execution-20261110`|`codex/w3-latency-implementation-20261111`|0.646|0|2229|0|C 保护本地更改/数据|
|`C:/Users/WEIXUANXIE/Desktop/DoxAgent-w3-luna-fast-20261111`|`codex/w3-luna-fast-release-20261111`|0.591|0|0|0|D 近期/关联/无法确认，保护|
|`D:/DoxAgentDialogFix`|`codex/hedge-account-form`|1.806|0|0|0|B 待确认候选|
|`D:/DoxAgentHttpCoverage20261107`|`codex/http-source-coverage-20261107`|0.228|0|211|0|C 保护本地更改/数据|
|`D:/DoxAgentRssRace20261107`|`codex/rss-http-race-20261107`|0.402|0|0|0|B 待确认候选|
|`D:/DoxAgentTradeUpgrade`|`codex/revenue-audit`|1.806|0|0|0|D 近期/关联/无法确认，保护|
|`D:/DoxNewswireHttp`|`codex/newswire-http-listing`|0.411|0|52|0|C 保护本地更改/数据|
|`D:/DoxStorageBaseline20261111`|`Detached`|0.590|0|0|0|D 近期/关联/无法确认，保护|
|`D:/DoxStorageLifecycle20261111`|`codex/storage-lifecycle-20261111`|2.855|0|18399|0|C 保护本地更改/数据|
|`D:/DoxW3CdcRepair20261111`|`codex/w3-cdc-receipt-repair-20261111`|0.600|0|3|0|C 保护本地更改/数据|

主目录未跟踪统计含本次生成的盘点文件，最终数量会随报告生成增加；主目录无已跟踪文件更改。19个实际目录均没有相对主线的独有提交。main 只在主目录占用，没有需要 Detached 释放 main 的临时工作树。

### 逐项保留依据与本地数据

- `C:/Users/WEIXUANXIE/Desktop/DoxAgent`：当前主工作目录；main；活跃对话；保留 .env/.env.v2/.env.providers.local 及本地审计与嵌套工作树。
- `C:/Users/WEIXUANXIE/.codex/tmp/doxagent-relevance-20261103`：候选：11/03 后未见文件更新；已合入主线，无忽略文件；关联相关性任务可能仍从主目录访问，需用户确认退役。
- `C:/Users/WEIXUANXIE/.codex/worktrees/bus-metrics-reading/DoxAgent`：保护：直接关联「前端bug排查与修复」（idle，11/11 更新）；14 份未跟踪方案/审计文件。
- `C:/Users/WEIXUANXIE/.codex/worktrees/execution-queue-robustness/DoxAgent`：保护：直接关联「审计交易意图收益」（notLoaded，11/11 更新）；5 份未跟踪执行修复证据。
- `C:/Users/WEIXUANXIE/.codex/worktrees/main-convergence/DoxAgent`：候选：文件最新更新 10/15；无未提交/未跟踪文件，仅缓存；未在已查看对话列表找到相同 cwd，不能证明已废弃。
- `C:/Users/WEIXUANXIE/.codex/worktrees/runtime-p0-main-integration/DoxAgent`：保护：直接关联「提升W3/一轮研判时效」（notLoaded，11/11 更新）；虽 clean 且 Detached，但关联对话仍保留。
- `C:/Users/WEIXUANXIE/Desktop/DoxAgent/.worktrees/live-margin-main-20261111`：保护：11/11 新建；「勘察交易执行模块现状」最近已完成合入，仍按近期工作树保护。
- `C:/Users/WEIXUANXIE/Desktop/DoxAgent-luna-r3-20261111`：保护：11/11 近期发布工作树；暂未确认其后续任务已结束。
- `C:/Users/WEIXUANXIE/Desktop/DoxAgent-w1-index-20261106`：保护：11 个未提交文件，513 行增加/15 行删除，包含 daily_index.py 和测试；未执行合并或内容等价判断。
- `C:/Users/WEIXUANXIE/Desktop/DoxAgent-w3-execution-20261110`：保护：2229 份未跟踪文件；W3 延迟与非 SDK 审计；「W3编排优化与修复」近期任务的辅助工作树。
- `C:/Users/WEIXUANXIE/Desktop/DoxAgent-w3-luna-fast-20261111`：保护：11/11 近期发布工作树；没有足够证据确认已废弃。
- `D:/DoxAgentDialogFix`：候选：已合入主线、clean，无额外本地配置/数据发现；主要忽略内容为前端依赖、构建、缓存；对话 cwd 不在该目录，需确认不再使用。
- `D:/DoxAgentHttpCoverage20261107`：保护：211 份未跟踪数据（HTTP coverage 等）；消息源任务从主目录操作，不能按列表 cwd 排除使用。
- `D:/DoxAgentRssRace20261107`：候选：clean 且已合入主线；存在被忽略的 eval/pytest-race-1/2/3 测试运行数据，及缓存；需确认不再需要这些测试产物及任务。
- `D:/DoxAgentTradeUpgrade`：保护：clean 但「勘察交易执行模块现状」11/11 仍在收尾；近期交易任务辅助工作树，不能据目录日期判定废弃。
- `D:/DoxNewswireHttp`：保护：52 份未跟踪数据（48 份 BusinessWire 排查及4份测试文本），另有 .test-tmp；最新文件 11/10。
- `D:/DoxStorageBaseline20261111`：保护：11/11 存储治理对照基线，Detached；近期项目尚有自然运行验收。
- `D:/DoxStorageLifecycle20261111`：保护：18399 份未跟踪文件，多数 pytest 产物，含123份存储治理证据；「排查远端磁盘空间反复耗尽」24/72小时和七天自然验收未完成。
- `D:/DoxW3CdcRepair20261111`：保护：11/11 W3 CDC 修复；3 份未跟踪修复/验收材料；最近任务「W3编排优化与修复」。

大多数完整工作树出现的 replay.sqlite / observations.sqlite3 都是已追踪的历史验证夹具，不能仅凭 .sqlite 扩展名当成本地独有数据。除主目录三个 .env 文件外，其他树未发现这类未追踪配置；仍按完整 ignored 清单审查，不以 git status clean 作为数据可删的唯一依据。

## 第二阶段建议操作（尚未执行）

1. 重新获取 worktree 表，核实13个失效路径仍然不存在、C盘可用、对应本地分支仍在；再次运行 `git worktree prune --dry-run --verbose --expire now`。只有输出仍准确匹配 A 表时，执行 `git worktree prune --verbose --expire now`。核验13条登记消失、全部分支和实际 worktree 状态未变。
2. 下列4个目录仅为用户可选择的退役候选，不代表已确认无活跃任务。用户须确认停用对应任务及不再需要本地忽略产物后，重新核验 clean、无未跟踪/独有提交及本地配置，再逐个使用正常 remove；任何命令拒绝或状态变化立即保留，不使用 force。

```powershell
git worktree remove "C:/Users/WEIXUANXIE/.codex/worktrees/main-convergence/DoxAgent"
git worktree remove "C:/Users/WEIXUANXIE/.codex/tmp/doxagent-relevance-20261103"
git worktree remove "D:/DoxAgentDialogFix"
git worktree remove "D:/DoxAgentRssRace20261107"
```

这4个候选合计约 3.27 GiB，其中 C盘 1.07 GiB，D盘 2.21 GiB。在 .codex 下的两个目录只能通过获准的 Git worktree remove 退役，不直接用文件系统删除。

3. 其余15个存在目录本轮保留，尤其所有未提交更改/未跟踪证据/近期辅助树及直接关联对话。若未来退役，先做另行明确授权的证据迁移与校验；本次不代替用户做取舍。
4. 无 main 临时占用，不执行 Detached 切换；当前主目录保持 main。所有本地/远端分支保留。

## 任务关联与检查边界

通过 Codex list_threads 检查当前列表、活动历史前50条和归档历史前50条，并读取5个相关任务最近两轮以确认发布/验收关联。直接 cwd 匹配到 bus-metrics-reading、execution-queue-robustness、runtime-p0-main-integration；其余很多任务 cwd 是主目录，但工具会操作辅助工作树，故“无 cwd 命中”不能证明废弃。idle/notLoaded 不等于已结束。未为盘点修改会话、发消息或暂停任务。

进程命令行快照未发现匹配辅助路径的 Python/Node/Git 进程；这不能排除编辑器/容器/后台进程使用，不据此批准删除。未完整检查全部历史会话或容器挂载。

此前单独盘点过的 doxagent-rss-cache-20261104：本轮路径不存在，且已无 worktree 登记，故没有列入本次清理对象；本次没有删除它，也没有验证先前137份未跟踪证据的迁移去向。

原始文件：inventory.json、worktrees-before.txt、prune-dry-run.txt、task-associations.json。JSON包含完整未提交/未跟踪清单、忽略项目、分支包含关系、逻辑大小和扫描错误；未读取或输出凭据内容。

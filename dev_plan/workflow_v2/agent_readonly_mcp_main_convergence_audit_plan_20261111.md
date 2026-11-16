# agent-readonly-mcp → main 收敛审查与执行方案

日期：2026-11-11，北京时间。本轮仅检查和设计；没有提交、合并、推送、取消追踪、删除文件或发布生产。下列工作区状态指本方案写入之前。

## 1. 结论

**没有发现需要从本分支补入 main 的业务代码。建议做内容收敛，不直接 merge 旧分支历史。**

分支名称虽然是 agent-readonly-mcp，当前剩余差异实际为 W1 历史修复与 LIVE 账户调整的本地提交。相关产品代码和模拟测试已经通过其他提交进入 main。机械合并只会补入本地生产审计资料、重复及带具体配置的 changelog，不会补入 MCP 或 Runtime 新功能。

推荐保留旧分支为本地档案，从最新 main 建立干净的集成分支，补充必要的精确忽略规则。日常开发随后使用最新 main 基线；不以保留旧提交身份为代价把本地运营记录同步出去。

## 2. 实际 Git 状态

检查目录：`C:/Users/WEIXUANXIE/Desktop/DoxAgent`。

| 项目 | 结果 |
| --- | --- |
| 当前分支 | codex/agent-readonly-mcp |
| HEAD | 26253609779faa92faeca5a3bc8918dfe61c1e34 |
| 已刷新 origin/main | 3348f2fcd5fc3683fa9975c30003eb810933e396 |
| 分叉 | 当前分支独有5提交；main独有137提交 |
| 已追踪、未暂存修改 | 0 |
| 已暂存修改 | 0 |
| 实际未追踪项 | `.worktrees/live-margin-main-20261111/`，1个嵌套Git工作树 |
| 已追踪且匹配当前ignore规则 | 0 |

嵌套工作树自身也没有未提交修改，其HEAD为1b6131dc。它含`.git`指针，不是应提交的项目文件夹。不能通过git add把它作为嵌套仓库/gitlink放入main，也不需要删除该工作树。

`git ls-files --others --directory`额外打印了若干空目录；去掉目录折叠后，真实未追踪文件项只有上述嵌套工作树，不能把那些空目录统计成待提交实验数据。

## 3. 五个提交逐项处理

| 当前分支提交 | 内容 | main中的对应情况 | 推荐 |
| --- | --- | --- | --- |
| bc0136cd | W1跨分支Fact bridge去重与输入膨胀修复 | git cherry确认补丁等价于3974f98e；后续发布记录6caaad55已在main | 保留main，不重放旧补丁 |
| 197f8ef3 | 账户ticker上限、CLI及模拟测试 | 已合入f21f2435，并保留于1b6131dc | 不重复提交 |
| 064cee4a | 区分保证金提示与实际拒绝 | 同上，交易源码与测试逐字节一致 | 不重复提交 |
| b2e5b0d6 | LIVE配置及只读验收本地记录 | 通用产品改动已合入；生产审计文档未合入 | 运营记录留本地 |
| 26253609 | 补充main同步验收与脚本 | 当前main已包含其验证的产品代码；本地验收文件未合入 | 留本地，不再次执行配置/发布脚本 |

逐文件比较：daily_index.py、providers.py、capital.py、cli.py、portfolio.py、三个相关测试文件均与main一致。service.py不同，是main已有后续W3、Provider和R3改进；不能用本地旧文件覆盖main。

额外执行了不改工作区、索引或分支引用的`git merge-tree`模拟。唯一文本冲突为changelog；模拟结果相对main只有changelog和18个本地审计文件变化。src、tests、prompts、frontend、deploy、scripts、Dockerfile、Compose全部无净变化。模拟未完成真实合并，也未执行交易或生产操作。

## 4. 文件同步与追踪分类

### A. 必须保留在main

- 最新main的产品源码、Prompt/Skill、Schema、部署配置和测试；特别是近期W3时效、CDC投影、存储治理与R3切换修复。
- 当前分支对应的通用账户上限、保证金提示修复、W1 bridge修复：已在main，无须重复搬运。
- main已有的公开变更说明及脱敏验收记录。
- 受测试或回放契约引用的冻结输入、Gold、schema、fixture。不能把eval整个目录设为忽略，也不能按JSON/SQLite扩展名统一删除。

### B. 现在未追踪，应继续排除

| 路径 | 做法 |
| --- | --- |
| `/.worktrees/` | 增加根锚定ignore；不git add，不git rm，不删除工作树；保留Git worktree注册 |

它从未被本仓库追踪，因此不需要`git rm --cached`。

### C. 本分支已追踪、main未追踪：建议留本地

共18个文件、约250KiB：

- `docs/live-margin-upgrade-readonly-acceptance-20261111.md`。
- `eval/live-margin-upgrade-20261111/`下17个文件：before/after、api-after、broker-before/after类快照、发布/同步回执、pytest XML和五份审计/配置/发布脚本。

这些文件包含真实运营配置、策略资金、profile身份、账户回报及一次性生产操作，报告自身也明确区分“通用源码进main、生产审计留本地”。其中账户ID有脱敏，并不等于整套资料已适合进入公共源码历史。

推荐在未来干净基线上增加这两条精确ignore：

```gitignore
/eval/live-margin-upgrade-20261111/
/docs/live-margin-upgrade-readonly-acceptance-20261111.md
```

加上`/.worktrees/`共三条，不扩大成对所有eval/docs的忽略。后续若需要通用交易接入文档，另写不含具体账户、资金及当次发布动作的说明，而非直接推这份审计原文。本轮没有发现必须从这些一次性脚本抽取的新产品能力。

**只在原分支执行git rm --cached再合并仍不够**：旧提交仍包含文件，普通merge会让这些历史提交成为main祖先；`merge -s ours`也保留祖先历史。推荐以main为起点作干净集成，不改写已发布main历史。

### D. 仓库已有历史产物：另批清理，不阻塞本次收敛

顺带发现当前分支追踪约593.48MiB文件，eval有7013个文件。它们大多已经存在于main，不是本轮未提交内容。

| 类别 | 当前清单规模 | 建议边界 |
| --- | --- | --- |
| 历史`.control/`运行观测文件 | 40文件，约12.67MiB | 优先归档，再按具体运行目录取消追踪；不删除程序运行时对.control的支持 |
| 根目录`rklb_badcase_*_temp.*` | 4文件，约0.57MiB | 未检出产品代码/测试引用，可移至本地审计归档后取消追踪 |
| tar.gz/zip历史包 | 21文件，约12.45MiB | 区分一次性发布context与有意保留的研究/交付包；不批量移除 |
| 上述.control之外的数据库 | 22文件，约43.02MiB | 含W1/W2冻结测试库；不能统一取消追踪。重复runs库需先核对回放依赖 |
| CDECR大型catalog | 多份大型JSON | 属产品数据，不能仅因体积大取消追踪 |

以上分类存在历史用途差别，不能把总数当成已批准删除数量。主线执行建议只处理A/B/C；D作为独立、小范围仓库卫生变更，逐路径检查测试/文档引用后再做。取消追踪只影响后续树，不会回收已有Git历史的体积，本次不做历史重写。

## 5. 推荐执行顺序

1. **冻结输入清单**：重新fetch，记录HEAD、最新main、status和上述18文件的SHA256；若其他对话又改动，先重做增量评估，不直接git add -A。
2. **保全本地内容**：将18文件及私有changelog追加内容复制到仓库外本地档案，核对哈希；旧分支另建仅本地归档引用，不推送，也不删除`.worktrees/`及其.git指针。
3. **干净集成**：从最新origin/main建独立的`codex/agent-readonly-main-convergence-20261111`工作树。只增加三条ignore、必要的通用审查说明及简短changelog。若重新比对仍无业务净差异，不人为重放五个旧提交。
4. **审查候选提交**：确认相对main仅有上述管理性变化；私有18路径不在候选树和新增祖先历史中；工作树不是gitlink；W3/Runtime及其他main业务源码逐字节保持。
5. **同步main**：按正常非force推送流程集成；若main并行推进，先吸收新main再复查，不覆盖其他会话更新。不执行生产账户配置、历史脚本或容器重启。
6. **统一日常开发基线**：保留旧分支为本地档案后，用最新main基线替代日常开发的旧分支。若仍要求沿用`codex/agent-readonly-mcp`名称，先从该已检出分支安全切走，再重建本地同名引用并恢复本地档案；不对远端分支force push。嵌套工作树保留，不能通过清空目录重建工作区。

若坚持让原五个提交完整成为main祖先，必须接受那18文件及原私有changelog同时进入历史；这与推荐的本地运营资料边界冲突，因此不推荐此路径。功能完整收敛不要求原提交身份进入main。

## 6. 验收

- 新main保留所有已有业务代码，W1/交易修复无遗漏；service.py及W3/CDС/R3保持最新main实现。
- 新提交不含真实生产快照、账户/资金审计或凭证；不引入原本仅本地的审计提交历史。
- 原18文件及私有记录仍可从本地档案恢复，SHA256一致；工作树注册和现有本地.env/凭据保持。
- `git check-ignore`能识别三类指定路径；`git ls-files`不包含它们；三条规则以外的业务文件不被误忽略。
- 日常开发分支以最新main为基线，status除了有意的本地产物/方案文件无待处理业务差异。
- 仅忽略/文档变更不需要跑模型、重建容器或发布生产；若实施时发现实际代码差异，再按该差异运行相关离线回归。

## 7. 本轮操作边界

已执行远端引用刷新、本地Git清单、文件内容/摘要比对和只写Git对象的合并模拟；新增本方案文件。没有修改业务文件、ignore、追踪规则、分支引用或索引，没有提交/推送，也没有访问生产账户或触发发布。18文件内的常见API key/私钥特征扫描未发现命中，仅为该小清单的辅助检查，不代表对整仓历史完成凭证审计。

## 8. 后续授权执行结果（2026-11-11 22:31，北京时间）

用户随后授权执行，明确目标为拉取origin/main并同步本地main。本轮据此只做本地收敛，不增加远端提交：

- 已fetch最新origin/main，并将本地主目录切换到main；本地main从1718776be快进到3348f2fcd5fc3683fa9975c30003eb810933e396。再次通过ls-remote确认GitHub main为同一提交，ahead=0、behind=0。
- 原codex/agent-readonly-mcp仍指向262536097；额外保留仅本地归档分支codex/archive-agent-readonly-mcp-20261111，未合入其私有历史。
- 18个本地审计文件、原changelog及原方案共20文件已复制到仓库外档案并校验SHA256。切换main后恢复18文件到原路径，逐份验证内容一致且不在main索引中。
- 三条忽略规则写入本地.git/info/exclude，代替提交.gitignore，确保本地main与origin/main完全相同；不影响其他克隆或远端版本。
- .env、.env.providers.local、.env.v2哈希未变；已追踪的.env.example和.env.v2.example按最新main正常更新，不以旧模板覆盖新主线。
- 嵌套live-margin-main工作树的.git指针、HEAD及干净状态均核验未变，没有删除或注销任何工作树。
- 已追踪文件修改0，暂存修改0；唯一未追踪文件为本方案，保留本地供复核。没有推送、修改生产或重启服务；历史产物清理D未执行。

本地档案目录：`C:/Users/WEIXUANXIE/.codex/local-archives/doxagent/agent-readonly-main-sync-20261111-223021/`。其中manifest.json保留备份哈希，verification.json记录最终核验；原changelog在files/changelog中保存。没有业务代码修改，因此不对main的changelog另作本地追加，避免制造与origin/main的工作区差异。

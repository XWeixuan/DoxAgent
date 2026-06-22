# Document 2 评估记录

本文件用于追加记录 Document 2 预期单元 eval 循环。当前文件先提供模板，后续每一次可评估真实运行都应追加在本文件末尾。

记录原则：

- 每个可评估 run 都要记录，包括 partial、blocked、failed 或质量门槛失败的 run。
- baseline 和 retest 必须写在同一个 eval section 下，便于比较。
- 不要把 pending patch 写成 stable expectation_unit。
- 不要把 Document 2 focused smoke 写成完整 Blackboard 初始化通过。
- 如果本轮只停在 `GenerateExpectationDetails`、`ReviewExpectationFields` 或 `ResolveObjectionsAndDelegations`，必须在标题和结果里标注 partial scope。

## Baseline 记录模板

```markdown
## YYYY-MM-DD HH:mm - <ticker> - Document 2 baseline - <stop_after>

### Test Info
- Git state:
- Source run_id:
- Source state:
- Source Brief State JSON:
- Execution mode: clone/in-place
- Command:
- Environment:
- Execution run_id:
- Stop after:
- Brief State JSON:
- LangSmith project/run link or MCP query:
- Evaluator:

### Scope Decision
- Eval mode: detail_only/field_review/resolve/promote
- Can judge stable expectation_unit: yes/no
- Cannot claim:

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass/fail | | |
| D2-HG02 | pass/fail | | |
| D2-HG03 | pass/fail | | |
| D2-HG04 | pass/fail | | |
| D2-HG05 | pass/fail | | |
| D2-HG06 | pass/fail | | |
| D2-HG07 | pass/fail/not_applicable | | |
| D2-HG08 | pass/fail/not_applicable | | |
| D2-HG09 | pass/fail/not_applicable | | |
| D2-HG10 | pass/fail | | |
| D2-HG11 | pass/fail | | |
| D2-HG12 | pass/fail | | |
| D2-HG13 | pass/fail | | |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 1-5 | |
| D2-R02 | 1-5 | |
| D2-R03 | 1-5 | |
| D2-R04 | 1-5 | |
| D2-R05 | 1-5 | |
| D2-R06 | 1-5 | |
| D2-R07 | 1-5 | |
| D2-R08 | 1-5 | |
| D2-R09 | 1-5 | |
| D2-R10 | 1-5 | |
| D2-R11 | 1-5 | |
| D2-R12 | 1-5 | |
| D2-R13 | 1-5 | |
| D2-R14 | 1-5 | |

### Document 2 State Summary
- Pending expectation patches:
- Stable expectation_unit count:
- Open objections:
- Blocking delegations:
- Promotion blocker summary:
- Working Memory entries of interest:
- Commit Log entries of interest:

### Failure Categories
- category:
  - issue:
  - evidence:
  - severity:
  - suspected root cause:

### Optimization Hypothesis
- Hypothesis:
- Expected hard-gate movement:
- Expected rubric movement:
- Risk:
- How to test:

### Proposed Modification Plan
- Change 1:
- Change 2:
- Files likely touched:
- Tests or smoke commands:

### Baseline Commit Or Snapshot
- Commit hash:
- Dirty-tree notes:
```

## Retest 记录模板

```markdown
### Retest - YYYY-MM-DD HH:mm - <stop_after>
- Git state:
- Command:
- Environment:
- Source run_id:
- Execution run_id:
- Brief State JSON:
- LangSmith project/run link or MCP query:

#### Scope Decision
- Eval mode:
- Can judge stable expectation_unit: yes/no

#### Hard Gate Delta
| Gate | Baseline | Retest | Delta | Notes |
| --- | --- | --- | --- | --- |
| D2-HG01 | | | | |
| D2-HG02 | | | | |
| D2-HG03 | | | | |
| D2-HG04 | | | | |
| D2-HG05 | | | | |
| D2-HG06 | | | | |
| D2-HG07 | | | | |
| D2-HG08 | | | | |
| D2-HG09 | | | | |
| D2-HG10 | | | | |
| D2-HG11 | | | | |
| D2-HG12 | | | | |
| D2-HG13 | | | | |

#### Rubric Delta
| Rubric | Baseline | Retest | Delta | Notes |
| --- | ---: | ---: | ---: | --- |
| D2-R01 | | | | |
| D2-R02 | | | | |
| D2-R03 | | | | |
| D2-R04 | | | | |
| D2-R05 | | | | |
| D2-R06 | | | | |
| D2-R07 | | | | |
| D2-R08 | | | | |
| D2-R09 | | | | |
| D2-R10 | | | | |
| D2-R11 | | | | |
| D2-R12 | | | | |
| D2-R13 | | | | |
| D2-R14 | | | | |

#### Result
- Improved:
- Regressed:
- Hard gates still failing:
- Accept modification: yes/no
- Reason:
- Follow-up hypothesis:
```

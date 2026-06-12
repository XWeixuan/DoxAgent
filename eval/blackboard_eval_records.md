# Blackboard Eval Records

Append every baseline, modification, and retest cycle here. Do not replace old
records. Do not claim improvement without a baseline and retest judged under the
same `blackboard_hard_gates.yaml` and `blackboard_rubrics.yaml`.

No formal eval cycle has been recorded under this contract yet.

## Record Template

Copy this section when starting a new baseline.

### YYYY-MM-DD HH:mm - <ticker> - baseline

#### Test Info
- Git state:
- Baseline commit before modification:
- Command:
- Environment:
- run_id:
- LangSmith project/run link or MCP query:
- Brief State JSON:
- Evaluator:

#### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| HG01 | pass/fail | | |
| HG02 | pass/fail | | |
| HG03 | pass/fail | | |
| HG04 | pass/fail | | |
| HG05 | pass/fail | | |
| HG06 | pass/fail | | |
| HG07 | pass/fail | | |
| HG08 | pass/fail | | |
| HG09 | pass/fail | | |
| HG10 | pass/fail | | |
| HG11 | pass/fail | | |
| HG12 | pass/fail | | |
| HG13 | pass/fail | | |
| HG14 | pass/fail | | |

#### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| R01 | 1-5 | |
| R02 | 1-5 | |
| R03 | 1-5 | |
| R04 | 1-5 | |
| R05 | 1-5 | |
| R06 | 1-5 | |
| R07 | 1-5 | |
| R08 | 1-5 | |
| R09 | 1-5 | |
| R10 | 1-5 | |
| R11 | 1-5 | |
| R12 | 1-5 | |
| R13 | 1-5 | |
| R14 | 1-5 | |

#### Failure Categories
- category:
  - issue:
  - evidence:
  - severity:

#### Optimization Hypothesis
- Hypothesis:
- Expected metric movement:
- Risk:

#### Proposed Modification Plan
- Change 1:
- Change 2:
- Files likely touched:

#### Retest - YYYY-MM-DD HH:mm
- Git state:
- Command:
- Environment:
- run_id:
- LangSmith project/run link or MCP query:
- Brief State JSON:

##### Hard Gate Delta
| Gate | Baseline | Retest | Delta | Notes |
| --- | --- | --- | --- | --- |

##### Rubric Delta
| Rubric | Baseline | Retest | Delta | Notes |
| --- | ---: | ---: | ---: | --- |

##### Result
- Improved:
- Regressed:
- Hard gates still failing:
- Accept modification: yes/no
- Reason:
- Follow-up hypothesis:

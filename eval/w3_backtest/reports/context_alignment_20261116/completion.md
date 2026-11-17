# W3 v6回测编排与上下文升级实施回执

日期：2026-11-16。状态：开发、数据补充与离线验收完成；未部署，未发起评分模型回测。构造资料使用Terra medium子Agent真实搜索，每次只派3–4条；没有向构造者提供Gold或旧W3决定。

## 交付结果

- 新运行仅支持v6；旧v5/v4指令包会在评分调用前被拒绝，历史报告继续可读。仍仅Trade，不执行Ops/W1R3/发单。
- 生产/回测共享基础消息和W1输入、Policy/可见字段投影与v6 research scope；生产校验保持严格，回测适配层允许历史字段缺失。
- 每Case可选context.json；恢复完整上游结果而非根据旧长reason猜HIT。Case独立Fork，按实际模型分别复用母线程。
- 增加显式--service-tier default/fast，保留--model/--effort；默认按新版生产路由及速度档，固定普通Luna须显式--model gpt-6-luna --effort max --service-tier default。
- 缺研究/行情不再由宿主直接NO_TRADE；已提供坏文件、错ticker或未来完整bar仍为输入错误。最终cutoff与消息发布时间分开，日期精度不伪造成精确时分。
- 运行保存visible_input.json、覆盖清单、业务输入指纹与SDK审计。Gold留宿主并保留起始快照，不进入研究入口；比较优先按每Case业务输入指纹判定可比性。

## 当前49条测试集

48条从远端冻结Case恢复，NASA使用既有本地构造档案；不重跑W1/W2、不重新筛选Case或判断Gold。新增49份context.json，49条均通过最终投影与Research Store绑定/禁止对象读取验收。

| 已恢复内容 | Case数 |
|---|---:|
| 上游W1/W2记录 | 49 |
| 非空W1匹配事实 | 28 |
| 非空关联Policy | 11 |
| 原始alignment | 10 |
| D1/D2元数据 | 49（D1 cutoff未知如实为null） |
| 可确认与本次as_of一致的PIN/近期成交快照 | 0 |

PIN/近期成交没有补造空状态，也没有将旧72h意图冒充48h实际成交。缺这两项的Case仍可运行。部分旧规则只恢复到当时存在的字段：Synspective原Policy没有transmission，保留其标题/条件/参考基线和HIT，不编造传导说明。

原as_of与Gold保持不变；Gold SHA256为`e9ae0be2529c9f095a7076c50c2a7cebb5d580f6bb25406961bf942abe0717b7`。未改变当前消息原文，未用当前活动Policy替换历史绑定版本。

默认测试指令包为`prompt_source_v6_20261116`，来自当前本地AGENTS/Skill和更新后的backtest.md，显式采用v6离线Trade合同。原本地生产目录marker仍为v5且operations.md在其他工作中被删除，本轮没有改写这些生产资产；新包的provenance明确记录它不是已发布生产bundle。旧prompt_source和旧run保留。

## 低资料Case补充

选择原来只有5–8条来源的21个Case，分批真实检索补充，共新增164个来源条目。当前49条合计617条来源；补充Case现为12–17条。数量不是独立验证数，重点是不同合理研究问题的实质覆盖，不按转载凑数。

| Case | 原有 | 补后 | 新增 |
|---|---:|---:|---:|
| ADD-INTC-CLASSIQ | 5 | 15 | 10 |
| ADD-INTC-ORACLE | 5 | 14 | 9 |
| ADD-SNOW-EVERCORE | 5 | 15 | 10 |
| ADD-SNOW-OCI | 5 | 16 | 11 |
| ADD-INTC-NEWSTREET | 6 | 13 | 7 |
| ADD-NBIS-BETHUNE | 6 | 13 | 7 |
| MU-01-03 | 6 | 12 | 6 |
| RKLB-01-02 | 6 | 12 | 6 |
| ADD-COHR-CORNING | 7 | 15 | 8 |
| ADD-COHR-SPIL | 7 | 14 | 7 |
| ADD-INTC-HAMAS | 7 | 15 | 8 |
| ADD-NBIS-CITI | 7 | 14 | 7 |
| BE-03-05 | 7 | 15 | 8 |
| ADD-CEG-DOE | 8 | 17 | 9 |
| BE-01-01 | 8 | 17 | 9 |
| EXTRA-INTC-OPENAI | 8 | 16 | 8 |
| EXTRA-INTC-TERAFAB | 8 | 15 | 7 |
| EXTRA-RKLB-NASA | 8 | 15 | 7 |
| EXTRA-RKLB-SYNSPECTIVE | 8 | 14 | 6 |
| MU-01-01 | 8 | 15 | 7 |
| MU-03-02 | 8 | 15 | 7 |

构造记录位于construction/，包含查询、覆盖、实际来源与剔除边界。修复包括Corning/Snowflake财报数字缺失、Synspective历史合同日期与任务口径、CEG原AP日期、NBIS后续更新及OpenAI消息截止后口径解释与采集话语。没有重标时效Gold。

仍有少量正常材料边界：部分机构原始报告付费不可得、同日页面仅日期或被后续更新。已保留可确认的原始核心并排除明确后发细节，边界写宿主笔记，不放进模型资料作为拒绝交易提示。未声称全球历史网页零遗漏/零未来函数。

## 验证与边界

- 124项相关测试通过：生产v6、回测、研究权限、市场、模型选择与SDK权限/档位；其中完整/部分输入、日期精度、缺行情、同名Policy及真实phase序列化路径均有覆盖。模型传输为替身，没有评分调用。
- 新增/重构模块Ruff检查通过。
- 49条实际数据全部可投影/绑定；Policy/Reference原文旁路读取被拒绝，离线网页仍可检索；行情与研究cutoff检查无新增错误，精确发布时间未发现越过as_of的来源。
- 现有数据按新版路由为38条Luna/max、11条Sol/medium。需要和此前全量Luna对照时应固定模型与standard tier，并使用新的输入版本；本轮已补上下文，不能把未来成绩变化全部归因于提示词。
- 更宽测试探索中发现旧v4双阶段直调测试仍有READY与RUNTIME_DELAY_EXCEEDED的断言失败；已在独立目录用未修改HEAD源码复现，日志legacy_baseline_check.log。这是旧测试与现行交易时钟的已有问题，新入口不再支持该执行契约，未为其放松生产时钟。

## 可复用命令

```powershell
.venv/Scripts/python.exe -m doxagent.w3_backtest prepare-context --dataset w3_curated_20261030
.venv/Scripts/python.exe -m doxagent.w3_backtest build --dataset w3_curated_20261030
# 以下为未来评分示例，本轮没有执行
.venv/Scripts/python.exe -m doxagent.w3_backtest run --dataset w3_curated_20261030 --run-id D:/DoxAgentBacktest/runs/v6_luna_standard --model gpt-6-luna --effort max --service-tier default --concurrency 8
```

prepare-context支持--case限定，只在准备期只读远端；回放只读本地冻结资料。新运行不覆盖旧run；成功结果保留，技术失败才补跑。

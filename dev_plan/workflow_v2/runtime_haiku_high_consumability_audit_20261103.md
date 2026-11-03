# Runtime Haiku临时上线与high输出可消费性根因排查

日期：2026-11-03；所有业务时间注明时区。证据目录：`eval/haiku-case-rollout-20261103/`。

## 结论与已执行范围

1. W1R2、W1R3、W2R2、W2R3已临时上线OpenRouter `anthropic/claude-haiku-5.5` / `google-vertex/global` / `xhigh`；使用原生`json_schema + strict`、65536输出token、300秒读取等待预算，静态系统前缀沿用1小时显式缓存。R1及原有回退规则不变。
2. 代码提交`bab0739d`已推送origin/main；远端以该提交的完整已提交源码及现有依赖镜像重新构建，未把本地其他会话的未提交改动带入。发布Scheduler、API；随后按用户补充要求更新百炼主凭据文件、发布消息总线，并刷新API/Scheduler实际进程凭据。未重放历史失败Case，未改业务Prompt、业务输出Schema或路由。
3. 截至北京时间19:32:21，已完成的新xhigh回执115条全部OK：W1R2 62、W1R3 4、W2R2 25、W2R3 24。两种R3均为REPLAY_PARENT，四节点均有自然生产回执；这些是已完成turn的快照，不代表所有在途Case或后续交易均已完成。四节点均观察到静态前缀缓存命中。
4. Decision Model已用用户指定的新key调用原生产System One端点成功（HTTP200，约0.89秒）。新key指纹`99e52ce2871e`，实际消息总线、API、Scheduler均确认读取；旧主key仅作受限备份保存，凭据不进入Git或报告。其他运行角色未为这次Decision Model切换而全部重启。
5. 三次W1R2 high不可消费的直接原因全部是**最外层Markdown代码围栏**，不是推理不足、截断、字段缺失或复杂事实引用。另一方面，GLM及新对照实验还暴露了**结构约束与程序业务约束不相同**的问题；这部分不能仅靠严格JSON或提高推理强度解决。

## 三个原始失败的逐项核查

| 原20例编号 | 输入规模（Haiku原生prompt tokens） | 事实上下文 | 输出tokens（含推理） | 结果 | 直接失败原因 |
|---|---:|---|---:|---|---|
| #8 Stocks to Watch: CoreWeave, TSMC, Rocket Lab | 5848 | 4个Canonical Events、13个Facts | 740 | OLD | 外层` ```json `围栏 |
| #16 AEP to invest $4bn…Google | 5928 | 5个Provisional Events，无Canonical Facts | 2125 | OLD | 外层` ```json `围栏 |
| #19 Lennar Breaks Ground…The Foundry | 4327 | 无已知Events或Facts | 395 | NEW | 外层` ```json `围栏 |

三条均以`finish_reason=stop`完整结束。逐条仅剥去完整的最外层围栏后，均通过现有W1NoveltyResult与已加载Event/Fact归属校验；没有改字段、改判断或推断缺失身份。尤其#19没有已知事实需要比较，不能把其失败解释成“任务复杂导致无法形成Schema”。#8/#16的核心事实粒度仍可讨论，但与这次解析失败是不同问题。

证据：`three_high_failures.json`及前轮原始`high/calls/`。这种诊断剥围栏未被用于放宽生产验收，也没有把原失败回执改成成功。

## 直接根因：测试请求没有启用Schema约束接口

前轮为保持与DeepSeek基线的共同请求口径，向Haiku和GLM发送的是：

```json
{"response_format":{"type":"json_object"}}
```

完整Schema仅作为系统文本中的“Exact Output Contract”。系统文本已经明确禁止Markdown；本轮W1R2业务Prompt也未要求把输出装进代码围栏。因此没有发现“Prompt明确要求围栏”的冲突，继续重复增加“不要Markdown”不是主要解决办法。

`json_object`与`json_schema`不等价。前者不把本轮完整字段契约交给Schema约束接口；即使`provider.require_parameters=true`，也只是在请求所带参数的支持范围内选择端点，不会凭空把文本Schema变成严格约束。原始回执证明当前组合仍会输出围栏。

生产Haiku R1此前已使用原生Schema请求。本次四个新节点复用这种方式：

```json
{
  "response_format": {
    "type":"json_schema",
    "json_schema":{"name":"w1_novelty_result","strict":true,"schema":{}}
  },
  "provider":{"only":["google-vertex/global"],"allow_fallbacks":false,"require_parameters":true}
}
```

示例中的`schema`实际填入程序从业务模型编译的完整Schema，不是空对象。OpenRouter官方要求通过这个字段传递Schema；其文档同时说明执行方式取决于具体端点。我们能核验请求、端点支持和回执，不能仅凭OpenRouter响应独立审计其底层约束解码实现。

## 真实对照验证及其限制

保持原历史Case、Prompt、messages和high档位；统一使用65536输出预算与300秒等待预算，不改变交易或生产Case。只在原生组替换`response_format`。

| 组别 | 请求数 | 有效JSON/发送的Schema | 程序最终可消费 |
|---|---:|---:|---:|
| 三个原失败Case再次使用json_object | 3 | 3/3 | 3/3 |
| 原生Schema：W1R2全20例 | 20 | 20/20 | 20/20 |
| 原生Schema：W2R2原有10例 | 10 | 10/10 | 9/10 |

原失败三例复跑json_object也全部成功，说明格式失败是概率性的，而不是三例内容必然无法处理；不能把小样本复测说成严格的因果或稳定性保证。原生组W1R2未再出现围栏、额外字段或空数组类型错误。证据保留于`format_ab/`。

上述原生组没有使用修复重试。又针对唯一W2失败执行一次现有修复提示词重试，该次改为触发HIT身份对应错误，仍失败；因此不能声称“加原生Schema和一次重试就已保证30/30”。该次的错误轨迹和修复请求保留在`correction_r3/`；评估脚本在校验前未落下其完整响应，缺少该次完整输出/usage，这一证据缺口不影响已保存的首次失败分析，但不能拿它做逐token成本统计。

## 第二根因：API Schema没有表达本地跨字段不变量

原生组失败为W2R2 #16，多Policy混合HIT/RELATED。模型正确列出HIT身份和Condition，在reason中也明确指出另外两个RELATED Policy，却返回：

```json
{"policy_related":true,"related_policy_ids":[]}
```

用原请求实际发送的完整wire Schema验证，这个对象通过JSON Schema；用现行业务模型验证，则被`RELATED requires related_policy_ids`拒绝。可复核的程序结果见`wire_vs_domain_validation.json`。

原因是`model_json_schema()`只导出字段结构、类型等约束，不会把Python `@model_validator`逻辑自动翻译成JSON Schema。当前API知道`related_policy_ids`是数组，但不知道它必须与`policy_related`联动。下列业务规则仍由本地代码单独执行：

- `policy_hit`必须与HIT Policy集合和非空Condition分组一致；
- `policy_related`必须与RELATED集合是否非空一致；
- HIT和纯RELATED不能包含同一个Policy；
- W1 Fact归因的Event必须也在reference_ids中；
- 返回的Event/Fact/Policy/Condition必须属于这次加载的业务集合。

所以“Schema不复杂”在字段数量上成立，但同一判断现在被重复表达为布尔、Policy列表、Condition所属Policy、reason中的文字；模型需要额外保持这些副本自洽。重复表达本身制造了可以避免的失败点。

还存在一个较小的契约不齐：系统文本要求每个key都输出，业务Schema却为部分列表提供可省略默认值。原生组#12省略`policy_ids`/`hit_condition_ids`时，程序正常补默认空数组；这不是失败，准确性应按程序实际消费的归一化对象评估，不能把原文缺少一个可省略key误算为判断错误。

## GLM失败不是同一类问题

此前共同json_object口径下的失败目录见`glm_failure_catalog.json`，至少应区分：

1. **外壳/语法**：把答案包入`properties`，把Schema的`additionalProperties`当答案字段，额外输出`confidence_detail`等。严格输出协议主要处理这一层。
2. **结构契约**：应为`[{policy_id,condition_ids}]`，却输出扁平`["C5","C6"]`；把应为数组的reference_ids输出成null。严格Schema可减少这类错误，但需端点确实支持相应能力。
3. **身份和交叉字段**：Fact归因未出现在reference_ids、给Provisional编造F#、引用Policy没有的Condition。静态Schema不完全知道本轮业务身份；本地校验与修复仍有必要。
4. **业务判断**：把“首飞移至2027”推断成“因技术资格或地面准备导致”，或把旧项目总容量当成新增采购。这些即使输出完全可消费也可能错误。
5. **资源预算/运行**：max档的120秒超时、前轮Haiku xhigh用尽16K输出预算。它们与三条high围栏失败无关，不应合并统计为同一种格式能力问题。

因此，此前格式失败率不能直接当成GLM/Haiku的业务能力上限，更不能据此证明某provider是假模型。共同请求口径下的横比仍有效，但不是各模型充分适配后的生产上限比较。

## 提示词和业务判断的独立问题

没有证据显示W1R2存在必须输出围栏的提示词冲突。W2 R2仍提及历史`matched_condition_ids`并使用“若由当前合同保留”的兼容表述，而当前V2输出禁止该旧字段；GLM某次确实吐回这个旧字段。这是可能的干扰源，后续Prompt对齐宜移到历史读取文档，不继续放在现行模型输出任务中；尚未做只删该句的因果实验。

严格Schema只能提高可消费性，不能保证判断正确。新原生high组W1限定银标15/15，W2为8/9；这些不是独立人工Gold，也没有正向HIT召回覆盖。W2 #14再次忽略C6的“且原因为技术资格/地面准备”前提，reason甚至承认归因较弱，却用“不构成相反解释”支持HIT。Policy的criterion和trigger_boundary都包含该必要原因，未发现二者相互矛盾。

这里暴露的是**把confidence规则误用于补足HIT前提**：没有支持相反解释，不等于必要事实已成立。后续Prompt应把“必要事实是否明确存在”和“已存在事实是否有二义性”分开强调，缺必要事实即明确NO HIT，不要求证明相反事实。无需增加研究阶段或新的模型调用。

## 切回high前的最小改进顺序

### 已经完成

- 四节点接入原生Schema协议、保持本地业务校验；R3显式回放不可变父轮输入，不跨provider依赖response_id；回执允许xhigh。
- 按用户要求放宽输出预算；静态系统前缀1小时缓存；原始输出/usage和失败仍保留。
- 自然生产四类turn均已成功，109项定向回归通过；W1R3另有生产适配器的隔离真实回放成功证据。

### 下一步优先：减少W2模型输出中的重复决策字段

建议只调整模型wire输出，内部W2业务模型、Router和下游契约保持现状：

```json
{
  "hit_condition_ids":[{"policy_id":"P1","condition_ids":["C1"]}],
  "related_policy_ids":["P2"],
  "confidence":"normal",
  "reason":"按Policy说明事实依据"
}
```

程序确定性构造：`policy_ids`取HIT分组的Policy身份，`policy_hit=bool(policy_ids)`，`policy_related=bool(related_policy_ids)`；再运行现有完整业务模型校验。这样不再让模型重复生成三个可由身份集合唯一推出的字段，也不从reason猜测身份，不把缺失判断强改为false。

实现应冻结新的wire版本及wire Schema哈希，仅对新执行包生效；历史回执按旧格式读取。仍保留Condition归属、候选集合、重复身份、HIT/RELATED互斥等检查。原始wire输出与转换后的业务对象分别留痕。不要为此引入复杂动态oneOf生成器或新研究阶段。

在输出协议之外，给W2 Prompt增加简短且明确的必要事实检查说明，移走旧字段兼容叙述。W1保留现有字段；如加最外层围栏的无损归一化，仅允许剥完整包装并记录warning，再执行完整验证，不能删除未知业务字段或猜测ID来伪造成功。

验收采用同一批冻结输入，先区分首次成功与一次修复后成功；另外补少量人工确认的真正HIT及混合HIT/RELATED样本。输出可消费率与业务准确率分别验收，不能用本轮全为NO HIT的九例银标来宣称交易判断全面通过。

本轮未自行切回high，未实施上述wire精简或业务Prompt修改；生产继续按用户要求临时使用xhigh。

## 部署和凭据边界

当前镜像：`doxagent-v2:haiku-rounds-bab0739d`。原运行镜像在远端镜像库已不可用于重建，因此使用现有依赖镜像及Git归档源码重建；未修改运行容器内文件。Compose当前发布覆盖只更新目标角色，保留其他角色的镜像和配置。

百炼主key更新于远端受限环境文件；Compose只保存变量引用，旧主key备份权限0600且父目录0700。Decision Model、API、Scheduler都验证新指纹，OpenRouter key未改变。未将用户提供的明文凭据提交仓库。其他常驻角色仍需在后续按需重建时读取更新后的环境文件，不声称本轮全量重启。

官方依据：[OpenRouter Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)、[Anthropic Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)。

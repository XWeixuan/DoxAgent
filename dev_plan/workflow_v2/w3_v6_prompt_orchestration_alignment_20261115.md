# 给W3提示词维护者：v6编排与输入/输出对齐说明

2026-11-15。本文描述**待实施的目标契约**，不是当前生产行为。用于拆分、对齐AGENTS/Skill/Operations；执行细节见同目录`w3_case_isolation_and_operations_refactor_plan_20261115.md`。

## 最少需要先知道的变化

W3不再是W1/W2裁判。W1最终判断NEW/OLD；W2最终判断Policy HIT/RELATED及Condition身份；W3 Trade独立决定LONG/SHORT/NO_TRADE。Operations只维护PIN/WATCH。

NEW+HIT可以NO_TRADE，NEW+NO HIT可以LONG或SHORT。对已依照既有LOW/WATCH等规则进入的OLD Case，OLD不自动禁止交易。W3不得靠改写上游判断使它们与方向“看起来一致”。上游NEW也不保证市场刚刚首次获知，Policy HIT不等于ticker级预期差充分成立；Trade仍需按方法完成自己的研究和时效判断。

事实抽取交给W1R3。W1R2/W2及入口必要步骤完成后，W3立刻运行；W1R3独立并行，不是W3前置。W1判NEW才进入该事实抽取分支，W3不用抽事实、找Event ID或补Delta。

## 1. 指令分工和实际注入顺序

| 文件 | 职责 | 何时可见 |
| --- | --- | --- |
| `prompts/persistent_runtime_v2/w3/AGENTS.md` | 共同身份、输入信任/边界、工具与阶段边界 | 公共模板初始化，Case继承 |
| `prompts/persistent_runtime_v2/w3/skills/skill.md` | Trade的独立研究、预期差、方向与时效方法 | 公共模板初始化，Case继承 |
| `prompts/persistent_runtime_v2/w3/operations.md` | PIN/WATCH维护方法和两数组交付 | 仅进入Operations时，全文放在任务JSON之前 |

三份文件一起版本化、冻结；运营时不会读取磁盘“最新版”。每个新Case从干净公共模板创建自己的物理线程，**不继承其他Case历史**。同Case的Operations仍继承本次Trade输入、输出及工具结果；这不是只有四字段结论的无历史新对话。

Operations回合形状：

```text
<本Case冻结的operations.md全文>

{"phase":"post_decision_operations","input":{...},"context_ref":"...","instruction":"...","output_fields":["pin_actions","watch_actions"]}
```

Trade只追加自己的阶段JSON；不会提前收到operations.md。AGENTS/Skill必须真的移除旧运营规则，否则它们仍被模板继承，编排不能替提示词自动消除冲突。可以保留“之后另有Operations阶段”的一句边界说明，不在Trade方法里要求预先归档、分配Event或维护状态。

编排实施只会添加占位operations.md。请在启用v6前交付最终三份正文；占位资产不能作为已完成提示词发布。本轮无须大改研究方法，也不要把编排版本、幂等、存储细节搬进业务提示词。周末sweep仍由其任务单独附加原有指令。

## 2. Trade收到什么

共同外壳仅：`phase / input / context_ref / instruction / output_fields`。运行版本、Schema版本、manifest指纹仍由后台管理，不要求Agent返回或查询它们。`output_fields`只是键名提示，完整结构由SDK Schema约束。

Trade的`input`保留：

| 字段 | 阅读口径 |
| --- | --- |
| ticker、message、cutoff_at | 当前标的、消息及原判断截点；消息published_at与cutoff不是同一概念，也不等于原事件首次公开时间 |
| additional_tags | 原有纯信息标签；不授予交易资格、不触发动作，不用它替代事实判断 |
| w1 | result、confidence、reason及events等原结构；是上游意见/可比事实，不是必须采纳的交易方向 |
| w2 | 仅policy_hit、policy_related、confidence及policy_matches；不再含上游长reason/内部Policy ID |
| policies | 与本次有关的裁剪后研究规则，见下；没有预设LONG/SHORT方向 |
| pins | 已有置顶消息、理由、到期状态及容量；Trade只读，不在其输出维护 |
| recent_trade | 同ticker、截点前48h实际ENTRY成交的历史Case，每项只有message及四字段w3_decision；部分成交算，接受但未成交不算 |
| contexts | 只有D1/D2名称及记录的cutoff_at；null是未知，不是假定最新 |
| alignment | 原status/error及存在时的新增事实对齐布尔值；缺失不等于false |

Trade不再收到trading_context、case_type、WATCH、candidate_eligibility、完整Policy方向/匹配范围，以及原72h意图长列表。后台仍执行交易资格、闭市候选、过期、退出等规则；字段隐藏不代表约束解除，W3不需要重建它们。

`additional_tags`中的RECENT_TRADE与recent_trade统一使用截点前48h实际ENTRY成交的冻结成员集合。只有当前Case与其中至少一个历史Case实际命中的Event身份或Policy HIT身份相同，才生成标签；部分成交算，仅RELATED、Prior引用或未成交意图不算。列表为空一定无此标签；列表非空但无身份匹配也无此标签。它仍只是信息，不授予交易资格、不触发重复交易拦截。

### Policy与W2指向

```json
{
  "w2": {
    "policy_hit": false,
    "policy_related": true,
    "confidence": "normal",
    "policy_matches": {
      "hit": [],
      "related": [{"policy_title": "容量交付进度", "condition_ids": null}]
    }
  },
  "policies": [{
    "title": "容量交付进度",
    "cutoff": null,
    "transmission": "相关研究传导说明",
    "activation_conditions": [{
      "condition_id": "C1",
      "criterion": "对应条件的事实标准",
      "calibration": {"reference_state": "原研究基线"}
    }]
  }]
}
```

这是结构示意，不是新业务规则或真实时间。HIT指向用`{policy_title, condition_ids:["C1"]}`；RELATED没有Condition证据时为null，不代表全部Condition命中，不要补造C1。Condition ID只在对应标题下有意义，不能单独把不同Policy的C1混起来。后台对同名标题做稳定展示消歧，同一Case的policies、matches和运营targets保持一致。

Policy cutoff表示该条Policy真实生成/维护时点；未知为null。它不是本次消息日期，也不是数据必然过期/实时的证明。

Trade不见policy_id、match_scope、condition decision、trigger_boundary、内部来源定位与版本；不需要通过工具把这些重新找回来。不能因为取消Policy预设方向，就把HIT简单翻译成LONG。

## 3. Trade必须输出什么

只有以下四键，严格JSON：

```json
{
  "decision": "NO_TRADE",
  "reason": "本次方向判断的理由",
  "prior_expectation": null,
  "expectation_delta": null
}
```

- decision只能LONG、SHORT、NO_TRADE，不再有POLICY_TRADE/EXPERT_TRADE。
- reason为非空字符串；每个字符串沿用4000字符上限。
- LONG/SHORT时prior_expectation和expectation_delta必须是非空字符串；NO_TRADE时二者可以是null或非空字符串，四个键仍必须显式提供。
- 不输出新旧结论、Policy裁判、Delta、PIN/WATCH动作、交易数量或执行指令。

Trade输出一旦提交，不等运营或W1R3。候选/执行是否接受与方向是不同维度，未成交不能倒推Agent当时输出NO_TRADE。

## 4. Operations收到什么、还要做什么

仅需在既有Trade历史上完成本Case的PIN/WATCH维护，不重新做第二轮方向研究，不复审W1/W2，不以找不到Event ID为由阻塞。

```json
{
  "phase": "post_decision_operations",
  "input": {
    "submitted_decision": {
      "decision": "NO_TRADE",
      "reason": "本Case已提交理由",
      "prior_expectation": null,
      "expectation_delta": null
    },
    "output_disposition": "NO_TRADE",
    "watch": {"limit": 2, "used": 0, "items": []},
    "pins": {"limit": 2, "used": 0, "items": []},
    "policy_targets": [{"policy_id": "示意内部身份", "title": "容量交付进度"}]
  },
  "context_ref": "w3ctx_示意",
  "instruction": "由编排提供的简短阶段说明",
  "output_fields": ["pin_actions", "watch_actions"]
}
```

`submitted_decision`是已提交Trade四字段；`output_disposition`是Runtime实际处置，可能为NO_TRADE、交易意图或候选相关状态，不能用它反向改方向。WATCH/PIN在首次运营时可比Trade快照更新，重试固定本次快照。

policy_targets仅给必要身份映射，不重新给完整Policy；Policy ID出现在Operations是WATCH绑定例外，不是要求Trade获取ID。

### 运营输出唯一Schema

```json
{"pin_actions": [], "watch_actions": []}
```

无动作就返回两个空数组。禁止额外novelty、policy、delta_candidates、reason或decision顶层键。

沿用的动作类型如下；两条例子是形状说明，不要求每Case产生动作：

```json
{
  "pin_actions": [
    {"action": "SET", "pin_id": null, "reason": "保留当前消息的后续参考价值"}
  ],
  "watch_actions": [
    {"action": "SET", "policy_id": "从policy_targets复制的实际ID", "reason": "待跟进的问题"}
  ]
}
```

| 动作 | 参数与边界 |
| --- | --- |
| PIN SET | pin_id必须null，绑定当前消息；不能用SET去指定任意历史消息 |
| PIN CANCEL | pin_id必须是本次pins快照中可见的实际ID |
| WATCH SET | policy_id必须是本Case W2确认HIT/RELATED的冻结Policy目标 |
| WATCH CANCEL | 只取消本次watch快照中的目标；target仅可取消不意味着允许重新SET |
| 两类动作共同 | 每条reason为非空字符串、最长4000字符；action只能SET/CANCEL；ID不可编造 |

当前各类有效状态容量仍为2，不是“输出数组最多2个动作”：一次可能先取消再设置。活动WATCH不因重复SET自动续期；CANCEL/SET同目标时沿用CANCEL优先；并发更新、过期或容量变化由后台按现有规则处理，不能尝试绕过。交易与否不自动强制产生/禁止这些动作，也不靠WATCH授予交易权。

## 5. 研究工具的新边界

- `context_ref`只是本阶段允许的冻结研究入口，不是每Case必须调用的步骤。
- w3_research的catalog/search/read可读D1、D2及二者合法原文/来源；不再允许Policy、PolicySet、Reference View、Reference Event或Policy usage，也不能从raw/source/runtime state/history等旁路取回。
- Trade内联的w1.events和裁剪policies依然可见；取消MCP Reference/Policy并不等于它们从输入消失。
- Web Search和原有行情/必要只读工具按需保留；不要新增“先查完D1/D2才能考虑Web”的强制流程。
- 只在实际选择了相关且尚未完整的资料时跟next_cursor；分页body可能是文本片段，不要把每一片都当完整JSON独立解析。
- PREPARING行情仍非阻塞，不忙轮询，不自行改走IBKR下单能力。工具失败或缺证据按业务方法处理，不自行扩权限。

## 6. 提示词对齐时最容易误写的地方

1. 不再要求运营补NEW/OLD、找合法E#、抽事实或证明Policy命中；不要通过PIN reason变相增加这些必交作业。
2. 不把W1 NEW、W2 HIT或历史LONG写成当前方向的先验答案；允许解释“上游成立但ticker预期差不足”。
3. 不把去掉WATCH/trading_context当成权限放开，不在输出里补执行字段。
4. 不把本Case继承Trade历史误写成跨Case持续记忆：新Case没有别的Case线程历史，只有明确内联的PIN/实际成交历史和研究资料。
5. Skill保留交易方法，AGENTS只保留共同边界，运营细则放operations.md；不靠后阶段指令覆盖仍留在共享资产中的相反规则。
6. 四badcase暴露的原披露时点与市场新颖性问题仍需要提示词方处理。输入裁剪与线程隔离可以减少锚定，但本方案没有证据保证它们自动修复这些判断错误。

模型参数本次不改：现有Policy关联Trade使用Sol/medium，普通Trade用Luna/max且Fast开启；Operations统一Luna/max、Fast关闭。不要在提示词中要求改档位或换模型。

交付三份实际提示词后，应与v6 SDK Schema、输入快照和MCP可见范围同批验证。代码激活前旧Case仍按旧bundle恢复，不能把本文两数组输出要求套到已冻结v5任务上。

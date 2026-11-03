"""Model-visible counterparts of existing local validators; no wire-schema changes."""

VERSION = "case-output-invariants.v1"

_CHECKS = {
    "w1_novelty_result": """输出前核对已有字段的一致性，不增加字段，不改变先前完成的事实判断：
- result 为 OLD 时，reference_ids 必须非空，且每个 Event 都来自本轮已加载集合。
- fact_attributions 中每个 event_id 必须同时出现在 reference_ids，不能重复；fact_ids 必须属于该 Event 的已加载 Canonical Facts。Provisional 参照不能编造 Canonical F#。
- 无引用或无 Canonical 归因时使用空数组；不要返回围栏、Schema定义或额外字段。""",
    "w2_policy_result": """输出前逐项核对现有身份字段；以下是程序实际验证的同一个合同，不是新的判断任务：
- policy_ids 只包含完整满足 Condition 的 HIT Policy；hit_condition_ids 必须是对象数组，每项为 policy_id 与非空 condition_ids。
- hit_condition_ids 中的 Policy 集合必须恰好等于 policy_ids；每个 Condition 必须属于对应的本轮候选 Policy。不要输出扁平 Condition 字符串数组。
- policy_hit 必须等于 (policy_ids 非空)。没有 HIT 时 policy_ids 和 hit_condition_ids 都为 []，policy_hit=false。
- related_policy_ids 只包含最终明确 RELATED、但没有 HIT 的 Policy；policy_related 必须等于 (related_policy_ids 非空)。没有 RELATED 时 related_policy_ids=[]，policy_related=false。
- HIT 与纯 RELATED 集合不得重叠；一条消息可以 HIT Policy A 并 RELATED Policy B。reason 中明确判为 RELATED 的 Policy 必须实际填进 related_policy_ids，不能只在 reason 中写身份而返回空列表。
- 只使用本轮候选中的完整 Policy/Condition ID，不重复身份，不输出历史 matched_condition_ids。
- 必要事实缺失意味着未确认 HIT；“没有支持相反解释”不能补足缺失前提。confidence 只评价事实的真实歧义，不用来放宽 HIT 的完整满足要求。
以上检查只校正表达的一致性，不从下游交易或归档效果反推结论。""",
    "w2_novel_fact_alignment_result": """输出前核对已有字段与本轮待判断集合：
- condition_results 覆盖每个待判断 HIT Condition；related_policy_results 覆盖每个待判断 RELATED Policy。
- 每个身份只判断一次，不漏项，不返回集合外的身份，不给出相冲突的重复判断。
- is_new 必须为严格布尔值，不是 null、字符串或未知枚举。相应待判断集合为空时才返回对应空列表。
- 不重新判断整条消息 NEW/OLD 或 Policy HIT/RELATED，不输出额外 reason 字段。""",
    "w1_fact_extraction_result": """输出前核对已有 candidates：
- 返回1至12个 candidate，每项只包含当前Schema字段，proposition非空，assertion_state保持原文事实状态。
- 没有明确时间时保留允许的 null，不编造日期；entities 使用原文实际支持的实体，不重复。
- 只编译当前 Capture Mode 与父轮确定的事实范围，不重新判定 NEW/OLD，不输出 Event/Fact ID 等额外字段。""",
}


def consistency_checks(schema_name: str) -> str:
    checks = _CHECKS.get(schema_name)
    return "\n\n# Final Output Consistency Checks\n" + checks if checks else ""

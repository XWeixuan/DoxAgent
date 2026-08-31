# O3_MAINTAIN

MAINTAIN 的目标是保持现有 Policy 的交易含义与 D2-provenanced Tradable Path，只把其现实坐标推进到最新状态。维护后的 Active Policy 应继续表达同一路径中仍面向未来、可直接交易的边界；优化对象是现实一致性与逻辑连续性，而不是发现更多变化或改写更多 Policy。

以 Current Published Policy Set 为已经成立的业务基线，通过最新 Reference View 发现可能改变 Policy 适用性的现实变化，只确认这些变化，并用最小业务 Patch 将 Policy 推进到新的现实状态。MAINTAIN 不重新建立完整 D2 Policy Surface。

## 1. Working Baseline

读取 `task.json`、`current_policy_set.json`、`reference_event_view.md` 和已有 `maintenance_candidates.jsonl`；如果存在 `runtime_maintenance_feed.json`，必须完整读取其中的 Reference View Delta、Trade Records 与 BADCASE Records；retry 时同时读取已经存在的 `policy_patch.json`。Current Policy Set 提供当前 Policy 逻辑、Calibration、稳定身份和 D2 provenance，workspace 中的候选与 Patch 表示本次运行已经完成的工作。

Runtime feedback 仅用于维护现有 D2-provenanced Policy：Trade Record 表示当日实际命中的完整消息与判定上下文，BADCASE 表示事实已知但 Policy 仍命中的漏维护信号。不得把 Runtime feedback 直接解释为成交订单，也不得脱离现有 D2 Path 新造经济逻辑。Reference View Delta 为空时，仍须处理 Trade/BADCASE；三者均为空时才可 NOOP。

Reference View 是现实变化入口，而不是全部世界状态。结合 Policy Set 的 `published_at`、既有 `event_library_ref`、事件时间和当前 Calibration，区分本轮可能的新进展与视图中原本存在的历史事实；较早事实如果明确与当前 Policy 状态冲突，同样构成需要处理的变化线索。

## 2. Possible Change Gate

先轻量扫描全部 Active Policies。对每条 Policy，联合查看 `title`、`match_scope`、`activation_summary`、`activation_conditions` 及其 `reference_state`，只问：

> Reference View 或明确的时间推进，是否提供了理由相信这条 Policy 的现实起点、待满足条件、Calibration 或适用状态可能已经变化？

没有变化信号即视为 `NO_CHANGE`，继续下一条，无需记录或研究。存在合理变化信号时，将其作为 `POSSIBLE_CHANGE` 写入 `output/work/maintenance_candidates.jsonl`，每行只含现有字段 `policy_id` 与 `reason`。

`reason` 说明可能需要维护的 Policy 状态，而不是复述相关消息。例如，“客户 qualification 已完成，C1 可能已成为持续现实，当前 `reference_state` 可能过时”比“出现客户相关新闻”更能指导后续判断。

重点识别四类变化：现实沿既有路径推进；某个 Condition 可能已经成立；比较基准或 Calibration 已经移动；原 Path 可能已经走完或失去适用性。完成全部 Policy 的候选扫描后，再进入研究与 Patch 判断。

跨过具有业务意义的时间节点可以形成 `POSSIBLE_CHANGE`，但时间流逝本身不证明 Condition 已满足；预定窗口已经过去只意味着需要确认实际结果。

## 3. Resolve Candidates

逐个 Candidate 检查 Reference View 是否能在不补充关键事实假设的情况下，确定 **Keep / Absorb / Recalibrate / Advance / Retire**，并完成该动作所需的判断或完整 Policy 修改。可以则直接处理；否则先明确仍缺失、且可能改变 disposition 的事实，再开展定向研究。

商业阶段、合同、监管、客户采用或生产状态通常适合 Web Search。Data MCP 仅在当前问题天然需要结构化、provider-specific 或高频数据，该数据自 Current Policy Set 发布后确有可能变化，且它对 Candidate disposition 必要时调用。Research 在已经足以确定 disposition，并完成该动作所需的判断或完整 Policy 修改时结束，服务于现有 Candidate，而不是逐 Policy 重做最新性检查。

每个 Candidate 收敛为以下一种业务结果：

- **Keep**：进一步确认后，现实没有改变 Policy；不产生 Patch。
- **Absorb established reality**：某个 Condition 已成为持续现实，而同一 Path 仍有剩余交易意义。将该现实吸收到剩余 Condition 的 `reference_state`，移除已经成为历史的 Condition，并重新表达剩余未来边界。这样未来同一条消息只需满足仍待发生的 Conditions。
- **Recalibrate**：Trigger 尚未发生，但现实起点、时间基准或阶段已经推进。联动检查 `reference_state`、`trigger_boundary`、`qualifying_evidence`、`criterion`、`activation_summary`，并在召回范围实际变化时更新 `match_scope`。
- **Advance or Retire**：原 Trigger 已经成为历史时，判断同一 Tradable Path 是否仍存在自然延续且具有直接交易意义。现实对象、原 expectation transmission 和 `decision` 不变，只是沿既有状态链继续推进，才属于同一路径；存在时推进到下一现实状态，路径已经走完或失效时 retire。

缺少完美数值不妨碍依据清晰的商业阶段或法律状态维护 Policy；变化结论应来自现实含义，而不是措辞优化。

## 4. Policy Continuity

同一 Tradable Path、同一 `decision`，仅现实基线、Condition 或 Calibration 推进时，保持原 `policy_id`；语义未改变的 Condition 保持原 `condition_id`。一旦修改 Policy，使 Condition、Calibration、`activation_summary` 和必要的 `match_scope` 重新保持一致。

Daily Maintenance 围绕现有 Policies 的同一现实路径推进。只有当现有 Policy 必须被重新拆分才能准确表达同一 D2-provenanced Path 时，才建立新 Policy，并沿用现有 Policy 中精确的 `shell_id + expectation_id + gap_id`；需要新的经济传导逻辑时留给下一次 D2/D3 INITIALIZE。

修改现有 Policy 时原样保留 stable `policy_id`。重新拆分产生的新 Policy 使用 `tmp_pol_...` 一类 temporary draft identity，后续确定性 ID 阶段负责分配稳定身份；Agent 不设计永久 ID。

## 5. Patch Gate

全部 Candidates 得到结论后，统一写入 `output/work/policy_patch.json`。严格使用现有字段：

```text
base_policy_set_version
event_library_ref
upsert_policies
retire_policy_ids
```

`base_policy_set_version` 与 Current Policy Set 一致，`event_library_ref` 使用 `task.json` 提供的最新引用。Keep 不进入 Patch；Absorb、Recalibrate、Advance 或重新拆分在 `upsert_policies` 中写入完整 Policy；Retire 写入 `retire_policy_ids`。同一 Policy 只采取一种动作。

最小 Patch 指只包含实际发生业务变化的 Policies。被修改的 Policy 仍是完整、内部一致的对象。没有实质变化时输出两个操作数组均为空的合法 Patch，由确定性流程保持 `NOOP`。

## 6. Completion

当全部 Active Policies 已完成轻量扫描、所有 Candidates 已收敛、未变化 Policy 未进入 Patch，且 `policy_patch.json` 符合现有 schema 时，本轮维护完成。返回节点 output schema 要求的小型 `O3RunResult`。

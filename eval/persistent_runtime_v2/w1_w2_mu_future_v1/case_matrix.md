# MU W1/W2 Future Corpus V1 — Frozen Case Matrix

## Frozen baseline

- Simulation window: 2026-09-04 through 2027-08-31.
- No earnings releases, earnings calls, quarterly results, earnings previews, or revenue/EPS/gross-margin guidance stories.
- Every body is English and at least 500 whitespace-delimited words.
- Novelty distribution: 13 NEW / 12 OLD.
- Final W2 `policy_ids` non-empty: 4 / 25 cases.
- Model-visible source message fields are only `ticker`, `title`, and `body`.

## Case briefs

| Case | Kind / form | Required story semantics | W1 Gold | W2 Gold |
|---|---|---|---|---|
| MU-W12-001 | NEWS / wire follow-up | Re-report E14: Micron secured the NVIDIA HBM4 design win, began volume shipments for Vera Rubin, and exceeded $1B of HBM4 revenue. Add no new stage, customer, volume, timing, or economics. | OLD normal; R1 requires E14; refs E14 | no hit normal |
| MU-W12-002 | NEWS / trade feature | Repackage E15 and E20: HBM sold out through 2027 and HBM consumes about three wafer starts per equivalent conventional-DRAM bit. All apparent novelty is interpretation/background. | OLD normal; R1 requires E15,E20; refs E15,E20 | no hit normal |
| MU-W12-003 | NEWS / business feature | Revisit E7/E8: Micron's $250B US investment plan and continuing New York fab development. No permit, groundbreaking, schedule, capacity, customer, or financing change. | OLD normal; R1 requires E7,E8; refs E7,E8 | no hit normal |
| MU-W12-004 | NEWS / long market roundup | Multi-company roundup dominated by unrelated semiconductors. MU portion only repeats known memory rally/tight-supply/HBM background from E56/E23/E27; no new MU fact. | OLD normal; R1 includes E56 plus closest supply event; refs may be E56,E23,E27 | no hit normal |
| MU-W12-005 | NEWS / peer-industry report | Re-report SK hynix HBM4 supply into NVIDIA and Samsung HBM4E sampling from E39/E41; MU appears only through known E14/E17 comparisons. | OLD normal; R1 requires E39,E41; refs E39,E41 (E14/E17 allowed) | no hit normal |
| MU-W12-006 | NEWS / policy retrospective | Long retrospective repeating the 2024 CHIPS support/buyback restriction and already-known US restrictions on Chinese-memory sourcing from E12/E117. No new rule, scope, effective date, award, or compliance action. | OLD normal; R1 requires E12,E117; refs E12,E117 | no hit normal |
| MU-W12-007 | NEWS / legal report | Re-state Netlist's complaint and requested ITC exclusion/cease-and-desist remedies from E121/E122. No ruling, hearing, settlement, new patent, or procedural development. | OLD normal; R1 requires E121,E122; refs E121,E122 | no hit normal |
| MU-W12-008 | OFFICIAL / Micron strategic-agreement update | Corporate background release repeats at least 16 long-term agreements, roughly $100B cumulative revenue arrangements, and about $22B deposits/cash commitments from E2/E3. Explicitly no new contract, cash receipt, repeated delivery, cancellation, or revised amount. | OLD normal; R1 requires E2/E3; refs E2,E3 | no hit normal; strategic-agreement Policies are scope-only |
| MU-W12-009 | NEWS / historical peer explainer | Re-report Samsung's February 2026 HBM4 mass production and commercial shipments from Published Event E43, which is outside Reference View. No new customer breadth, repeated delivery, yield, lead-time, or price evidence. | OLD normal; R1 requires E43; refs E43 | no hit normal |
| MU-W12-010 | NEWS / product qualification | Micron HBM4E moves from E17 development/expected 2027 HVM into completed production qualification at one named AI customer; no final binding order or extra wafer/packaging allocation. | NEW normal; R1 requires E17; refs E17 | no hit normal; `pol_4fbb...` near miss |
| MU-W12-011 | NEWS / local-project report | A specific New York fab advances beyond E7/E8 plans: final local permit and a dated groundbreaking are confirmed. Do not claim qualified output, utilization, or orders. | NEW normal; R1 requires E7/E8; refs E7 or E8 | no hit normal |
| MU-W12-012 | NEWS / supplier technology report | Samsung formally confirms the roughly 80% HBM4 yield that had only been rumored in E42. No multiple-customer repeat delivery and no DRAM lead-time or contract-price decline. | NEW normal; R1 requires E42/E43; refs E42 allowed | no hit normal; `pol_83c...` near miss |
| MU-W12-013 | NEWS / customer deployment | One major CSP completes production qualification of Micron's 245TB QLC SSD and takes its first commercial batch. Only one customer and one delivery period. | NEW normal; R1 should include E47 or related QLC event; historical ref allowed | no hit normal; `pol_17b9...` near miss |
| MU-W12-014 | NEWS / device-launch aggregation | Long launch roundup with mostly unrelated device facts. One genuinely new MU-relevant fact: one major phone platform raises base-model DRAM capacity by one tier. Only launch/preorder data; no complete sales period and no confirmed mobile-DRAM procurement growth. | NEW normal; existing mobile-cost events may be recalled; old refs optional | no hit normal; `pol_89ac...` near miss |
| MU-W12-015 | NEWS / supply-chain agreement | A chemicals supplier and Micron sign a new multi-year US supply agreement for a named critical input, adding a new facility/volume commitment. No shortage, allocation, unpassable price increase, production cut, utilization change, or margin consequence. | NEW normal; no exact known Event required | no hit normal; `pol_c8b...` scope-only |
| MU-W12-016 | NEWS / low-MU-relevance CXL feature | Article is mainly about a CSP server architecture. It starts one production CXL-pool deployment and lists Micron as one qualified module supplier, but gives no comparable total-DRAM increase and no actual procurement growth. | NEW normal; no exact known Event required | no hit normal; `pol_ba44...` near miss |
| MU-W12-017 | NEWS / enterprise-SSD wording ambiguity | A customer calls a Micron SSD program “repeat production deployment,” but the body establishes it is one customer receiving a second lot within the same initial qualification campaign, not two customers/two delivery periods. Compact projection leaves a genuine boundary ambiguity; full calibration resolves no hit. | NEW normal | W2 R1 `pol_17b9ee999d1e3ca3ab45`, low; R2 empty, normal |
| MU-W12-018 | OFFICIAL / Micron operations release | Micron confirms an unplanned critical fab or advanced-packaging interruption, lowers saleable-bit output or customer deliveries, and gives a recovery window beyond normal maintenance. | NEW normal; no OLD coverage | clear hit `pol_a2fb4594422ae0c078a2` C1, normal; W1 R3 expected |
| MU-W12-019 | NEWS / same-day syndicated rewrite | A later same-trading-day article restates the exact core interruption facts in MU-W12-018 with no new consequence. Ordered replay must use the provisional fact(s) from case 018. | OLD normal; required provisional E# from 018 | same clear hit `pol_a2fb4594422ae0c078a2` C1, normal; OLD+Policy BADCASE, no R3 |
| MU-W12-020 | OFFICIAL / peer NAND project update | A major NAND supplier delays a 2027 qualified-output milestone by only one quarter. It says inventory is lean but does not establish a two-quarter delay or continued tight lead times/allocation. | NEW normal | no hit normal; `pol_8ae20cddcbcd95e5df29` near miss |
| MU-W12-021 | NEWS / detailed AI-platform supply-chain report | A final production BOM raises VR300 HBM4E capacity to 640GB versus the known 512GB baseline, creates an incremental binding Micron HBM order, and raises Micron HBM wafer/packaging allocation. A secondary but explicit section says the same major platform makes Micron 245TB QLC the standard production data tier, raises per-cluster deployed capacity, confirms Micron qualification, and has entered repeat purchasing. Compact projection lacks the 512GB calibration, so R1 is low; R2 confirms both Policies and ranks the HBM Policy first because it is the principal news. | NEW normal; R1 should include E171 and E14/E15 plus the closest QLC Event; E171 historical ref allowed | R1 ordered `pol_4fbb78816d7863893f09`,`pol_df1332e7ce712ae0dc0c`, low; R2 preserves both, C1 for each, normal |
| MU-W12-022 | GOVERNMENT / regulator notice | A new Chinese procurement notice is effective and appears to expand restricted memory categories, with reported order cancellations, but the operative translation leaves genuinely unresolved whether the newly covered category/customer was previously serviceable by Micron. | NEW normal; prior restriction E117 or related Event may be recalled | R1 `pol_e28a1b1e8eedc9bf53f5`, low; R2 same Policy, low because the new-scope question remains unresolved |
| MU-W12-023 | OFFICIAL / peer product update | Samsung describes HBM4 as “now in broad commercial delivery,” but the release never says whether this is a new multi-customer repeat-delivery expansion or a restatement of E43's existing mass production/commercial shipments. The ambiguity can flip W1. | OLD low; R1 requires E43; refs E43 | no hit normal; missing market relaxation and repeat-customer evidence |
| MU-W12-024 | NEWS / same-day customer-story rewrite | Later article repeats exactly MU-W12-013's one-CSP qualification and first batch with no new customer, period, order, or deployment stage. Ordered replay uses provisional facts from case 013. | OLD normal; required provisional E# from 013 | no hit normal; `pol_17b9...` remains near miss |
| MU-W12-025 | NEWS / pre-production SSD issue | Micron and a customer identify and fix a firmware issue during pre-production sample validation. No production deployment is paused, no qualification is withdrawn, and no order/shipment is cut. | NEW normal | no hit normal; `pol_07c...` near miss |

## Counts and required W2 continuation cases

- NEW: 010-018, 020-022, 025 = 13.
- OLD: 001-009, 019, 023, 024 = 12.
- Final non-empty Policy: 018, 019, 021, 022 = 4.
- W2 R2 hit/normal: 021.
- W2 R2 no-hit/normal: 017.
- W2 R2 still-low: 022.
- W1 R3: 018 and 021 only.

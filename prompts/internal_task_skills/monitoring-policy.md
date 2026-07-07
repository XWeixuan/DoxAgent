+++
kind = "internal_task_skill"
id = "monitoring-policy"
name = "Monitoring Policy"
version = "2026.07.07"
applicable_agents = ["O4"]
applicable_task_types = ["generate_monitoring_policy"]
workflow_nodes = ["GenerateMonitoringPolicy"]
+++
# O4 Monitoring Execution Policy

Create message-driven runtime policies for W2.

In this node, do not act as a price-action or technical-analysis agent. Use market context only as background. The policy must be triggered by news or social message content, not by price movement.

## Goal

Monitoring Policy defines positive W2 match rules:

1. `direct_trade`
   A high-trust message whose factual content can create a trade-intent record candidate.

2. `escalate`
   A high-impact, ambiguous, contradictory, weakly sourced, or context-heavy message that needs O3 judgment.

Do not create `cache` policies. Runtime W2 uses `NULL` for relevant unmatched messages and `Irrelevant` for noise.

If either `direct_trade` or `escalate` is intentionally omitted, the document-level `no_action_rationale` must explain why that policy type is absent.

## Trigger rule

`trigger.condition` is the only core trigger.

It must be a message-content condition that W2 can judge from one incoming source message.

Good triggers:

- official announcement of a new customer contract
- updated revenue, margin, capex, or guidance disclosure
- regulatory investigation, approval, fine, ban, or lawsuit
- confirmed product launch, delay, cancellation, outage, or security incident
- named partner, supplier, customer, regulator, executive, or peer event that changes an expectation
- credible report of a fact that updates a Known Event

Bad triggers:

- stock breaks support or resistance
- closing price above or below a level
- volume above moving average
- 20-day / 30-day correlation
- relative performance versus peers
- SOXX / QQQ / SPY movement
- RSI, moving average, technical breakout
- "price holds above X"
- "market reacts strongly"

Do not put price, volume, technical, or correlation conditions in `trigger.condition`.

## DTC vs EBA

Use `direct_trade` only when the message itself is high-trust and fact-complete.

Good DTC sources or facts:

- company official announcement
- SEC filing
- earnings release or transcript
- regulator official action
- confirmed major customer / partner / supplier announcement
- multiple high-quality media reports confirming the same concrete fact

Use `escalate` when the message is important but requires judgment:

- rumor or single-source report
- social post with a concrete but unverified claim
- supplier, customer, peer, or industry news that needs ticker-specific inference
- message conflicts with Known Events or expectation units
- message may be material but needs source check, price-in judgment, or O3 context

Do not use DTC for broad bullish/bearish tone, analyst opinion, generic sector news, social hype, or price action.

## Rule fields

Every rule must include:

- `policy_id`
- `policy_type`: `direct_trade` or `escalate`
- `scope`: bind to expectation_unit_id and relevant ticker/entity scope
- `trigger`: message-content condition
- `confirmation`: optional non-trigger checks for O3 or route layer
- `action`
  - `direct_trade`: include `side`, `conviction`, and `size_bucket`
  - `escalate`: include `send_to`, `question`, and `priority`
- `risk_guard`: what blocks direct trade or forces escalation
- `reasoning`: one concise sentence

`confirmation` must not be required for W2 trigger matching. If price or market data is useful, place it in `confirmation` or `risk_guard`, never in `trigger`.

## Coverage

Policies should cover message events that can change:

- expectation validity
- timing
- magnitude
- probability
- downside risk
- upside catalyst
- known-event status

Prefer a small set of precise rules over broad generic rules.

Do not output generic rules such as "monitor ticker-relevant signals" or "trade on positive AI news."

Do not include time fields, `source_condition`, `cache_label`, or `handling`.

Return only `MonitoringPolicyDocument`.

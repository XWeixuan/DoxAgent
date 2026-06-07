+++
kind = "internal_task_skill"
id = "expectation-detail"
name = "Expectation Detail"
version = "2026.06.01"
applicable_agents = ["O1"]
applicable_task_types = ["generate_expectation_detail"]
output_requirements = ["ExpectationDetailResult"]
+++ 
# Expectation Detail

You are working on one expectation unit only. Treat the existing expectation name, direction, and market view as the scope. Complete these sections:

III. Fulfilled facts
IV. Key variables and current status
V. Event forecasts / monitoring direction

Use the current expectation unit, DoxAtlas narrative evidence, Document 1, and available price context. DoxAtlas remains important for market-side interpretation; Document 1 is used to clarify fundamentals, macro, industry, and price background.

## III. Fulfilled facts

Identify important facts that the market already knows and may have priced into this expectation.

For each fact, explain:

* what happened;
* when it happened, if known;
* why it matters to this expectation;
* how the stock reacted, if price context is available;
* whether the reaction suggests the fact is already priced in, only partly priced in, or still has incremental room.

Focus on facts that shaped the current expectation. Do not list every related news item.

End this section with a short judgement:

* what the market appears to have priced in;
* what may already be fully reflected;
* what still has room to change the expectation.

If price reaction evidence is missing or unclear, state the uncertainty and leave room for O4 review.

## IV. Key variables and current status

Identify the variables that will determine whether this expectation is confirmed, weakened, or overturned.

Cover:

* important historical facts behind the expectation;
* current real-world variables;
* current status of each variable;
* what is relatively certain;
* what remains unresolved;
* relevant fundamental or industry judgement from Document 1.

Keep the section focused on this expectation unit. Do not write general company background.

## V. Event forecasts / monitoring direction

Translate the expectation into future monitoring logic.

Cover three types of events:

1. Known upcoming events
   Events already visible on the calendar or likely to occur. Explain the possible positive and negative interpretations.

2. Positive events
   Events that would strengthen the expectation if they occur.

3. Negative events
   Events that would weaken, delay, or overturn the expectation if they occur.

Events should be monitorable through news, filings, earnings, guidance, orders, product progress, regulatory updates, industry data, macro data, or market discussion.

## Evidence standard

Every major judgement should be grounded in available DoxAtlas evidence, Document 1, or price context.
If evidence is insufficient, mark the point as uncertain instead of filling the gap with assumptions.

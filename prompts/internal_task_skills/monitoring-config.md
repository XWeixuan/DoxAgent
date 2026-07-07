+++
kind = "internal_task_skill"
id = "monitoring-config"
name = "Monitoring Config"
version = "2026.07.07"
applicable_agents = ["O2"]
applicable_task_types = ["generate_monitoring_config"]
workflow_nodes = ["GenerateMonitoringConfig"]
+++
# O2 Monitoring Config

Build message-source coverage for Document 3 runtime.

Monitoring Config is not a policy document and not a research note. It defines what the Message Bus should watch so W1/W2/O3 can receive useful media and social messages.

## Goal

Create API-shaped monitoring items that cover:

- ticker-level company news
- policy-relevant catalysts
- known-event update paths
- key products, projects, customers, suppliers, regulators, executives, and peers
- high-value social or X sources when available

Each item must explain which expectation, known-event family, or policy-relevant message type it serves.

## Source coverage

Use sources by their real interface:

1. `benzinga_news`
   - media, by ticker
   - optional `search_terms`
   - use for company news and fast market media coverage

2. `finnhub_company_news`
   - media, by ticker only
   - no search parameters

3. `stocktwits_messages`
   - social, by ticker only
   - no search parameters
   - use for ticker chatter, early social recaps, and retail reaction

4. `tikhub_x_search`
   - social, by parameter
   - `search_terms` only
   - use for company + product + project + regulator + catalyst terms

5. `tikhub_x_user_posts`
   - social, by parameter
   - `usernames` only
   - use only for concrete official, executive, regulator, industry, or high-signal accounts

6. `newswire_rss`
   - media, by parameter
   - `rss_urls` only
   - use for company IR, press releases, regulatory or industry feeds when concrete URLs are known

## API shape

For each monitoring item, `tool_input` must contain only:

- `ticker`
- `source_id`
- `enabled`
- `mode`
- `reason`
- plus fields supported by that source

Allowed parameter fields:

- `benzinga_news`: `search_terms` only
- `finnhub_company_news`: ticker only
- `stocktwits_messages`: ticker only
- `tikhub_x_search`: `search_terms` only
- `tikhub_x_user_posts`: `usernames` only
- `newswire_rss`: `rss_urls` only

Never put these fields inside `tool_input`:

- `keywords`
- `source_filters`
- `extra`
- `poll_interval_seconds`
- `expectation_id`
- `priority`
- `trigger_condition`

Keep `expectation_id`, `priority`, `trigger_condition`, `base_keywords`, `extra_keywords`, `related_entities`, and explanatory text as MonitoringItem metadata only.

## Parameter limits

Keep edits small:

- at most 3 `search_terms`
- at most 2 `usernames`
- at most 3 `rss_urls`

Use concrete API-ready terms/accounts/URLs. Do not put natural-language explanations inside parameters.

## Quality rules

Prefer coverage clarity over volume.

Every monitoring item must answer:

- what message type it catches
- why this source can catch it
- which expectation or known-event family it supports
- what would be missed without this item

Do not create policy actions in this node.

Return only `MonitoringConfigDocument`.

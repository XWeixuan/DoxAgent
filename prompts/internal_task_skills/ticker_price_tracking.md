+++
kind = "internal_task_skill"
id = "ticker_price_tracking"
name = "Ticker Price Tracking"
version = "2026.06.01"
applicable_agents = ["O4"]
+++
# Ticker Price Tracking

This skill writes Ticker Price Tracking report.

## Task

Analyze the ticker’s price action over the past 1 month and compare it with the broader market, relevant sector, and close peers.

## How to analyze

Start with the ticker itself:

* 1-month return direction and magnitude;
* major up/down days or sharp intraday moves;
* volatility level and whether it is expanding or compressing;
* volume changes, especially abnormal volume around large moves;
* trend structure: uptrend, downtrend, range-bound, rebound, breakdown, or failed breakout;
* simple technical levels: recent high, recent low, support/resistance area, moving-average position if available.

Then compare with market context:

* compare against major indices such as SPY, QQQ, or relevant benchmark;
* compare against the sector ETF if available;
* compare against close peers if available;
* judge whether the ticker is outperforming, underperforming, or moving with the market.

## What to conclude

Explain what the price action suggests from a market-behavior perspective:

* stock-specific strength or weakness;
* sector-driven movement;
* broad risk-on / risk-off movement;
* unclear or mixed pattern.

Keep attribution cautious. You may describe correlation and divergence, but do not claim a news or narrative cause in this task.

## Standard

Be concrete and concise. Focus on what the chart and relative performance show. If data is missing, state the limitation instead of filling the gap.

+++
kind = "external_skill_package"
id = "macro-analysis"
name = "Macro Analysis"
version = "2026.06.01"
source_project = "HKUDS/Vibe-Trading"
source_kind = "vibe_trading"
applicable_agents = ["C2"]
applicable_task_types = ["generate_global_research"]
allowed_tools = ["fred.series_observations", "bls.timeseries", "bea.nipa_data", "fed.fomc_calendar_materials", "polymarket.market_probability", "twelvedata.daily_ohlcv", "yfinance.daily_ohlcv"]
output_requirements = ["macro regime", "risk scenarios", "monitoring indicators"]
+++
---
name: global-macro
description: US macro analysis framework (Federal Reserve policy transmission / Treasury yields / geopolitical risk / capital flows), used to build macro factor signals that drive US market allocation.
category: analysis
------------------

# Macro Analysis

## Overview

Builds a macro analysis framework from three dimensions: Federal Reserve policy, Treasury yield curve, and geopolitics. Outputs quantifiable macro factor signals to drive US market allocation decisions. Core logic: macro cycles determine major asset direction, while micro-level timing is delegated to other skills.

## Core Concepts

### 1. Federal Reserve Policy Transmission Chain

```
Policy-rate changes → Treasury yield curve → credit spreads → financing costs for the real economy → corporate earnings → equity valuation
```

**Monitoring framework for the Federal Reserve:**

| Central Bank          | Core Indicators    | Forward Signals            | Lagging Confirmation         |
| --------------------- | ------------------ | -------------------------- | ---------------------------- |
| Federal Reserve (Fed) | FFR, dot plot, SEP | CME FedWatch probabilities | nonfarm payrolls / CPI / PCE |

### 2. Geopolitical Risk Assessment

**Quantitative approach (proxy for the GPR index):**

```python
# Geopolitical risk proxy indicators
risk_indicators = {
    "vix": "Fear index > 25 = high risk",
    "gold_oil_ratio": "Gold / oil > 25 = rising risk aversion",
    "usd_index": "DXY jump > 2% / week = capital flowing back to USD",
    "credit_spread": "IG spread > 150bp = credit tightening"
}
```

**Typical asset impacts of geopolitical events (historical averages):**

* Local conflicts: gold +3-5%, oil +5-15%, equities -2-5%, with impact lasting 1-4 weeks
* Trade friction: affected sectors -10-20%, beneficiary substitute sectors +5-10%, lasting 3-6 months
* Financial sanctions: sanctioned-country currency -10-30%, commodity supply side hit

### 3. US Capital Flow Tracking

**Key data sources:**

* EPFR fund flows: weekly net inflows into US equity / bond funds
* US Treasury TIC data: monthly, showing changes in foreign holdings of Treasuries
* FX reserve changes: quarterly, indicating central-bank asset allocation direction

## Analysis Framework

### Steps for Building a Macro Dashboard

1. **Data collection**: rates (US 10Y Treasuries), FX (DXY), commodities (gold / oil / copper), capital flows (EPFR / TIC)
2. **Cycle positioning**: which stage are we in now: hiking / cutting / pause? Strong-dollar or weak-dollar cycle?
3. **Factor scoring**: score each macro factor from -2 to +2 (-2 = extremely bearish, +2 = extremely bullish)
4. **Asset mapping**: macro factor scores → recommended weights for major US asset classes

### Example Macro Factor Scoring

```python
macro_factors = {
    "fed_policy": +1,      # Hiking pause, dovish tilt
    "geopolitical": 0,     # Neutral geopolitical risk
    "usd_cycle": -1,       # Stronger USD
}
# Composite score = sum(values) / len(values) = 0 → neutral
```

## Output Format

```
## Macro Analysis Report

### Cycle Positioning
- Federal Reserve: [late hiking / pause / early cutting]
- Dollar cycle: [strong / range-bound / weak]

### Factor Scores (-2 ~ +2)
| Factor | Score | Basis |
|------|------|------|
| Central bank policy | +1 | Fed paused hiking and the market expects cuts this year |
| FX pressure | -1 | DXY strengthened and tightened financial conditions |
| Capital flows | +2 | US equity fund inflows continued |

### Asset Allocation Recommendations
- US equities: [overweight / neutral / underweight] — rationale
- US Treasuries: [overweight / neutral / underweight] — rationale
- Gold: [overweight / neutral / underweight] — rationale

### Risk Warnings
- [specific risk events and potential impacts]
```

## Notes

* Macro analysis provides directional guidance, not precise timing. Leave timing to skills such as `technical-basic` or `volatility`
* Federal Reserve policy judgment should be based on official statements and meeting minutes. Do not over-interpret unofficial messages
* Geopolitical shocks are usually short-lived (1-4 weeks) unless they change fundamentals (such as long-term sanctions or trade wars)
* This framework is not investment advice and is for research backtesting only


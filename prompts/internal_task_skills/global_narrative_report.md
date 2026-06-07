+++
kind = "internal_task_skill"
id = "global_narrative_report"
name = "Global Narrative Report"
version = "2026.06.01"
applicable_agents = ["O1"]
+++
# Global Narrative Report

Write a global narrative report. This report explains the overall narrative structure around the ticker. Treat each finalized expectation unit as a structured narrative. The goal is to show how these narratives relate to each other and how they shape the market’s understanding of the ticker.

## Task

Build a concise narrative framework for downstream agents. Use the finalized expectation units as the main reference. Use DoxAtlas narrative evidence and Document 1 only to clarify the origin, strength, and relationship of each narrative.

## What to explain

### 1. Dominant narrative structure

Explain which narratives are central and which are secondary.

Focus on why certain narratives currently dominate market attention, valuation logic, risk perception, or future price sensitivity.

### 2. Inter-narrative relationships

Explain the relationships between narratives.

Common relationships include:

* one narrative reinforces another;
* one narrative offsets or challenges another;
* one narrative is the condition for another to work;
* one narrative is short-term, another is long-term;
* one narrative is about upside, another is about downside risk;
* multiple narratives compete to define how the market reads the ticker.

### 3. Narrative hierarchy

Explain whether the ticker is being understood through:

* one dominant master narrative;
* several parallel narratives;
* a bullish narrative versus a risk narrative;
* a transition from an old narrative to a new narrative;
* a fragmented narrative structure with no clear center.

### 4. Narrative treatment

Briefly explain how the final framework was formed:

* which narratives were retained as core expectation units;
* which were merged into broader narratives;
* which were downgraded to background context;
* which were excluded as weak, repetitive, stale, or not useful for future monitoring.

## Standard

Keep the report synthetic and concise. It should help later agents quickly understand the ticker’s overall narrative logic and the relationship between its core expectation units.

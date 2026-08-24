+++
kind = "internal_task_skill"
id = "known-events-review"
name = "Known Events Review"
version = "2026.07.07"
manual_only = true
applicable_agents = ["A1"]
+++
# Known Events Review

Review Known Events as W1 runtime memory.

This skill is registered for a future Known Events review lifecycle only. It must not be auto-injected or bound to a workflow node until the workflow has a real staged patch, review, resolve, and promote path.

Do not rewrite the document. Identify only issues that harm novelty detection.

Check:

- recent 30-day material facts are not obviously under-covered
- events are atomic facts, not narrative summaries
- `core_fact` is factual and matchable
- duplicate keys are compact and useful
- old news, recaps, and widely discussed catalysts are included
- price action, thesis, sentiment, and narrative ranking are not treated as facts
- expectation links are useful but not forced

Blocking issues:

- too few events for an active ticker
- major recent fact families missing
- many events are broad summaries
- duplicate keys contain full sentences or useless fragments
- unsupported claims are promoted as Known Events

Minor wording issues are not blockers.

Follow the current required review schema. Raise concise objections only for material runtime risks.

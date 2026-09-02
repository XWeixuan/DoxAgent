# O4_CONFIGURE

Input is a frozen PolicySet plus its Document 2 expectation shell context. Build one immutable
Configuration Plan around Source Needs, not one source per policy.

First read Default Profile, all registered sources and parameter schemas, the ticker's bindings,
polling/streaming state, and all crawler assets. Aggregate policies that share disclosure actor,
channel and observability target. For each material policy choose exactly one resolution:
KEEP_DEFAULT, CONFIGURE_REGISTERED_SOURCE, ENABLE_EXISTING_CRAWLER, NEW_CRAWLER_REQUIRED, or an
explicit NO_DEDICATED_SOURCE.

Map in this order: ticker binding, registered SourceDefinition, existing crawler asset, new crawler.
CONFIGURE applies minimal changes for defaults/registered/existing crawler assets now. A new source
capability (crawler, RSS, X account or search strategy) is admitted only after real web search/visit
and publication-history inspection; unchanged current/default capability needs no redundant proof.

For NEW_CRAWLER_REQUIRED provide a concrete Source Need, priority, one primary candidate and only
genuinely equivalent/accessibile/worthwhile ordered alternatives. Stop adding candidates when
marginal coverage is smaller than recurring polling plus downstream Runtime/LLM cost. Every PolicySet
policy_id must appear in exactly the appropriate Source Need coverage. Record baseline, applied
changes, admission evidence, deliberate omissions and the stopping rationale. Do not deliver new
crawler code in this node.

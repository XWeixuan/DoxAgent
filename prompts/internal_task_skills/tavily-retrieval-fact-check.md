+++
kind = "internal_task_skill"
id = "tavily-retrieval-fact-check"
name = "Tavily Retrieval And Fact Check"
version = "2026.06.01"
applicable_agents = ["A2"]
applicable_task_types = ["fact_check", "delegated_retrieval", "review_expectation_field"]
output_requirements = ["DelegatedRetrievalResult", "source_refs", "confidence", "unknowns", "query_log"]
guardrails = ["Do not call SEC, issuer-material, DoxAtlas, or fact_check.search tools.", "Do not treat missing Tavily evidence as a confirmed fact."]
+++
Use Tavily search and extract only to answer fact-check and delegated information-retrieval questions. Cite sources, return confidence, and keep unsupported information in unknowns.

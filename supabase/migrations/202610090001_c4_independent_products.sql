-- Keep historical Global Research bundles readable; new runs publish independent C4 products.
ALTER TABLE doxagent.codex_global_research_bundles
  ADD COLUMN IF NOT EXISTS entity_network_report text NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS c4_product_status jsonb NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN doxagent.codex_global_research_bundles.entity_network_report
  IS 'Independent C4e network-build Markdown research report';
COMMENT ON COLUMN doxagent.codex_global_research_bundles.c4_product_status
  IS 'Per-product available/empty/failed status for future_nodes, entity_relations, entity_network_report; empty object means historical unknown';

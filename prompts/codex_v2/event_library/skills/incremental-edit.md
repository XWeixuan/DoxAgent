# Incremental Event reconstruction and edit

Read the prior candidate map, then load the full Event Detail for every selected stable Event ID. Each Detail contains all active Facts and must be considered as a unit. Reconstruct occurrences and prepare complete revisions only for affected Events; omit untouched Events. Preserve unresolved items as `KEEP_PENDING`. Write work products under the attempt work path and return `PENDING` with complete Delta coverage.

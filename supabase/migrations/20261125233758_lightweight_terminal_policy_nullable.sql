-- Apply before enabling the first LIGHTWEIGHT ticker. Existing FULL rows remain valid.
ALTER TABLE doxagent.persistent_runtime_v2_terminal_cases
  ALTER COLUMN policy_set_version DROP NOT NULL;
-- running_mode remains in the existing projection_json; no row rewrite or new index.
ALTER TABLE doxagent.persistent_runtime_v2_terminal_cases
  ADD CONSTRAINT runtime_terminal_running_mode_policy CHECK (
    (coalesce(projection_json->>'running_mode', 'FULL') = 'FULL' AND policy_set_version IS NOT NULL)
    OR (coalesce(projection_json->>'running_mode', 'FULL') = 'LIGHTWEIGHT' AND policy_set_version IS NULL)
  ) NOT VALID;

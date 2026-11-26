# CDECR OpenRouter release — 2026-11-24

Production executor image: `doxagent-cdecr:openrouter-20261124` (running, healthy).
Base rollback image: `doxagent-cdecr:before-openrouter-20261124`.
Remote backup directory: `/home/ubuntu/cdecr-openrouter-20261124/backup`.

Non-embedding nodes use `deepseek/deepseek-v4.1-flash-1017` through OpenRouter, pinned to `sail-research/fp4` with provider fallback disabled. General nodes retain M2 model profile / reasoning none across scheduler lanes. Package clustering uses low; description uses none. Local .env/.env.v2 and remote .env.v2 are configured. The remote current Compose overlay pins the executor image and settings. Other running services were not recreated.

Embedding remains `qwen3.7-text-embedding` via DashScope. The executor and local configuration use key SHA-256 prefix `99e52ce2871e` (no secret stored here).

The adapter translates internal Responses calls to Chat Completions, preserving the complete input and schema in JSON-object instructions; local business validation remains responsible for schema validation. Provider-side strict JSON Schema is not asserted. Previous-response continuation is rejected rather than silently discarded. Sync, async and Package none/low real requests all returned the expected JSON; embedding returned a vector. Running executor configuration and health were verified after deployment. No complete ticker initialization was started by this release, so end-to-end semantic quality and costs have not been established.

Validation: focused suite 43 passed; full CDECR suite 338 passed, 4 skipped, 1 failed. The failure is the existing `test_induction_prompt_removes_the_over_split_boundary_sentence` assertion against the untouched Parent prompt. Ruff checks passed. Unrelated local changes were preserved.

Rollback: restore the backed-up Compose overlay and .env.v2, restore the backed-up source files (remove the new openrouter.py if needed), then recreate only v2-cdecr-executor using the existing full Compose file chain and .env.v2. This returns the original credential configuration as well; validate embedding access before resuming work.

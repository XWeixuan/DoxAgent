# SNOW O4 deployment recovery — 2026-10-19

## Failure evidence

At 07:49 CST, original run `init-snow-b14da3f619f84766995e3ec0a6862928` was FAILED / O4_CONFIGURE, seq 1246. The only failed node was `o4.configure`, with `Site Access is enabled but the worker token is unavailable`. D3 had completed. The initializer's environment pointed to `/run/secrets/site_access_worker_token`, but its actual mounts did not contain that secret. Current main Compose already declares it: the previously running deployment had not retained the earlier configuration repair.

Guardian incident `repair-b9e9ba78451b43c29b89a7757ffc34c8` reached HUMAN_REQUIRED after three infrastructure retries of the same round. Logs consistently show `workspace routing discovery unauthorized (401)`. The deployed Guardian lacked `DOXAGENT_INITIALIZATION_REPAIR_EXTERNAL_AUTH_SOURCE`. Its image and configured repair Agent image contained external-auth call sites but omitted `initialization_repair/auth.py`. Enabling the environment alone would therefore leave a missing-module error. No maintenance executor or maintenance Control operation had run.

## Repair

Persisted initializer `site_access_worker_token` and Guardian external-auth source in the canonical `/var/lib/doxagent/releases/compose.current.json`. Recreated only initializer and Guardian after confirming no active initialization lease. Initializer image, command, existing environment, memory/swap/PID limits and cgroup parent were preserved. Site Access authenticated resolve returned HTTP 200 without exposing credentials.

Built two minimal derived Guardian/Agent images from their exact running/configured parent image IDs, adding only the existing main-branch `auth.py`. File SHA-256: `71ccceeb6445e43d9ded7593caaf8e487d61ac0bd756c3e9f3237266b5c88127`. The derived images retain their parent source revision and additionally carry an explicit auth-file patch hash label; they are deployment overlays, not a claim of a full main-branch image build. Canonical release and Guardian environment now reference those images. Initializer and normal Worker images were not replaced. No Site Access/browser service was restarted.

Guardian image: `doxagent-initialization-repair-guardian:snow-auth-20261019` / `sha256:1a7d0df120318e8e46dd386fcbf77e075b65a414227a50aa533602d9459c19bd`.
Agent image: `doxagent-initialization-repair-agent:snow-auth-20261019` / `sha256:560895d76b4b1be350ff805de4c74383df71e570f1fe3c0dbe02bc4409cb55c6`.

Both image auth imports passed. An isolated Agent image used an access-only snapshot from the existing login owner: actual SDK external login, account read and models read passed (7 models). It received no refresh token, started no repair round or business task, and its temporary snapshots were removed afterward. No fresh autonomous model repair turn was triggered. The old failed incident CODEX_HOME is retained for evidence and is not resumed.

Added operator recovery evidence and released the terminal failed Guardian round through `RepairRepository.release`. Submitted formal `RESUME_INITIALIZATION`, operation `b5daeb9eaee446068de47044b173da5a`, actor/key `operator:snow-o4-site-token-recovery-20261019` / `snow-o4-site-token-recovery-20261019`. Operation SUCCEEDED / INITIALIZATION_RESUMED. No direct node-table changes, new initialization, budget reset, reinitialize or trade-mode change.

## Acceptance

At 07:56 CST, original O4 node RUNNING, generation 2 / ordinal 1; actual Worker `af3bfc52b208efd924131d85361773da3c835cf180869a800afb28f3f8b296a6` had thread `01a15170-71ef-7ec2-9086-4d7847af87e5`, execution_phase RUNNING, no error and current session writes. Initialization lease renewed; frontend revision 5 correctly showed INITIALIZING with retry disabled. Guardian incident CANCELLED / RECORD; no maintenance execution overlapped the operator recovery.

Original cutoff `2026-10-18T15:54:52.25109Z` and all 549 successful node payload SHA-256 values were unchanged. CEG remained PAPER_TRADING; other completed initialization states were unchanged. Recovery verified actual O4 execution, not whole-run completion.

Regression: `test_cohr_initialization_recovery.py`, `test_initialization_repair_guardian.py`, `test_initialization_repair_agent.py`: 17 passed. No new source-code change was required; canonical source fixes are already on main. Production overlay reconciliation and missing-file deployment were the material changes.

Evidence: local `eval/snow_o4_recovery_20261019/`; remote `/var/lib/doxagent/snow-o4-recovery-20261019/` contains before/success-node hashes, original inspect records (root-private), canonical-manifest backup, frozen deployment overlay, auth patch/hash/build receipt, SDK proof and post-resume verification. Existing stopped unrelated proof containers and other chats' deployment/source changes were left intact.

## Completion verified at 08:45 CST

The original run completed at 2026-10-19 00:05:42 UTC (08:05:42 CST), SUCCEEDED / VERIFY_READY, seq 1262. Original cutoff and all 549 pre-recovery successful node hashes remain unchanged; no failed nodes remain. Active revision is init-snow-b14da3f619f84766995e3ec0a6862928-activation; both bus and runtime ACK that revision with fresh heartbeats. Control revision 6 is RUNNING / PAPER_TRADING, analysis_allowed=true and initialization_incomplete=false. Frontend revision 6 agrees, effective mode AVAILABLE / PAPER_TRADING. No initialization lease or active initialization Worker job remains, consistent with completion. Existing pending crawler needs are retained in the published monitoring configuration and are not classified as an initialization failure. Completion evidence: eval/snow_o4_recovery_20261019/completion.json and completion-activation.json. CEG remains PAPER_TRADING. Long-term hourly monitoring remains active.

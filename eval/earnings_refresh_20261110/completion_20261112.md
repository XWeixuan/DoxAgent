# Earnings refresh completion

All four ticker refreshes completed and were formally applied to production. Research coverage remains explicitly PARTIAL; execution success is not a claim of exhaustive coverage.

| Ticker | D3 version | Policies | Remaining coverage gaps |
|---|---:|---:|---:|
| IONQ | 122 | 140 | 34 |
| COHR | 107 | 96 | 25 |
| CEG | 91 | 52 | 32 |
| RKLB | 164 | 102 | 27 |

IONQ/COHR were accepted and activated before the second batch began. Their original activation receipts are recorded in this chat and remote ops records. Current production D1/D2 references were checked again at closure and still point to the refreshed runs; subsequent normal maintenance revisions were preserved.

CEG original run `init-ceg-8ef7f16e72dd45b999a883af90f39499` succeeded at2026-11-12T00:22:50.352078Z. Activation `init-ceg-79a9a0a588554fd0ae93570309659489` succeeded with prepare/commit/bus.ready/runtime.ready and actual bus/runtime revision acknowledgements. It retained the current Eventv26 and original monitoring configuration. Export22 files verified; tarSHA256 `627783d8b8fb07d8e40996f3fb6cfbe05bcc17d122474ccb91620b604b758484` matched locally/remotely.

RKLB original run `init-rklb-52bc665afa8e43b1a38edd1da824e860` succeeded at2026-11-12T03:01:47.542786Z. Activation `init-rklb-c02e76fb902e4301a329fbb02213062b` passed the same four nodes, active references and actual acknowledgements. Current Eventv56 and monitoring configuration were retained. Export23 files verified; tarSHA256 `53e41b2fc6a385cb28bec13c9507036a3eea3f853b0298b62ce78918dfdd0b5c` matched locally/remotely.

CEG had SDK deadline failures and partial final delivery, including six missing/conflicting supplemental Results. Its32 gaps remain recorded; missing results were not labelled zero-Policy findings. RKLB retains27 evidence/business-boundary gaps despite no final task errors. Both publish nonempty, host-validated Policy sets. CEG/RKLB completion-audit.json remote records preserve errors and actual task model counts; excluded CDECR/O2/O4/activation/config nodes were absent from research execution.

No successful research was fully rerun. Original runs/cutoffs/results and failure audits were retained. Shared Worker/services were not restarted in final acceptance. Dedicated containers naturally exited and remain preserved. CDECR, Event Library rebuilding and message-source configuration were excluded throughout; formal activation only replaced D1/D2/D3 references and retained the then-current Event/configuration.

The earlier operational EMLINK cache rotation recovered snapshots; the shared Worker permanent fallback was observed missing in a later release. This completion does not claim that infrastructure regression was permanently repaired. See snapshot_recovery_20261112.md.

Acceptance evidence: final_pair_acceptance.jsonl, ceg-acceptance-final.json, rklb-acceptance-final.json, both export/manifest.json directories and remote per-ticker acceptance-final.json. The historical primary report was absent locally and was not reconstructed as an original record. This document is the current completion record. The90-minute heartbeat is removed after verified completion.

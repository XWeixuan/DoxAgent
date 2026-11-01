# INTC earnings reinitialization — 2026-11-01

- User authorized remote formal D1/D2/D3 refresh only; preserve CDECR, Event Library and message-source configuration. No activation in this research plan.
- Run: `init-intc-f656561082114d889b85001c4c6d8c84`; fixed cutoff `2026-11-01T00:15:45.817028Z`.
- Container: `doxagent-intc-refresh-20261101`; immutable production initialization image `sha256:078285679b8f5503bd78257d8386c9ae0547ab0035ff720a05122a6f30c3cc97`. 2 CPU/4 GiB, actual production Worker; D2/D3 concurrency4/startup3.
- Control DB `/data/ops/intc-refresh-20261101/control.sqlite3`; research DB `/data/research/research.sqlite3`.
- Existing Event Library v53 pinned at `/data/runtime/runtime_artifacts/runtime-maintain-005abc0ed4fe145aaa464b60/events`; reference SHA256 `a95df6f46bb1a1fa72133b98b7b5f4f118912be6f437f1d0d8b8ac23373aaad4` verified.
- Explicit plan only d1→d2→d3. Existing O2 artifact reference is replayed, never executed. D1 brief requires actual release-date verification.
- Startup verified D1 RUNNING; D2/D3 PENDING; no error. No other active production initialization at preflight.
- Production source git baseline `b36e59ba90b161d6393ab0a6fe32808c80f22247`. No production source/image edits or service restarts.
- Active upstream D1/D2 remain `intc-v21-20261011-01-*`, D3 v174. Full active revision and monitoring references saved in `eval/intc_reinitialize_20261101/submitted.json` and remote equivalent.
- Automation `intc-d1-d2-d3` every90 minutes: preserve successful checkpoints, diagnose actual Worker errors/blocks, resume same run, notify only faults/completion. Download and verify final artifacts before deleting automation.
- Operator scripts in `eval/intc_reinitialize_20261101/`; status.py must execute with dedicated DB environment in dedicated container, or explicitly open that DB after container exit.

## 2026-11-01 09:49 CST — first 90-minute check
D1 SUCCEEDED/publish succeeded; D2 O0八节点成功，O1五Shell继续推进，D3 PENDING。实际Worker 31 succeeded/4 running/0 failed；D2并发4，实际请求模型按节点分配（sol/medium、network sol/high、候选/Review luna/max）。容器running/exit0/OOM false，日志无错误，Event v53 reference SHA与启动一致。未重启、未重复提交、未运行排除步骤；继续原run。

## 2026-11-01 11:16 CST — 90-minute check
D1/D2 SUCCEEDED，D2五Shell的25个O1请求均成功；D3 Discovery7/Planning6成功，Build10 succeeded/4 running（实际luna/max），全部Worker累计63 succeeded、4 running、0 failed。容器running，无OOM和日志错误；固定Event hash保持。控制层7个Build节点running含调度等待，不代表7个实际Worker并发；真实并发4。正常推进，不重启不重复提交；status_1116.json保留。

## 2026-11-01 12:43 CST — 90-minute check
D1/D2成功保持，D3 Build累计53 succeeded/3 running，真实luna/max；全部Worker106 succeeded、3 running、0 failed。容器running无OOM/错误日志，Event固定hash校验通过。原run正常推进，无重启或恢复重派；status_1243.json保留。

## 2026-11-01 14:12 CST — 90-minute check
D3已完成首轮Integration12请求并进入正式补充研究；Build累计60 succeeded/4 running（luna/max），全部Worker125 succeeded/4 running/0 failed。D1/D2成功保持，容器running，无OOM/错误日志，固定Event hash校验通过。正常推进，保留全部节点，不重启或重派。status_1412.json保留。

## 2026-11-01 15:42 CST — 90-minute check
补充Build累计99 succeeded/1 running，Integration12 succeeded保持；全部Worker164 succeeded/1 running/0 failed。最后一个S0005补研节点真实luna/max执行中，D1/D2成功保持。容器running无OOM/日志错误，Event hash校验通过。原run正常推进，无重启或重派。status_1542.json保留。

## 2026-11-01 17:08 CST — 90-minute check
D3 Build100项成功，Integration27成功，累计180个真实Worker请求全部succeeded，无失败；控制层final审查从final:6推进至final:7，尚未完成，不将当前零活跃Job误判为结束。容器running无OOM/错误，固定Event hash校验通过，原run持续推进。status_1708.json保留。

## 2026-11-01 18:34 CST — completed, verified and downloaded
Run SUCCEEDED at 09:40:52 UTC; container exited0 at 09:40:53, no OOM. Duration from submission approximately9h25m06s. D1 published and C5 explicitly incorporates actual October29 after-market Q3 release; D2 published/COMPLETE, 5Shell/8Unit. D3 published candidate v175, 106 policies, STAGED/PARTIAL. All185 actual Worker requests succeeded: D1 7, D2 33, Discovery7, Planning6, Build100, Integration32; no failed jobs, recovery replays or full reruns.
Five recorded missing categories: Foundry designer/OSAT research result; advanced external design competitive economics/delivery supplement; incomplete enterprise cloud/endpoint final edit; remaining research gaps; one unresearched late lead. These are retained as coverage limits, not concealed by SDK success. D3 is not complete coverage.
Pinned Event v53 hash unchanged; monitoring_configuration exactly equals startup reference. Active D1/D2 remain previous run, no activation in this plan; current research completion does not mean production cutover. No CDECR/O2/O4/configuration nodes executed. No production service restart/source-image change for this refresh.
Downloaded results_final.tar.gz and 23 individual files verified against manifest SHA256; archive SHA256734fdbc79428aa3e5cf02bb112fa7d57dbee8af249038628d2f4796f0113b4e5. Includes D1 reports/bundle, D2/seeds, D3 canonical/projection/coverage/origins/markdown/staged receipt and compact control evidence. verification.json and worker_final.json retained. Initial Windows verification hit implicit GBK decoding; explicit UTF-8 fixed verification, no model rerun. Dedicated container already stopped normally. Heartbeat removed after acceptance.

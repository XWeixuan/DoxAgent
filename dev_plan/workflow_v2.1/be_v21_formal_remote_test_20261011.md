# BE 正式 v2.1 远端初始化（2026-10-11）

- 正式D1→D2→D3；跳过CDECR/O2/O4，生产v2.0不迁移。
- prefix `be-v21-20261011-01`；截止 `2026-10-11T15:54:22.932336+00:00`（23:54 CST）。
- 代码 `b20c4aca`，独立检出 `/home/ubuntu/doxagent-be-v21-20261011`；生产HEAD仍 `b0f1caf50060b34483f13020a9d173ef57933afa`。
- 独立SQLite `/data/be-v21-test/research.sqlite3`（生产在线backup，不覆盖原库）、published `/data/be-v21-test/published`、Worker `doxagent-be-v21-worker`、JobStore `/data/be-v21-test/workspaces/worker-jobs.sqlite3`。
- 新镜像 `doxagent-v2:be-v21-20261011` 包含最新/app/src、prompts、scripts；Data MCP健康检查通过。空provider不写入env，包含此前workspace GET重试修复。
- Event Library BE v1，只读；发布2026-09-21T16:34:30.976275+00:00，SHA256 `857e3d937a7c3ba921729c4f53bd1ac2c59f9fc4698565f80a4667b945b35a2a`。
- 模型逐节点对齐当前本地node_models.py：核心Sol6.1/medium；C4network Sol6.1/high；C4pre/Future/formal、O0候选审查、D3Build Luna/max。D2/D3并发4，startup3，launch wave3、间隔2秒，timeout1800、attempts2。
- 已启动正式D1容器 `doxagent-be-v21-01-d1`，ID `1a4b2469ddccdc66fff2feb386cf4cbbeddbc6af4a7b20ea19ac9c0c96676657`；首次检查处于输入准备、暂无Worker Job，后续核验实际模型请求。
- O0前15分钟轮询；D1发布后start_stage.py d2-o0，落盘停点实物核验3–6 shells，合格才同run启动d2并改30分钟；超范围停止汇报。D2发布后启动d3。错误同run断点恢复，不重做成功节点。
- 复用已验证O0门控；相关门控+传输测试11 passed，Ruff通过。启动路径拒绝同名容器重复创建。
- heartbeat `be-v2-1` ACTIVE，每15分钟，正常无变化静默。生产仓库/服务未由本线程修改。

## 2026-10-12 00:10 CST — scheduled check
D1正常运行：program_collection、C4 pre-scan已完成；C1/C3进行中。实际Worker模型核验：C4pre gpt-6-luna/max，C1/C3 gpt-6.1-sol/medium，符合当前配置。容器无新错误，未重启或重复派发。继续15分钟检查，D1发布后再启动O0。

## 2026-10-12 00:25 CST — scheduled check
D1继续正常推进：C1/C3/C5与C4pre已成功，agent_normalization已完成，C4F Future Nodes进行中。实际模型符合配置（核心Sol6.1/medium，C4F Luna/max），无失败Job及容器错误；未重启/重复派发。保持15分钟检查。

## 2026-10-12 00:40 CST — scheduled check
D1状态延续：C4F Future Nodes仍在原Job内运行（Luna/max），C1/C3/C5/C4pre成功保留。容器和Worker正常，无新增失败或错误；未重启或重复派发。继续15分钟检查。

## 2026-10-12 00:55 CST — scheduled check
C4F Future Nodes与C4E formal已成功，C4E network正在原run内运行。实际模型：Future/formal Luna/max，network Sol6.1/high，符合配置；全部其他D1成功节点保留，无失败Job或新错误。未重启/重复派发，继续15分钟检查，待D1正式published后启动O0。

## 2026-10-12 01:10 CST — scheduled check
D1 C4E network仍在原Job内运行，实际Sol6.1/high；其余6研究节点成功保留。容器和Worker正常，无新错误/失败Job。未重启或重复派发；继续15分钟检查，尚未启动O0。

## 2026-10-12 01:25 CST — D1 published，启动O0
D1已published，容器退出0，全部7研究节点成功，无失败Job，模型逐节点匹配配置。按既定流程启动正式d2-o0容器，仅O0，在final_shell_seeds持久化后停点，未放行O1。仍保持15分钟检查，待实物核验shell数量。

## 2026-10-12 01:40 CST — O0数量放行
O0全部8请求成功、退出0；实物final_shell_seeds为6 shells，SHA256 64b17f82320f11805935b4bbf0f86661eed0ede102c7c79a60117e799a7f30a1。名称：美国大型负荷现场电力市场；燃料电池产品与机队经营；韩国现场燃料电池市场；公司现金与资本配置；电解制氢产品业务；工商业与区域分布式电力市场。符合3–6范围，原O0不重做。已启动正式d2容器e13e179e896591928677b3bec322362337b3284b157f3b85a5aa6a12e77e6dce，从同一D2 run继续O1。heartbeat已改每30分钟；实际O0模型匹配配置，无失败Job。

## 2026-10-12 02:11 CST — scheduled check
D2 O1正常四并发：STATE4成功、OPEN_DISCOVERY4成功、REALIZATION3成功1运行、GAPS1成功2运行、FINALIZATION1运行；实际请求全部Sol6.1/medium，无失败Job/容器异常。燃料电池产品与机队经营shell的OPEN_DISCOVERY产生5种验收告警（stage及shell记录共10条）：selection_not_bound_at_freeze、upstream_fallback、scan_unit_not_researched、discovery_unavailable、scan_recovered_after_turn；保留待最终质量说明，不因告警重做成功任务。继续30分钟检查。

## 2026-10-12 02:41 CST — scheduled check
D2继续正常：STATE/OPEN_DISCOVERY各6成功，REALIZATION5成功1运行，GAPS/FINALIZATION各5成功。所有O1实际模型Sol6.1/medium，无失败Job/新容器错误；既有OPEN_DISCOVERY告警未扩大。不启动D3直到D2正式published，未重启/重复派发；继续30分钟检查。

## 2026-10-12 03:11 CST — D2发布，启动D3
D2已published/PARTIAL，容器退出0，6 shells全部5个O1阶段共30 Worker请求成功，模型全部Sol6.1/medium。PARTIAL保留此前燃料电池产品与机队经营OPEN_DISCOVERY的5类告警（stage+shell共10条），无执行失败，不重做成功任务。已启动同prefix正式D3 v2.1，独立测试环境及原并发/模型配置，保持30分钟检查。

## 2026-10-12 03:41 CST — scheduled check
D3 Discovery全部8请求成功（6普通+2 OPEN），Planning2成功1运行；实际模型均gpt-6.1-sol/medium。原D3容器正常，无失败Job或新错误，未重启/重复派发。D1/D2发布状态保持，继续30分钟检查。

## 2026-10-12 04:11 CST — scheduled check
D3 Planning全部7请求成功，Build已3成功、4运行，实际Build gpt-6-luna/max，Discovery/Planning Sol6.1/medium，符合配置与四并发。无失败Job/新容器错误，未重启或重复派发，继续30分钟检查。

## 2026-10-12 04:41 CST — scheduled check
D3 Build13成功（+10）、4运行，实际Luna/max，四并发正常。Discovery8及Planning7成功保留，无失败Job/新增容器错误。未重启或重复派发；继续30分钟检查。

## 2026-10-12 05:11 CST — scheduled check
D3 Build23成功（+10）、4运行，实际gpt-6-luna/max，四并发正常。Discovery8与Planning7成功保留，无失败Job/新增容器错误。未重启或重复派发，继续30分钟检查。

## 用户追加授权 — BE结束后接续RKLB
BE正式流程结束并核验产物后，接续远端RKLB新D1→D2→D3 v2.1。启动前重新核对最新本地模型/并发与代码，使用独立远端源码/数据库/Worker，生产保持v2.0，跳过CDECR/O2/O4。RKLB O0前15分钟检查，实物3–6 shells才同run放行并改30分钟，超范围停止汇报。故障定点修复，保留成功节点。既有heartbeat已更新为顺序接续，BE进行中不提前启动RKLB。

## 2026-10-12 05:41 CST — scheduled check
D3 Build34成功（+11）、4运行，实际Luna/max，四并发正常。无失败Job/新增容器错误，未重启或重复派发。BE尚在运行，按顺序要求未启动RKLB；继续30分钟检查。

## 2026-10-12 06:11 CST — scheduled check
D3 Build44成功（+10）、4运行，实际Luna/max，四并发正常，无失败Job/新增容器错误。未重启或重复派发；BE未结束，RKLB尚未启动。继续30分钟检查。

## 2026-10-12 06:41 CST — scheduled check
D3 Build52成功（+8）、4运行，实际Luna/max，四并发正常。无失败Job/新增容器错误，未重启或重复派发；BE仍在运行，RKLB未提前启动。继续30分钟检查。

## 2026-10-12 07:11 CST — scheduled check
D3 Build60成功（+8）、3运行，实际Luna/max；原容器/Worker正常，无失败Job或新增错误。未重启/重复派发，BE尚未结束，RKLB未启动。继续30分钟检查。

## 2026-10-12 07:41 CST — scheduled check
D3 Build66成功（+6）、1运行，实际Luna/max；原容器/Worker正常，无失败Job或新增错误。未重启/重复派发；BE未结束，RKLB未启动。继续30分钟检查。

## 2026-10-12 08:11 CST — scheduled check
D3初始Build67请求全部成功，已进入Integration（3成功、1运行）。实际Build Luna/max、Integration Sol6.1/medium，符合配置。无失败Job/新增容器错误，未重启或重复派发。BE尚未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 08:41 CST — scheduled check
D3 Integration9成功（+6）、1运行，实际Sol6.1/medium；初始Build67成功保留。原容器/Worker正常，无失败Job/新增错误，未重启或重复派发。BE未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 09:11 CST — scheduled check
D3主Integration14请求成功，流程进入补研Build：累计70成功（初始67+补研3）、4运行，实际Luna/max；Integration Sol6.1/medium。原容器/Worker正常，无失败Job/新增错误，未重启或重复派发。BE未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 09:41 CST — scheduled check
D3补研Build累计80成功（+10）、4运行，实际Luna/max，四并发正常；Integration14成功保留。无失败Job/新增容器错误，未重启或重复派发。BE未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 10:11 CST — scheduled check
D3补研Build累计89成功（+9）、4运行，实际Luna/max，四并发正常；Integration14成功保留。无失败Job/新增容器错误，未重启或重复派发。BE未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 10:41 CST — scheduled check
D3处于SUPPLEMENT，Build累计95成功、4运行，模型均Luna/max；Discovery8、Planning7、Integration14成功保留。新增1次CODEX_TURN_TIMEOUT：supplement:S0004:26首attempt超过1800秒；持久任务已由原流程自动进入attempt_count=2/RUNNING，未人工重启或重复派发。四并发正常，暂无需代码修复，后续核验第二次attempt结果。BE未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 11:11 CST — scheduled check
D3 SUPPLEMENT Build累计104成功（+9）、4运行，实际Luna/max；Discovery8、Planning7、Integration14成功保留。上次超时任务supplement:S0004:26已在第二attempt完成，持久状态COMPLETED/error为空；历史1次failed Job保留，无新增失败或容器错误。原容器四并发继续，无人工重启或重复派发。BE未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 11:41 CST — scheduled check
D3仍处于SUPPLEMENT，Build累计114成功（+10）、3运行，实际Luna/max；Discovery8、Planning7、Integration14成功保留。历史1次超时对应任务第二attempt已COMPLETED，无新增失败或容器日志错误。原容器正常继续，未改并发配置、未重启或重复派发。BE未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 12:11 CST — scheduled check
D3 SUPPLEMENT Build累计118成功（+4）、1运行，实际Luna/max；Discovery8、Planning7、Integration14成功保留。历史超时任务第二attempt已COMPLETED，无新增失败或容器日志错误。原容器继续运行，未调整四并发配置、未人工重启或重复派发。BE未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 12:41 CST — scheduled check
D3仍处于SUPPLEMENT，Build累计119成功（+1）、1运行，实际Luna/max；Discovery8、Planning7、Integration14成功保留。新增历史失败Job为supplement:S0006:45首attempt的CODEX_TURN_TIMEOUT，原流程第二attempt已完成，持久状态COMPLETED/error为空；连同S0004:26共2次历史超时均已自动恢复。无容器错误，未人工重启或重复派发，四并发配置保持。BE未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 13:11 CST — scheduled check
D3 Build累计120成功，已完成补研并进入FINALIZE；Integration累计17成功、1运行，实际Sol6.1/medium，Build均Luna/max。Discovery8、Planning7成功保留，两次历史超时均已由第二attempt完成，无新增失败或容器日志错误。原run继续收尾，未重启或重复派发。BE尚未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 13:41 CST — scheduled check
D3仍处于FINALIZE，Integration累计20成功（+3）、1运行，实际Sol6.1/medium；Build120成功、Discovery8、Planning7成功保留。两次历史超时均已自动恢复，无新增失败或容器日志错误。原run继续收尾，未重启或重复派发。BE尚未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 14:11 CST — scheduled check
D3仍处于FINALIZE，Integration累计21成功（+1）、1运行，实际Sol6.1/medium；Build120成功、Discovery8、Planning7成功保留。两次历史超时任务已COMPLETED，无新增失败或容器日志错误。原run继续收尾，未重启或重复派发。BE尚未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 14:41 CST — scheduled check
D3仍处于FINALIZE，Integration累计25成功（+4）、1运行，实际Sol6.1/medium；Build120成功、Discovery8、Planning7成功保留。两次历史超时任务已COMPLETED，无新增失败或容器日志错误。原run继续收尾，未重启或重复派发。BE尚未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 15:11 CST — scheduled check
D3仍处于FINALIZE，Integration累计30成功（+5）、1运行，实际Sol6.1/medium；Build120成功、Discovery8、Planning7成功保留。新增1次Integration失败来自provider serverOverloaded（model at capacity），任务integration:final:4已自动第二attempt完成，状态COMPLETED/error为空，未切换模型。两次历史Build超时亦已恢复。无新增容器日志错误，未人工重启或重复派发。BE尚未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 15:41 CST — scheduled check
D3仍处于FINALIZE，Integration累计33成功（+3）、1运行，实际Sol6.1/medium；Build120成功、Discovery8、Planning7成功保留。历史2次Build超时与1次Integration容量错误对应任务均已COMPLETED，无新增失败或容器日志错误。原run继续收尾，未重启或重复派发。BE尚未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 16:11 CST — scheduled check
D3仍处于FINALIZE，Integration累计35成功（+2）、1运行，实际Sol6.1/medium；Build120成功、Discovery8、Planning7成功保留。历史2次Build超时与1次Integration容量错误对应任务均已COMPLETED，无新增失败或容器日志错误。原run继续收尾，未重启或重复派发。BE尚未结束，RKLB未启动；继续30分钟检查。

## 2026-10-12 16:41 CST — completed and verified
BE四阶段容器均exit0。D1耗时1:18:41，D2 O0耗时0:14:01、O1后续1:16:56，D3耗时13:26:05。D1 published；D2 published/PARTIAL，保留5类OPEN_DISCOVERY告警（阶段/对应shell重复记录）；D3 STAGED/PARTIAL v16，124 policies，缺口为remaining research gaps及late/d4401232db937d87bd32e8a5.jsonl的L1/L3未研究lead，不为清空缺口重跑。
Worker结果：D3 Discovery8、Planning7、Build120、Integration37成功；2次Build timeout、1次Integration serverOverloaded均第二attempt完成，无未恢复失败任务。模型实际值符合节点配置。
D3 document3.json 1,111,182 bytes SHA256 3fd35a30f0a0288525a35635c993e5d8d8fa8de054ca39bd4171297f0452f67a；projection 1,077,958 bytes SHA256 4df4ff6389a8918896a420f6fc53ece8a2e96f1d43f0dc97dbe2313e2a8aa1eb；coverage 110,137 bytes SHA256 c85432805c5a5722b066604521ec5c205a466c66b85d260310b7a258c9924b39；Markdown 906,152 bytes SHA256 9ad1fd74af79f479cd9fcc60126753a99bb6320dc3ad32c5c2f897651ff1bed9。四文件与handoff一致。D2 JSON 1,013,469 bytes SHA256 89dbf7d018ced1f7e22a2dfd1dfc9a0794980d8bfd5ddda955137492b5ec22c9。已下载至artifacts/be-v21-20261011-01。
生产git仍b0f1caf50060b34483f13020a9d173ef57933afa，生产init/Worker容器ID保持原值，未迁移v2.1。本次观察到生产init由其他操作于07:25 UTC重启，不能宣称全局无重启。按授权随后接续独立RKLB。

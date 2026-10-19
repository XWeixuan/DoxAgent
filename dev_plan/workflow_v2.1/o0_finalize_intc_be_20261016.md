# INTC / BE O0 finalized 并行执行（2026-10-16）

用户授权：BE重跑Discovery/Synthesis，INTC沿用成功结果，两者并行推进到O0 finalized；每15分钟检查，不高频轮询。

已启动正式工作流隔离入口 `eval/o0_formal_once_20261015/continue_o0.py`。2026-10-16 16:55 CST，实际Python PID 19484（启动wrapper PID 5556）。两个ticker由asyncio.gather并行执行，共享本地真实Worker，端口18792，模型路由及D2并发读取当前配置；每节点最多3次尝试。源代码未修改。

- INTC：原run `intc-o0-formal-local-20261015-01`，恢复既有Discovery/Synthesis，不重做。
- BE：新run `be-o0-formal-local-20261016-02`，重跑三个Discovery分支和Synthesis；三个Discovery artifact不齐时阻断Synthesis，不能带上次C1缺口继续。
- 两者继续正式Domain Review和Finalization，在_construct_shells完成及final_shell_seeds/checkpoint落盘后停止，不进入O1或发布。

为保持比较一致使用上次冻结的D1/Event资产和as_of，未重新获取上游；Narrative依旧UNAVAILABLE，不冒充输入已恢复。当前prompt SHA256记录在continue_20261016/assets.json。

隔离入口为历史Capsule CLEANING滞留增加40秒清理时限，超时只清理自身capsule identity/进程，记录CLEANUP_TIMEOUT；不改全局Worker源码、不伪造模型成功。结束时须核验新请求与checkpoint、Review完整性、final_shell_seeds实物及无O1证据，任何历史重试或清理异常均如实记录。

本轮日志：continue.stdout.log / continue.stderr.log；状态：continue_20261016/started.json与results.json；DB/workspace沿用eval/o0_formal_once_20261015内隔离实例。15分钟heartbeat `intc-be-o0-finalized`已启用，正常无变化静默，完成后删除。

当前状态：已确认两者START日志且无启动stderr；尚未完成，不宣称finalized。

## 17:10 CST 首次检查：BE C1 fallback 不计为成功

BE新run的C1 Worker在RUN_TURN失败（Codex process closed stdout），正式acceptance自动生成空候选CANDIDATE_UNAVAILABLE，NodeAttempt标记succeeded并附upstream_fallback/worker_delivery_error。入口只查artifact存在不足以阻止此情况，BE Synthesis已启动。不能认定满足用户要求；最终必须真实补跑C1并重算其依赖的Synthesis/Review/Finalization，保留已成功C3/C5和全部原证据。当前协调器仍运行，先保留INTC正常研究；禁止以当前BE finalized结果验收。INTC C5 Review成功、C1 Review仍RUNNING、C3 Review显示CLEANING；本轮40秒清理包装是否有效尚未证明，后续按真实回执与进程检查，不重复模型。

## 17:25 CST 定点恢复

已确认旧runner PID19484/wrapper5556、端口18792监听及capsule_child进程均不存在，退出原因尚未确认，不能把残留Job RUNNING当作实际研究中。三条活动Job无完整Worker receipt；INTC C1已写completion.json但无SDK完成事件，保留并通过正式runner验收文件，不伪造SDK成功。备份隔离两DB至continue_20261016/recovery_1725，原Job按已确认中断记录failed/LOCAL_RUNNER_INTERRUPTED，保留全部attempt/artifact。

仅使BE空C1及其依赖阶段checkpoint失效，C3/C5保留。隔离入口禁用acceptance fallback，格式/传输错误由正常节点重试处理；INTC C1复用原attempt的权威文件交由正式schema验收，不发新模型请求。使用Win32_Process.Create启动续跑，脱离工具shell的父进程生命周期（PID4652 wrapper；实际PID见started.json），两个ticker继续并行。日志改为进程内写continue.resume.log，原日志保留。此启动方式是否解决进程提前退出须下一轮核验。

## 用户“继续”后的恢复

Windows Application日志17:28:55有codex.exe异常0xc0000409；BE C1 stderr明确memory allocation of 1048576 bytes failed，同期Clash及node_repl也崩溃。旧runner已不存在，不归因于单个业务prompt。INTC C1权威文件已正式验收并形成checkpoint，C5 Review保留；INTC C3第二attempt未完成。BE C3/C5保留，C1第二attempt未产出可消费结果，fallback禁用后正确阻断Synthesis。

修正隔离入口：native acceptance无可用结果抛ValueError，原max_attempts只捕获Document2ExecutionError，因此增加针对No consumable的有限重试，保留各次attempt。已将无进程的残留Job记录中断后同run续跑，仅缺失节点执行。未改系统内存/分页文件、未关闭无关应用、未调整业务并发。下轮继续核验独立进程及模型结果。

## 17:40 CST 磁盘故障

BE C1及Synthesis已有非fallback业务文件通过正式schema验收（Worker交付含UTF-8解码异常，已保留warning），三个Review正在执行；INTC C3已有业务文件验收，Finalization执行中。新故障C盘可用约47MB，Worker SQLite报disk I/O error；不能宣称finalized。已将两隔离DB和当前两bundle备份D:/DoxAgentRecovery/o0_20261016_1740，未删除或清理无关数据。当前runner PID29752仍在，禁止重复启动。磁盘空间需要恢复；不进行未经授权的全盘清理。

## 17:55 CST 空间恢复后续跑

C盘可用5910913024 bytes（约5.5GiB），Worker DB quick_check=ok。旧runner/端口/capsule均不存在。INTC Finalization及BE三个Review无completion.json；将四条孤儿Job保留并标LOCAL_DISK_INTERRUPTED，原attempt标失败，仅缺失节点随正式同run恢复。成功Discovery/Synthesis和INTC三Review全部保留，未重跑。WMI独立启动--resume，实际pid见started.json，继续15分钟检查。磁盘清理非本线程执行。

## 18:26 CST 独立启动修复

此前终端会话再次消失。Windows临时任务首个python.exe启动返回3221225786（0xC000013A，控制台关闭/中断），并无新启动标记。改为系统Task Scheduler临时任务DoxAgent-O0-INTC-BE-20261016使用pythonw.exe无控制台执行，已确认Running、实际PID7512、真实capsule/MCP进程开始。完成后移除该临时任务。此路径隔离于工具终端生命周期，不修改模型/并发。

原四条10:12孤儿Job被原生恢复排队，已在尚未重派时标cancelled/SUPERSEDED_ORPHAN_ATTEMPT保留证据，避免与新attempt重复研究。只推进缺失INTC Finalization与BE Review。之前所有成功artifact保留；本轮空间约3.7GiB。

## 用户纠正：恢复原有本机Docker执行方式

用户明确不要远端，要求追溯旧本地无弹窗方案。已在eval/run_codex_document1_v2_smoke.py确认原真实测试明确使用real Docker worker；现有doxagent-codex-worker容器仍运行。此前另起Windows Worker/pythonw/TaskScheduler偏离已有本机Docker路径，导致下游SDK/MCP进程窗口和资源扰动；已停止仅本测试PID33640自有进程树，移除临时计划任务，保留全部Job/attempt/artifact，未停止其它容器或应用。

创建本机独立容器doxagent-o0-intc-be-local-20261016（镜像doxagent-v2:production，src/prompts只读挂载当前本地版本，隔离测试目录读写挂载）。正式入口continue_o0.py --resume在Linux容器内执行，内部Worker保持两ticker并行，不O1/发布。限额4 CPU/6GiB，Codex home仅本次独立D:/DoxAgentRecovery/o0_docker_home_20261016，复用已有本机Worker账户auth，未输出凭据、未写进仓库。未搬远端。

启动时间2026-10-16T10:35:43Z，容器PID1；已核验running与资源限制，Windows不再有本测试Python/Capsule进程。Windows残留活动Job记录OPERATOR_STOP_WINDOWS_POPUPS，保留成功checkpoint，Docker只补缺失阶段。之前日志中的MemoryError/HTTP500为旧Windows运行历史，不能混认新容器故障。后续每15分钟检查docker状态、continue.resume.log及checkpoint/产物，不再使用Windows后台启动链。

## 18:41 CST Docker运行时版本修复

新容器正常退出但模型请求全部服务端400拒绝：旧Worker auth/旧镜像SDK0.144.4不支持当前gpt-6模型；本地pyproject与SDK实际均锁定0.159.3。不是新研究业务结果。制作仅本次使用doxagent-o0-local:20261016镜像，将openai-codex对齐0.159.3；复制当前本机auth到本次独立D盘home，原Worker auth另存备份，未更改现有Worker账户或生产镜像。

入口追加确定性invalid_request/model not supported停止重试；Finalization要求C1/C3/C5真实Review齐全，防止Review失败后仍进入Finalization。原所有失败请求和产物保留，不重复成功研究。旧容器退出且保留，修复后只恢复缺失Review/Finalization。

## 18:56 CST 容器沙箱配置

SDK0.159.3与当前auth已能完成模型调用，但SDK回复明确bwrap namespace-permission导致不能读输入/写completion；Job succeeded不能冒充业务产物成功。检查sdk_runtime.py已有DOXAGENT_CODEX_CONTAINER_ISOLATION=true路径，在Docker内选择已有隔离运行沙箱。新独立容器补该环境变量，无需更改宿主/全局安全设置，src/prompts仍只读、仅隔离测试及本次home读写；原容器退出保留改名-bwrap。只恢复缺失节点，原成功checkpoint不重做。

## 19:11 CST 本机Docker I/O异常

本次docker inspect超过一分钟无响应（单个检查进程，未重复创建容器）；continue.resume.log出现容器内部Worker SQLite disk I/O error。宿主C盘约2.5GiB、D盘约12.2GiB，Windows只读Worker DB quick_check仍ok，不能简单归因于磁盘已满。最新BE三Review Job停留STARTING、无completion.json，未验收finalized。保留现场，不重启承载其它服务的Docker，不重派模型。待Docker后端恢复需先核验实际容器/Job及文件，再同run恢复；可考虑把本次SQLite/工作目录置于本机Docker原生volume避免Windows bind I/O，但未在后端无响应时贸然操作。

## 授权重启Docker及残留socket恢复

用户明确授权重启Docker Desktop并恢复断点。常规restart命令卡住后，停止Docker Desktop/backend，并仅终止docker-desktop专用WSL发行版（未关闭其它发行版）。启动暴露dockerInference与Secrets Engine engine.sock残留零字节ReparsePoint错误；已核验目录只含临时socket，将Docker/run与docker-secrets-engine目录改名保留，再新建空目录，未删除密钥、容器、卷或VHDX。结束卡住的旧restart客户端后单次启动成功，docker ps恢复，原服务自动恢复，api/web/worker健康检查通过。

为规避Windows bind目录SQLite I/O，将本次约109MB/12837文件打包经docker cp导入本机原生卷doxagent-o0-state-20261016；Codex home独立卷doxagent-o0-home-20261016（仅复制当前本机auth）。两DB通过quick_check并以Windows一致性SQLite backup覆盖，旧WAL/SHM保留改名。三条孤儿Review无业务文件/receipt，标DOCKER_BACKEND_INTERRUPTED保留记录。准备时误用identity列已按pragma改为record_key，未派模型。

新同名容器使用原生卷、SDK/CLI0.159.3、CONTAINER_ISOLATION=true、4CPU/6GiB，只恢复原run缺失节点；原bind容器保留改名-bindio。Windows任务目录现在是冻结备份，实时状态在Docker卷/app/eval/o0_formal_once_20261015内，完成后docker cp导出。独立临时传输容器已停止。未重跑成功Discovery/Synthesis/INTC Review。

## 19:56 CST 进展

Docker原生卷运行正常。INTC正式Finalization Worker succeeded，checkpoint completed_stages含o0，已实物4 Shell且无O1 attempt。BE C3/C5 Review已成功，C1 Review仍正常RUNNING，暂无新错误。旧日志disk I/O为迁移前历史，不是新容器故障。待BE C1和Finalization完成后统一导出核验与汇报。

## 最终验收（2026-10-16 20:11 CST）

两ticker均已O0 finalized并完成导出。最终稳定Docker运行2026-10-16T11:54:10Z—11:58:10Z，约4分钟；整轮此前包含Windows启动、内存/磁盘、Docker版本/沙箱及后端恢复，不能宣称整轮零错误。

| Ticker | 最终Shell | Unit | Finalization真实Worker | 未运行O1 |
|---|---:|---:|---|---|
| INTC | 4 | 7 | gpt-6.1-sol/medium，succeeded，19:56:12 CST | 是（0 attempts） |
| BE | 3 | 8 | gpt-6.1-sol/medium，succeeded，19:58:09 CST | 是（0 attempts） |

INTC：数据中心计算产品与平台（2 Unit）、客户端与边缘计算产品市场（1）、半导体制造与供给体系（3）、公司资本与股东经济权益（1）。

BE：现场发电市场与项目体系（4 Unit）、Energy Server制造与机队经济（3）、公司现金与资本结构（1）。

两者三Discovery分支、Synthesis、三Review全部为实际业务file来源，acceptance metadata逐条核验无upstream_fallback，候选/Review未含CANDIDATE_UNAVAILABLE或REVIEW_UNAVAILABLE。最终ShellFinalizationResultV21 schema通过，checkpoint completed_stages含o0、final_shell_seed_path实物存在。BE最后三Review真实Worker均gpt-6-luna/max succeeded。两DB quick_check=ok。

INTC SHA256：d7a0e6f70ed5a7acfe8f9e0f95f8a8e6c13dd197eca740783ccb05c8148a6ac4。
BE SHA256：e0b15ed5435c719f7c63e741af56eb18205dfeccbaea4c5d2ffa174f4e708e5b。

导出：eval/o0_formal_once_20261015/finalized_20261016/，含INTC/BE的final_shell_seeds.json、所有Discovery/Synthesis/Review产物、bundle.json、verification.json（真实acceptance metadata）、两一致性DB、results.json、complete-run.log。宿主旧目录仍保留迁移前证据，不覆盖。首次metadata定位只按文件名取hash未读到metadata，随后按完整relative_path重新验证并更新verification.json，全部来源file。

限制：Narrative上游UNAVAILABLE仍保留；历史Worker交付失败但业务文件验收成功的warning没有删除，INTC C1/C3 Review、BE C1/Synthesis的SDK交付不能冒称无错误。这些均有权威文件通过正式验收，最终阶段非fallback且正常成功。未运行O1、未发布D2、未修改远端生产。

本测试容器已自然退出exit0/非OOM；15分钟heartbeat intc-be-o0-finalized已删除。临时Windows计划任务已移除，无本测试Windows进程链；Docker原生卷和已停止容器保留供审计，不删除其它服务或数据。

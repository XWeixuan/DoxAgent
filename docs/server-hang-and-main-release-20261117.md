# main 发布与服务器假死排查（2026-11-17）

## 发布范围

本轮统一提交全部当时未提交的产品代码、测试、依赖及关联文档，包括另一份全局性能勘察文档。原始导出、运行缓存及凭据未混入提交。

应用先以 `2cc0f6bf2` 发布；终检又发现并行开发的热路径修正，已验证并纳入 `c2b0f6509753a5a48edd949bcb18714ff1539a31`，作为第二轮应用候选：已预热 ticker 不再等待其他 ticker 的冷加载；兼容 finalization 在提交锁外准备，并按实际保留正文去重。最终组合专项（进阶去重、Google、查询成本、资源保护）**31 passed**。随后 `486931723cc1b1241e1c3d139a178af1ffdc85cb` 补齐同 ticker 冷预热等待：完成参照加载后才标记 warmed；最终组合 **32 passed**，作为最终应用版本。

固定提交通过既有 storage_release 流程构建，RapidFuzz 3.14.6 进入正式依赖镜像；并非在现役容器临时安装或只复制源码。更新共享后端的 15 个角色和 Site Access，共 16 个角色。Web、Chrome Supervisor、Initialization Guardian 的自身源码与现役提交比较没有变化，保留其现役镜像。

用户追加允许中断任务后，采用正常停机及既有持久化恢复机制切换，没有直接改写任务状态。API／Scheduler 最后恢复。服务、源配置、数据库卷、业务账户和模型配置沿用现役值，回滚配置由发布框架保存。

第一轮验收：16 角色各 80 个变动文件，共 **1,280 次 Git blob SHA256 比较全部一致**，含其他先前开发的 Runtime/Event/Fact 缓存改动。22 个运行容器无 unhealthy，重启计数均为 0；有健康检查的角色均 healthy。83 个启用源中 82 个开启进阶去重，IBKR 唯一关闭；Google 现有订阅为 9/9 开启 require_relevance。新 Bus 启动后已产生跨 7 个 ticker 的 8 个自然 Stream。最终补发已完成：486931723 的16个角色再次通过各80个文件的摘要核验；全部22个运行容器无unhealthy，最新可用内存约17GiB，memory PSI avg10/60/300均为0，安全状态NORMAL。最终 Bus 重启后再次确认跨5个 ticker 的8个自然 Stream，源开关与 Google 9/9 配置仍正确。

最终后端镜像：`sha256:c271fe1fd01db1debc17fd8f29df3f1554da1e6ed116964a8b81a200f57799e3`。

最终 Site 镜像：`sha256:ce07decba52b17fcec6bf5bf11d89afc7d9549e23042e63f7924b49abacdd597`。

## 假死证据

SSH 最初握手超时；服务器在北京时间 22:52 重启后恢复。事故发生在本轮镜像构建之前，新进阶去重当时也尚未部署。

重启前 SAR 记录：

|北京时间|可用内存|文件缓存|1分钟负载|阻塞进程|CPU I/O等待|磁盘读|磁盘平均等待|
|---|---:|---:|---:|---:|---:|---:|---:|
|22:00|约9.83 GiB|约3.96 GiB|11.04|1|4.37%|约19.5 MiB/s|0.71 ms|
|22:10|约6.15 GiB|约3.34 GiB|54.33|1|1.71%|约16.2 MiB/s|0.92 ms|
|22:21:55|约2.28 GiB|约1.09 GiB|565.93|382|68.89%|约184 MiB/s|115.88 ms|
|22:36:06|约2.43 GiB|约1.05 GiB|449.63|289|83.06%|约196 MiB/s|191.83 ms|

22:21 内核后台扫描约34.6万页/秒、直接扫描约17.5万页/秒，说明内存回收极重。现有证据强烈支持：**内存压力压缩文件缓存，页面回收／重读导致磁盘排队及大量任务阻塞，进而表现为整机假死。**

Swap 虽接近满，但同期实际换入量很低，不能归结为持续大规模 swap-in，也不能只扩大 Swap 就称修好。磁盘当时仍有约19 GiB可用，没有 ENOSPC 证据。内核 journal 的上一轮记录止于11月13日，不能据当前 boot 的 OOM 计数推断事故当时没有 OOM。

重启后旧版本样本：Worker 匿名内存约9.1 GiB，Site Access约2.5 GiB，Chrome Supervisor约3.6 GiB；Worker 内有多份约200–350 MiB匿名内存的原生 Codex 进程。它们是主要内存贡献者，但这不是事故前逐进程记录，**尚不能指认哪个进程最先异常或证明单一内存泄漏**。

## 已发布的保护修复

既有安全控制器的主机低可用内存分支要求 `<1 GiB AND 真实压力`；本次约2.5 GiB时已发生严重文件页回收，这一分支存在介入过晚的盲区。另一个 aggregate 高水位分支在事故时是否触发，无法从保留状态完整复原。

提交 `1bfb08fa9` 将阈值改为 `max(1 GiB, min(3 GiB, aggregate memory_max / 10))`。仍要求 PSI／实际换页压力，仍保持15秒确认及原恢复滞回；30 GiB预算对应3 GiB，旧小机器不会一律硬套3 GiB。

复用原有空闲 Capsule 回收、预热与后台保护，不新增实时并发上限、常驻服务或数据库任务，不因 free 偏低或 Swap 存量高而限制正常工作。CRITICAL与既有在途策略不变。资源保护专项 **10 passed**，包含2.5 GiB可用、未触达 aggregate 高水位但已有真实PSI的场景，以及无压力不触发、恢复有滞回。

该宿主脚本已同步远端并重启原 systemd 安全服务；实际SHA256 `c2941a4ed948889a23360776bf47f0aab427d2a76d5bab01a81e97ea656e8719` 与提交一致。

恢复自然负载后曾观察到 PRESSURE，原因是既有 `memory_high_events_with_pressure`，随后恢复 NORMAL；不能将其冒充新增低可用分支的生产命中。随后一次采样可用内存约13.7 GiB、memory PSI avg10为0。初始止血也包含用户授权的任务中断与服务重启，因此不能把恢复量全部归功于阈值修复。

**已定位资源失速机制并修复一处保护盲区；具体首发进程归因和长期不复发仍未验收。**发布后磁盘约98%、仍余约11 GiB，是独立容量风险。本轮没有删除业务数据，例行镜像协调使用原发布框架；第一轮协调 errors 为空。

## 证据和边界

公开证据位于 [发布审计目录](../artifacts/audits/main_release_20261117/public-summary.json)，包含汇总、源码核验及memory/load/cpu/disk/paging/swap/PSI的SAR文本。最终补发另保存 final-public-summary.json 与 final-source-verification.json。

远端完整日志、SAR摘录、脚本及私有回滚快照：`/var/lib/doxagent/releases/message-efficiency-20261117/`。有效生产环境只留远端私有目录，不提交Git。重启后退出的captcha-proxy已以原镜像恢复，未切换代理区域；这不等于Reuters正文访问已恢复。

原开发回归中的RuntimeJournal存量失败仍见 [开发记录](message-efficiency-implementation-20261117.md)，未修改测试掩盖。短窗口消息／健康验证不代表全部源成功、全部Case完成或完整交易日资源稳定。

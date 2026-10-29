# 32GB整体集成、正式发布及45分钟观察

## 当前实施边界

用户授权：适配8核32GB，整合本地main全部开发，推送origin/main，正式重建相关生产容器，再进行完整45分钟观察；修复IBKR/Reuters之外仍失败来源。不更改W1/W2/W3能力或无依据削减预热，不删生产事实/Profile。

实测基线：32GB升级后MemTotal约31GiB，远端仍有14G/15G宿主slice、Site6G/Supervisor8G/Worker15G的旧边界。之前正常低在途10分钟Worker2.41–3.36GiB，浏览器合计约7GiB；积压释放时Worker约10–11GiB、Site约4–5GiB、Supervisor约3–5GiB。CPU仍8核，不能因内存翻倍机械翻倍浏览器或SDK并发。

## 内存配额

采用可借用的组件上限，非静态预留，总和允许超过父级。父slice改为MemoryHigh28G、MemoryMax30G，SwapMax保持1G；保留宿主正常余量，避免以大Swap掩盖回收抖动。

|组件|内存上限|内存+Swap上限|依据|
|---|---:|---:|---|
|Worker|20G|21G|覆盖已见10–11G积压峰值及后续ticker增长，不削减预热|
|Site Access|10G|11G|覆盖多Identity controller/managed Chrome及热页|
|Chrome Supervisor|10G|11G|受保护Chrome/Profile驻留与自然多页面负载|
|Scheduler|8G|9G|覆盖W1/W2/W3本地上下文及恢复重叠|
|API、Collector、Bus、BODY、Projector|各4G|各5G|显著高于既有正常占用，Collector补入统一slice|
|CDECR/初始化/O4等|保留既有6G/4G等|保留既有|不另加研究或W硬门禁|

压力控制器改读实际memory.max比例，28G+真实压力进入原PRESSURE逻辑，29.5G进入原接近硬边界逻辑；原时序/滞回不变，不残留14/14.75G硬编码。上线同时核对systemctl set-property生成的持久覆盖、Docker实际HostConfig及正式Compose配置，不能只改仓库文本。

## 全量集成与发布步骤

1. 保存本地/远端dirty、HEAD和资源/在途基线。包含退出策略模块、交易执行/行情修改、W3提示词、Runtime前端、运维脚本及调试工具/文档；不把多年eval生成物、凭证和临时结果作为开发源盲目入库。
2. 合并新退出能力时保留main已存在的ENTRY时效/三次失败放弃、并发订单对账、SDK回调持久性、非阻塞维护及短事务。旧分支覆盖这些修复必须整合，不能以“全部提交”为由直接发布回退。
3. 运行相应Python、schema/typecheck及前端夹具检查，审查完整staged diff，追加changelog，提交并推送main。
4. 先更新宿主slice及控制器，正式build backend/site/supervisor/web等实际需要的镜像。按依赖应用最新Compose与现有私有环境覆盖，正式更新全部实际消费者，保留数据/Profile和可续作在途事实；不用临时生产容器/source overlay。不默认启用任何尚未授权的退出策略账户绑定。
5. 核对组件版本、真实内存边界、健康、游标/case/Read和消息源实际进展。清退真正未引用的作废镜像，保留动态模板/依赖及必要回滚，禁止删卷。

## 45分钟验收及排查

完整窗口从正式必要角色就绪后开始，前段回补与后段自然负载分开，但失败不删除。以全部应执行source/ticker/必需频道周期验证真实取网/新鲜列表、延期/失败/缺失/在途；真实新canonical正文首次/当前FULL及article×ticker完整交付分母。Owner、Runtime消费、Read/UI边界分开，不能用已发布子样本代替最终消费。

资源每分钟低成本宿主/cgroup差分；较低频采集Worker真实job/预热归属。CPU仍8核，观察iowait、IOPS/吞吐/await/队列、匿名/文件缓存、refault、Swap/PSI及工作集增长。W1/W2/W3非LLM跨度只用真实持久跨度，缺失记不可测。按逐source证据修复IBKR/Reuters之外的真实故障，不因更大内存隐藏池故障、缺证据或错误解析。

最终补记：精确提交、测试、正式发布、45分钟全量数据、来源修复与剩余瓶颈。未实际完成不得声称达标。

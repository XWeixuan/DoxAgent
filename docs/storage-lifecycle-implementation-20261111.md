# 资源生命周期优化实施记录（进行中）

依据 `doxagent-storage-lifecycle-optimization-plan-20261111.md`，用户已授权执行修复。独立分支 `codex/storage-lifecycle-20261111`，工作树 `D:/DoxStorageLifecycle20261111`，起点为生产提交 `b4f18aca`。原始脏工作区保留，不能将其中尚未提交的 Runtime/W3 改动夹带发布。生产后续采用各角色实际镜像为底的精确文件构建，通过现有 release state/Compose 发布；尚未执行。

## 当前进度

- R1：范围索引、剩余预算贯通、按阶段短事务提交和游标；现有 Read 11 项回归通过，规模/故障专项待补。
- A1：候选先限量、引用在 writer 下复核、小时与物理/小表退出分开提交、16 unit-hour/轮及游标；原96h等效等3项回归通过（96秒）。需补 protected 大账本成本/提交失败专项。
- D1：大不可变字段复用 Native CAS，StateV21 小状态不重复展开大输入；正式 candidate 发布记录 settlement；retire 合同及历史 staged 读取。D3 初次50项通过、1项因本地C盘临时文件空间不足失败；改D盘临时目录重跑中，尚不能宣称该组通过。
- D2：workspace按256 job候选页、既有run索引、候选恢复责任核验、已结算24h、marker owner版本、writer外文件退出与D3结算。待定向测试和独立低频入口，不能恢复全历史扫描到热派发。
- T1：廉价结算不被实时W3全局门槛阻断；pending root重试、5s候选预算/30s重压缩预算、已成功非W3过程输入24h，保留业务版本/交易。待回归。
- R2/B2/P1：停止已切Acquisition的旧poll重复投影、MVCC关闭旧技术头；冷缓存按7d/阈值退出，轮转cache目录/profile；精确纳管旧doxagent:o2-auto-* tag。待回归。
- I1/S1/B1/L1/容量覆盖、旧数据迁移/物理回收、发布与生产验收：尚待完成。

测试使用原环境 `C:/Users/WEIXUANXIE/Desktop/DoxAgent/.venv/Scripts/python.exe`，`PYTHONPATH=D:/DoxStorageLifecycle20261111/src`。所有后续临时文件设为D盘，`--basetemp`使用此工作树eval独立路径。原本地C盘测试临时文件的批量删除被工具安全策略拒绝，未绕过；通过将后续测试临时目录迁至D盘继续工作。

尚未commit/push/deploy；尚未追加本次代码changelog（收尾前必须追加）；尚未做第二轮生产数据清理。此前40.105GiB应急回收不计为本轮修复收益。

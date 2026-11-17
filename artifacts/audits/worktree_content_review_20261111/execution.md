# Worktree退役执行记录（2026-11-11）

按用户认可的内容复审报告执行。未提交、推送或删除分支。

- 18个辅助工作树已取消Git登记；17个目录已移除，1个只剩空目录。
- 13条物理目录不存在的旧记录经再次dry-run后prune。当前只登记主目录main。
- 55个本地分支保留；除并行任务更新main外，其余原分支指针一致。
- A类方案已更新脱敏并迁入docs，单消息/正文0%复现已转入test_bus_dashboard.py，changelog已追加；三项均未暂存、未提交。
- B类484个文件共57927083字节，已在指定D盘根目录按原相对路径归档；归档后及最终检查均逐文件核对SHA256和大小。
- W1旧11项修改按获准精确清单处理；C生成物已舍弃，D成果未重复迁移。
- Python消息源卡片12项回归通过；TypeScript检查及react/vite/vitest/axe/playwright解析通过。

## Windows junction事故与修复

旧Bus Metrics、DialogFix、TradeUpgrade的node_modules是指向主目录依赖的junction。前两个正常Git移除遇到此类链接时留下残留，并造成主目录部分共享依赖缺失。随后仅非递归解除旧树链接，不进入链接目标。主目录依赖从本地pnpm缓存按原锁文件重建425包，禁用安装脚本，未改package.json/锁文件；已通过TypeScript及依赖解析检查，修复备份已清理。

Bus/Dialogue残留通过临时无检出登记、git worktree repair恢复Git链接，再正常remove；没有直接递归删除.codex工作树目录，也未修改会话数据。Bus目录最后为空，但Windows拒绝删除该目录本身，未改ACL或强杀占用进程。

这也说明前次目录逻辑大小包含共享依赖的重复计数，不能直接当作可释放物理空间。

## 最终状态

- C:\ 可用空间 7.37 → 13.55 GiB，操作窗口净增加 6.18 GiB。
- D:\ 可用空间 25.99 → 32.32 GiB，操作窗口净增加 6.33 GiB。

期间另有主目录消息源修复任务提交，main由3348f2fc前进至当前HEAD；本轮没有执行commit或push，没有撤销其改动。盘符净变化包含该并行窗口的其他活动，不作逐字节因果归因。

空目录残留：`C:\Users\WEIXUANXIE\.codex\worktrees\bus-metrics-reading\DoxAgent`。无文件、无Git登记、无分支占用。

详见execution-verification.json、archive-verification.json、execution-prune-dry-run.txt、execution-prune-output.txt及worktrees-after.txt。

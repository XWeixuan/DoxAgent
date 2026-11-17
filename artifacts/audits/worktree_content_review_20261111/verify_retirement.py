"""Final read-only verification and local execution receipt."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
ARCHIVE = Path(r'D:\DoxAgentArchives\worktree-retirement-20261111')


def git(*args):
    return subprocess.check_output(['git', '--no-optional-locks', '-C', str(ROOT), *args]).decode('utf-8')


trees = json.loads((OUT / 'trees.json').read_text(encoding='utf-8'))
before = json.loads((OUT / 'execution-before.json').read_text(encoding='utf-8'))
archive = json.loads((OUT / 'archive-verification.json').read_text(encoding='utf-8-sig'))
assert archive['files'] == 484
for row in archive['verified']:
    p = Path(row['target'])
    assert p.is_relative_to(ARCHIVE) and p.stat().st_size == row['bytes']
    assert hashlib.sha256(p.read_bytes()).hexdigest() == row['sha256'], p
refs = dict(x.split() for x in git('for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads').splitlines())
old_refs = dict(x.split() for x in before['refs'])
assert set(refs) >= set(old_refs)
assert all(refs[name] == sha for name, sha in old_refs.items() if name != 'refs/heads/main')
assert git('branch', '--show-current').strip() == 'main'
worktrees = git('worktree', 'list', '--porcelain')
assert worktrees.count('worktree ') == 1 and 'prunable' not in worktrees
residue = []
for tree in trees:
    p = Path(tree['path'])
    if p.exists():
        entries = list(p.iterdir())
        assert not entries, f'Nonempty retirement residue: {p}'
        residue.append(str(p))
    subprocess.run(['git', '-C', str(ROOT), 'merge-base', '--is-ancestor', tree['head'], 'HEAD'], check=True)
assert len(residue) <= 1
assert not git('diff', '--cached', '--name-only').strip()
assert 'tests/test_bus_dashboard.py' in git('diff', '--name-only')
document = ROOT / 'docs/frontend-bus-source-and-runtime-latency-audit-plan-20261111.md'
assert document.is_file()
assert 'docs/frontend-bus-source-and-runtime-latency-audit-plan-20261111.md' in git('ls-files', '--others', '--exclude-standard')
head_changelog = git('show', 'HEAD:changelog')
assert 'Retire reviewed historical worktrees' not in head_changelog
assert 'Retire reviewed historical worktrees' in (ROOT / 'changelog').read_text(encoding='utf-8')
junit = ET.parse(OUT / 'migration-tests.xml').getroot()
suites = list(junit.iter('testsuite'))
assert sum(int(s.get('failures', 0)) + int(s.get('errors', 0)) for s in suites) == 0
assert sum(int(s.get('tests', 0)) for s in suites) == 12
assert not (ROOT / 'frontend/v2/node_modules.retirement-repair-20261111').exists()
assert (ROOT / 'frontend/v2/node_modules/@axe-core/playwright/package.json').is_file()
disk_before = json.loads((OUT / 'disk-before.json').read_text(encoding='utf-8-sig'))
disk = []
for row in disk_before:
    now = shutil.disk_usage(row['drive']).free
    disk.append({'drive': row['drive'], 'free_before': row['free'], 'free_after': now, 'net_increase': now-row['free']})
receipt = {
    'retired_worktrees': len(trees), 'directories_absent': len(trees)-len(residue),
    'empty_directory_residue': residue, 'remaining_worktrees': 1,
    'stale_records_pruned': 13, 'branches_retained': len(refs),
    'archive_files_verified': archive['files'], 'archive_bytes': archive['bytes'],
    'main_head_before': before['head'], 'main_head_after': git('rev-parse', 'HEAD').strip(),
    'main_advanced_by_other_task': before['head'] != git('rev-parse', 'HEAD').strip(),
    'uncommitted_migration': ['docs/frontend-bus-source-and-runtime-latency-audit-plan-20261111.md', 'tests/test_bus_dashboard.py', 'changelog'],
    'python_tests_passed': 12, 'typescript_check_passed': True,
    'dependency_recovery': '425 locked packages restored offline; no install scripts; typecheck and package resolution passed; old cache backup removed',
    'disk': disk,
}
(OUT / 'execution-verification.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
lines = [
    '# Worktree退役执行记录（2026-11-11）', '',
    '按用户认可的内容复审报告执行。未提交、推送或删除分支。', '',
    f'- 18个辅助工作树已取消Git登记；17个目录已移除，1个只剩空目录。',
    '- 13条物理目录不存在的旧记录经再次dry-run后prune。当前只登记主目录main。',
    f'- 55个本地分支保留；除并行任务更新main外，其余原分支指针一致。',
    '- A类方案已更新脱敏并迁入docs，单消息/正文0%复现已转入test_bus_dashboard.py，changelog已追加；三项均未暂存、未提交。',
    '- B类484个文件共57927083字节，已在指定D盘根目录按原相对路径归档；归档后及最终检查均逐文件核对SHA256和大小。',
    '- W1旧11项修改按获准精确清单处理；C生成物已舍弃，D成果未重复迁移。',
    '- Python消息源卡片12项回归通过；TypeScript检查及react/vite/vitest/axe/playwright解析通过。', '',
    '## Windows junction事故与修复', '',
    '旧Bus Metrics、DialogFix、TradeUpgrade的node_modules是指向主目录依赖的junction。前两个正常Git移除遇到此类链接时留下残留，并造成主目录部分共享依赖缺失。随后仅非递归解除旧树链接，不进入链接目标。主目录依赖从本地pnpm缓存按原锁文件重建425包，禁用安装脚本，未改package.json/锁文件；已通过TypeScript及依赖解析检查，修复备份已清理。', '',
    'Bus/Dialogue残留通过临时无检出登记、git worktree repair恢复Git链接，再正常remove；没有直接递归删除.codex工作树目录，也未修改会话数据。Bus目录最后为空，但Windows拒绝删除该目录本身，未改ACL或强杀占用进程。', '',
    '这也说明前次目录逻辑大小包含共享依赖的重复计数，不能直接当作可释放物理空间。', '',
    '## 最终状态', '',
]
for row in disk:
    lines.append(f'- {row["drive"]} 可用空间 {row["free_before"]/2**30:.2f} → {row["free_after"]/2**30:.2f} GiB，操作窗口净增加 {row["net_increase"]/2**30:.2f} GiB。')
lines += ['', '期间另有主目录消息源修复任务提交，main由3348f2fc前进至当前HEAD；本轮没有执行commit或push，没有撤销其改动。盘符净变化包含该并行窗口的其他活动，不作逐字节因果归因。', '', f'空目录残留：`{residue[0] if residue else "无"}`。无文件、无Git登记、无分支占用。', '', '详见execution-verification.json、archive-verification.json、execution-prune-dry-run.txt、execution-prune-output.txt及worktrees-after.txt。']
(OUT / 'execution.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
shutil.copy2(OUT / 'execution.md', ARCHIVE / 'execution.md')
shutil.copy2(OUT / 'execution-verification.json', ARCHIVE / 'execution-verification.json')
print(json.dumps(receipt, ensure_ascii=False, indent=2))

# 绑定额外正则重调 · 2026-11-27

最终规则是 `rules-selected.json`（16个实际绑定），正式历史回放1824/8707额外过滤，79项业务控制全部符合预期。原始输入沿用 `../binding_extra_regex_20261127/cases.json`，SHA见manifest；不重复提交全文。详细判断边界、偏差与上线状态见 `../../docs/by-distribution-binding-extra-regex-retune-20261127.md`。

在项目Python环境设置 `PYTHONPATH=src`，依次运行：

```powershell
python eval/binding_extra_regex_retune_20261127/build_candidate.py
python eval/binding_extra_regex_retune_20261127/replay.py
python eval/binding_extra_regex_retune_20261127/check_controls.py
python eval/binding_extra_regex_retune_20261127/finalize_evidence.py
```

最后一步需要原先的标题审计 `eval/distribution_title_audit_20261127/title-review.jsonl`；只生成可比分母统计和复核台账，不据标题预警自动生成GOLD。`all-history-results.csv`覆盖全部消息；`blocked-review.csv`覆盖额外排除案例；`expected-controls.json`是不参与线上匹配的离线控制。控制来自本次复核，不是盲测验证集。

探索版本和全文复核卡片本地留存但不提交；上线脚本仅接受选定规则，通过现有Binding CAS及审计写入，不重放或删除历史消息。

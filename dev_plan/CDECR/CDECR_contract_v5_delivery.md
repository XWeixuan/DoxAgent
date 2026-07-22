# CDECR v5 契约修订交付

日期：2026-07-21

## 已实现

- Grounder 使用请求内短 Candidate ID，模型输出 Evidence 仅含 `segment_id + text`。
- 程序在 M4 Judge 后执行唯一精确定位，并生成最终 EvidenceSpan。
- `source_claim` 贯通 Grounder、Judge、EventMention、Atomic claims、Registry 和导出。
- Participant/Quantity/Local Package Hint 按 v5 分层，未增加附件明确排除的额外 Schema。
- 全部 Grounder drafts 强制进入 M4 `qwen3.7-max`；JSON Mode 与 `effort=none` 保持不变。
- 删除单文档和跨文档业务 confidence；多解或 UNCERTAIN 统一进入 HOLD。
- SQLite `user_version=5`，同步升级单文档 Pipeline/Prompt 和处理幂等身份。

## 验证

- Grounder Schema 检查：无 confidence、`start_char/end_char`、`entity_id`、
  `needs_judge/judge_reasons`、`package_family`；包含 `source_claim`。
- CDECR 非真实测试：139 passed，3 skipped。
- `ruff check src/cdecr tests/cdecr`：通过。
- `mypy --strict src/cdecr`：通过（24 个源文件）。
- Wheel：`dist/doxagent-0.1.0-py3-none-any.whl` 构建通过，含 `cdecr` 代码、Prompt 与目录资源。

真实语料需要使用 v5 新处理键重新运行；历史 v4 质量指标不能代表本契约效果。

# CDECR 步骤一交付说明

日期：2026-07-16

## 当前结论

步骤一的代码、契约、只读数据基线、SQLite Registry、模型适配器、CLI、测试和 wheel
打包已经落地。真实 DoxAtlas 数据验收及 M1–M4 真实模型探针均已通过，步骤一硬门槛现已
全部关闭，**本步骤标记为完成**。

## 已交付内容

- 独立 `src/cdecr` 包，不导入 `doxagent.*`，不注册 DoxAgent 工作流，也不读取其
  Gateway/Settings。
- 严格领域契约：Source Message、Event Mention、三种金融 Projection、Atomic Event、
  Event Package、成员关系、外部关系及全部判定枚举。所有契约禁止额外字段；证据位置采用
  左闭右开区间并支持原文切片一致性校验。
- 独立端口：`SourceMessageReader`、`EmbeddingClient`、`StructuredModelClient`、
  `CDECRRegistry`。
- 通过 Supabase REST `GET /rest/v1/raw_media` 的只读适配器。查询必须包含 market、ticker、
  UTC 时间窗和 limit，只选映射所需列；未实现远端写方法，也未接受 service-role 配置。
- SQLite Registry：`user_version=1`、foreign keys、WAL、busy timeout；包含 Source、Mention、
  Atomic、Package、成员/关系、Embedding、Run、Model Call 和 Decision Audit 表。Source、
  Mention、审计不可覆盖，Atomic/Package 版本连续递增，相同输入幂等。
- M1 `text-embedding-v4`（1024 维、最多 10 条）、M2 `deepseek-v4-flash` Chat Completions
  JSON Mode 且关闭 thinking、M3/M4 `qwen3.7-plus`/`qwen3.7-max` Responses API。按步骤四
  最新性能要求，M3/M4 推理强度统一为 `none`；错误只保留脱敏 code、HTTP status 和 latency。
- 四个机器可读 CLI：`registry init`、`data snapshot`、`models probe`、`doctor`。
- `cdecr` 已纳入 wheel、ruff、mypy 和 pytest，并携带 `py.typed`。

## 数据基线

固定查询为 `US/MU/[2026-06-25T00:00:00Z, 2026-06-26T00:00:00Z)`，正文至少 200 字，
最多保留 200 条。真实读取结果如下：

- 原始行：250
- 合格 Source Message：153
- 拒绝：97
- 来源：16
- 全文快照：`.tmp/cdecr/baselines/us_mu_2026-06-25.jsonl`（Git 忽略）
- 可提交清单：`dev_plan/CDECR/baselines/mu_2026-06-25_manifest.json`

清单只包含查询条件、row id、自产 SHA-256 指纹、数量和来源/拒绝统计，不包含新闻标题、
正文或 URL。真实 DB 测试验证了不少于 100 条、不少于 10 个来源，并与清单逐项一致。

## 验收结果

| 检查 | 结果 |
| --- | --- |
| CDECR focused 非真实测试 | 128 passed，3 deselected |
| CDECR 真实 DB 测试 | 1 passed，1 deselected |
| CDECR scoped ruff | passed |
| CDECR scoped strict mypy | passed |
| `registry init` CLI | passed |
| `data snapshot` CLI | passed，153 条基线已生成 |
| `doctor` CLI | passed，包边界/SQLite/Supabase/model config 全部正常 |
| wheel build | passed，wheel 内含 `cdecr` 与 `py.typed` |
| 完整非真实 pytest | 467 passed，21 skipped，26 deselected，2 个既有失败 |
| 全仓 ruff | 被既有 DoxAgent/eval lint 基线阻塞；CDECR scoped 通过 |
| 全仓 mypy | 被 33 个既有 DoxAgent 文件的 1771 项错误阻塞；CDECR scoped 通过 |
| M1–M4 真实 probe | passed：M1 返回 1024 维；M2–M4 返回符合 probe Schema 的 JSON |

完整非真实 pytest 的两个既有失败为：

1. `tests/test_baseline.py` 要求的 `dev_plan/PHASE0_BASELINE.md` 当前不存在；
2. `tests/test_dashboard_real_overview_api.py` 期望 `today_token_cost_usd is None`，实际读取到
   已持久化的 `0.006548`。

二者均不涉及 `src/cdecr`，本轮未扩大范围修改。

## 已完成的真实复验

使用当前主 key（原 key 保留为 fallback）执行：

```powershell
$env:PYTHONPATH='src'
python -m cdecr models probe --tiers m1,m2,m3,m4
$env:CDECR_RUN_REAL_MODEL_TESTS='1'
pytest -q tests/cdecr/test_real_integrations.py -m cdecr_real_models
```

复验确认 M1 返回 1024 维向量，M2–M4 均返回符合 probe Schema 的 JSON，且探针未触发
fallback。API key 未写入报告、stderr 或 Git 跟踪文件。

## 安全边界

本轮没有修改 DoxAtlas 远端 Schema。当前 Supabase 安全检查仍报告 `raw_media` 等 public 表未
启用 RLS；不能只执行 `ALTER TABLE public.raw_media ENABLE ROW LEVEL SECURITY`，因为在没有
先配置读取策略时会直接阻断现有 publishable-key 访问。后续应先设计并验证策略，再单独迁移。

参考：

- [Supabase Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [DashScope text-embedding-v4](https://help.aliyun.com/en/model-studio/text-embedding-synchronous-api)
- [DashScope DeepSeek API](https://help.aliyun.com/en/model-studio/deepseek-api)
- [DashScope Responses API](https://help.aliyun.com/en/model-studio/compatibility-with-openai-responses-api)

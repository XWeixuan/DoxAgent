# CDECR v2 对象知识库构建交付报告

生成时间：2026-07-22T23:58:23+08:00

## 结论

12 个 catalog 已按数组式 v2 schema 生成。最终严格验证结果：`valid=true`，错误 0 个，提醒 11 个。提醒项是上下文允许的 alias 多义性统计，不是 schema 错误。

Catalog 内容哈希（SHA-256）：`4d3f0cb842a59f983eabd5630957a32300bd1c8a8f343b1261ee621749f1ef76`。

本轮只生成 JSON 与离线构建/验证工具，没有修改现有运行时 loader。v2 使用独立目录，避免数组格式覆盖 v1 后破坏当前 loader。

## 最终规模

| Catalog | Records | MiB | Empty alias lists | Normalized alias collisions |
|---|---:|---:|---:|---:|
| `companies.json` | 8,335 | 0.892 | 2 | 139 |
| `institutions.json` | 28,865 | 3.768 | 9,155 | 371 |
| `persons.json` | 99,594 | 9.779 | 55,277 | 16,548 |
| `instruments.json` | 40,354 | 5.388 | 0 | 3,096 |
| `places.json` | 302,720 | 31.879 | 32,104 | 62,887 |
| `named_objects.json` | 102,243 | 16.136 | 17,874 | 5,849 |
| `concepts.json` | 258 | 0.032 | 0 | 14 |
| `metrics.json` | 9,248 | 2.610 | 5 | 45 |
| `fiscal_periods.json` | 207,190 | 39.584 | 0 | 312 |
| `units.json` | 1,664 | 0.140 | 1,180 | 298 |
| `artifacts.json` | 248,044 | 59.248 | 0 | 1,880 |
| `attributes.json` | 77 | 0.007 | 0 | 0 |

## 数据获取证据

- SEC Company Facts：已处理 8,011 个 CIK 检查点，其中错误/无数据 1,031 个；原始响应未保留。
- SEC submissions：已处理 8,011 个 CIK 检查点，其中错误/无数据 0 个；只保留近五年目标表单摘要。
- SEC insider transactions：20 个季度 ZIP。
- EPA National Single：扫描 5,300,149 行；owner 命中源记录 196,241，最终选择 50,000；未匹配工业设施样本 25,000。
- SEC Financial Statement Data Sets：近四个可用季度；custom tag 按 NUM 使用频率排序后限量补充。
- GeoNames：countryInfo、admin1CodesASCII、cities500、US、alternateNamesV2。
- FASB：2026 US GAAP Taxonomy。
- Institution：SEC 当前 RIA 月度 ZIP、FDIC 当前 Institutions CSV、SEC Active Broker-Dealers、受限 Wikidata 类别。

## 规模与质量决策

- Place 未导入 GeoNames 千万级全集，只保留国家、一级行政区、全球 cities500，以及美国行政/人口不少于 100 的居民点；alias 每对象最多 20 个。
- Metric 保留 FASB 数值型可报告概念，排除 abstract/axis/domain/member/table/text block 等结构项；SEC 十万级 custom tag 只取高频候选。
- Person 只保留近五年 officer/director，不导入普通 10% 股东；跨人物重复的单词 alias 被删除。
- EPA owner 命中按每 owner 最多 100 个与全局 50,000 上限做多样化，未匹配工业设施另取稳定 25,000 样本。
- Artifact 的 8-K/6-K 每公司限量；Earnings Release 允许按最近 filing 合成，不要求 Exhibit 99.1 明确证据。
- Fiscal Period 过滤比较期事实：filing date 与 fact end 的差必须在 0–200 天，并保留最近十个实际财年及一个 364 天平移的未来财年。

## 已知边界

- IARD `firm_compilation.zip` 当前只有规范、示例和说明，不含完整机构记录；因此改用 SEC 官方当前 RIA 月度 ZIP。该 ZIP 覆盖 SEC 注册 RIA，不等于全量州注册 adviser。
- FINRA A–Z 页面未抓取，Broker-Dealer 使用 SEC Active Broker-Dealers 替代。
- 静态 Artifact 无法覆盖全部分析师报告、公司新闻稿和协议；本次重点覆盖 SEC filing 与合成 Earnings Release。
- Person 与 Named Object 按公开批量资源尽力完成；长尾卖方分析师、私有产品/项目仍可能缺失。
- Alias collision 统计包含 Place 同名、Person 同名不同 org、Fiscal Period 跨公司同名等设计上需要字段上下文消歧的情况。

## 复现与规则

完整构建顺序、数据源、ID 冲突规则、Artifact kind 与限量策略见 `scripts/cdecr_kb/README.md`。

最终校验原始报告位于 `D:\cdecr-kb-work\reports\catalog_validation.json`。

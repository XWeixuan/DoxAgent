# MU D3 版本 22：模型拆分与条件索引

完整版本 22 保持原样。本目录是离线摘录，不是新的发布版本；不应把单个子集导入正式工作流或当作完整 coverage。

## 拆分口径

按最终接纳的 Build/补研稿的实际 Worker request.model/effort 分组，保留最终版本 22 的 Policy 正文及 ID：

| 分组 | Policy 数 | 文件 |
| --- | ---: | --- |
| gpt-6-sol / high | 140 | [可读版](document3_gpt-6-sol_high.md) · [JSON](document3_gpt-6-sol_high.json) |
| gpt-6-luna / max | 122 | [可读版](document3_gpt-6-luna_max.md) · [JSON](document3_gpt-6-luna_max.json) |

两组无重叠，合计原版全部 262 条 Policy，内容与原版逐条相同。

单纯按 Policy ID 首次出现划分会得到 Sol 148 / Luna 114，但其中 8 条原 Sol 稿后来由 Luna 续跑或补研保留同一 ID 重新交付，因此本次归入 Luna 组。Sol 组剩余 140 条中，还有 8 条的条件经 Luna 最终写入修改，已在可读版逐条标注；132 条保持接纳的 Sol 研究稿内容。所有 Policy 共同经过 Luna Integration 审查，这两份不能解释成完全独立的模型对照实验。

## 全量索引

[policy_title_criterion_index.json](policy_title_criterion_index.json) 包含 262 条 Policy 的 title 与全部 576 条 condition 的 criterion，保持原顺序与完整文本；保留 policy_id、condition_id 供定位，不包括 transmission、校准或其他长字段。

## 可核验来源

[policy_model_provenance.json](policy_model_provenance.json) 逐条记录初始来源、最终接纳研究稿来源、最终内容写入任务，以及 run/task/path/job/model/effort 与发生变化的字段。[origin_evidence.json](origin_evidence.json) 保存从远端隔离测试库及 Worker JobStore 只读提取的任务和接纳稿证据。生成器 export_model_split.py 可在本目录重建导出。

提取时验证真实 Worker 模型与持久任务 request 一致、接纳任务完成、子集完整且互斥、索引 title/criterion 完整相等；原 document3.json SHA-256 仍为 `7b55edcda0dd2d354543544845d6ec973f80bd7be591df021fe642c56b38213a`。

## 保留 ID、后由 Luna 重新交付的 8 条

- `pol_b89494adef244810b9e8bd4c2eca5c17`：HAMR商业供给、交期与高容量存储替代（research:S0004:13）。
- `pol_b6fea7b9ab1549718869daec4f5919b6`：战略供给最低采购的法律可执行性（research:S0009:72）。
- `pol_c38109f3076c4eefb83223e6feaed387`：供给争议中保证金与补偿的最终追索权（research:S0009:72）。
- `pol_cb76a68a6a1e4aa48b2e96c6f980f6fd`：争议后战略供给关系的重新安排（research:S0009:72）。
- `pol_d9a3943e820049d38ba8dc6f1d50bb04`：战略供给最低采购义务的暂停与恢复（research:S0009:72）。
- `pol_369f871bf11d41dc8f3b050f68c3e328`：战略供给的交付、验收争议与补救（research:S0009:72）。
- `pol_48a68a175ffe41b78f916337d103d45c`：MU供给争议的反向赔偿责任（research:S0009:72）。
- `pol_3f484c2a156243cc9d57c6185916c2c1`：美光HBM投片与已认证常规DRAM客户之间的配额重排（supplement:S0001:1）。

## Sol 研究稿后经 Luna 最终编辑的 8 条

- `pol_c5b0d5c9326c4b0c9c2d36ddba195add`：节点迁移与封装瓶颈对2027合格DRAM供给增量的修订（integration:final:0）。
- `pol_a90380812cb242dda9b045e68f7a60b5`：美光HBM在MI450与Helios路线的设计参与（integration:final:0）。
- `pol_4609893d792a47589ff82f0647e0afb1`：DRAM量产流程与瓶颈设备的有效工时（integration:final:1）。
- `pol_1c994bc18b624cd79019a41bb6cb7383`：跨年SCA组合的有效采购覆盖与承诺收缩（integration:final:2）。
- `pol_6184ef97555f44668ddd45d5a2e0c505`：SCA主供产品的交付窗口、备用来源与补供配置（integration:final:2）。
- `pol_792510c77907471dbfc228bf3136ce13`：美光客户价格补偿、退货与销售对价结算（integration:final:2）。
- `pol_0003201202d94113af9ec23c0a217ac9`：美光地域所得税与实质运营优惠的净保护（integration:final:2）。
- `pol_609acf451f294a6cbdba081ad9c15c6c`：美光新加坡RIC经营支出支持的税务资格与兑现（integration:final:2）。

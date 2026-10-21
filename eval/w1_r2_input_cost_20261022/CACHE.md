# GLM W1R2 缓存实测

2026-10-22；模型 `ZHIPU/GLM-5.3-FlashX` high；百炼原测试端点 `https://dashscope.aliyuncs.com/compatible-mode/v1`，未另换业务空间。最大输出 16,384，超时 120 秒，stream/include_usage；没有 SDK 自动重试。密钥仅进程环境注入。

公共系统内容为 Core + W1R2 Prompt + Exact Output Contract；Schema 确定性序列化。Case ID、ticker、日期和版本统计不进入 system。业务视图原文发给 user，不把整段 Markdown 再编码为 JSON 字符串。生产未增加未经证实的 cache_control 或 TTL。

配对测试保留了历史冻结 Prompt 的两个版本：4/6 的 NEW reason 规则更短，其余 18 例相同。两组逐 Case 保留各自业务标准，只替换视图格式说明；未把当前本地后来已有的“输出一致性示例”追加进旧冻结实验。缓存串行专选系统完全相同的 1/2/3/5/7/8 六个不同 Case，包含不同 ticker、不同事件和仅 provisional，避免混用 Prompt 版本。

## 串行首次与后续命中

| Case顺序 | 原输入 input / cached | 优化隐式 input / cached | 标记探测 input / cached |
| --- | ---: | ---: | ---: |
| 1 | 4,226 / 0 | 3,384 / 0 | 3,384 / 0 |
| 2 | 5,574 / 0 | 4,373 / 0 | 4,373 / 0 |
| 3 | 2,823 / 1,920 | 2,780 / 2,112 | 2,780 / 2,112 |
| 5 | 超时，无 usage | 8,248 / 2,112 | 8,248 / 2,112 |
| 7 | 3,227 / 1,920 | 2,783 / 2,112 | 2,783 / 2,112 |
| 8 | 3,813 / 1,920 | 3,195 / 2,112 | 3,195 / 2,112 |

优化隐式串行六例合计命中 8,448 / 24,763 = **34.12%**；首次之后五例为 8,448 / 21,379 = **39.52%**。第二例尚未命中，后四例命中相同的 2,112 tokens。不同业务内容下稳定命中，符合系统前缀复用预期，但 Provider 没有返回精确区间，不能证明每一个 cached token 都属于 system。cl100k 对优化 system 估算 3,056，与 GLM 原生 tokenizer 不等同，不能直接相减估计“漏缓存”。

## 完整20例

| 组 | 输入 | cached | 加权命中率 | 输出 | 平均耗时 | 按Token费用估算 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 优化、隐式 | 67,899 | 21,120 | 31.11% | 9,227 | 4.62s | ¥0.170185 |
| 优化、cache_control探测 | 67,899 | 38,464 | 56.65% | 11,775 | 5.81s | ¥0.163219* |

两轮 user/system 文本完全相同；探测组只将 system 内容改为 text block 并加入 `cache_control: {type: ephemeral}`，无 1h 参数。端点接受了所有请求，但 usage 只有 cached_tokens，没有显式写入/创建字段。前六次串行命中**完全相同**，没有显式创建可观测收益；后续全量命中更高，且有 Case 12 命中 6,272/6,321、Case 6 命中 2,624/2,652，明显包含重复业务内容。

探测在隐式组之后，复用了相同 20 例，缓存已被加热；不能把 56.65% 和较低费用解释为显式标记的因果收益。没有创建回执也不能证明 API 忽略了标记，结论仅为**接受请求，未证实显式缓存创建，不进入生产配置**。采用已获文档支持且实测可命中的隐式方案即可。

`*` 探测组金额是按已公布的 GLM 隐式缓存价格折算；不是已确认的显式缓存账单。官方未给 FlashX 显式缓存支持/写入单价，不编造创建费。原输入首次 19 例有 usage、1 例超时无 usage；缺失记录保留 null，不计成零。补测 5 命中 11,776 tokens，可能来自此前服务端已处理请求，也不能当作冷缓存输入费用。

## 官方依据

- [百炼上下文缓存](https://help.aliyun.com/zh/model-studio/context-cache)：FlashX 支持隐式；智谱模型最小公共前缀 512 tokens；命中不保证。显式支持列表未列 FlashX，通用显式有效期 5 分钟，不能套 Haiku 的 1 小时缓存。
- [FlashX 模型与价格](https://help.aliyun.com/en/model-studio/glm-5-3-flashx-by-zhipu)：北京输入/隐式命中/输出分别 2/0.57/7 元每百万 tokens。

原始依据为 `calls/*-request.json`、`calls/*-chunks.json`、`calls/*.json`，聚合为 `analysis.json`。统计分母使用总输入 token，不平均单 Case 百分比。

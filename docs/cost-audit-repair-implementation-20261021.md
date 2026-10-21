# 成本审计修复实施记录

完成日期：2026-10-22。依据：`dev_plan/workflow_v2/cost_audit_repair_execution_plan_20261021.md`。按用户最终要求收尾：不再扩展功能，不继续复杂历史回填。

## 已上线结果

- 成本页改为全标的聚合，兼容旧 ticker 链接预选；支持标的、节点、轮次、渠道、模型筛选，KPI、趋势和明细使用同一冻结水位及筛选。
- API 统计细化到 W1R1／W1R2／W2R1 等实际轮次，展示 input/output、缓存读取与创建、实际模型及费用。API 计费与 Codex 订阅调用分开。
- 区分已观测调用、usage 已知／未知、回执实报／规则估价／未计价；未知不会补成零。缓存命中率只使用可配对的已知输入样本，Embedding 不进入缓存分母。
- 版本化 Decimal 计价，保留 USD/CNY 原币，固定换算汇率6.8。OpenRouter 实报费用优先，缓存字段不重复累计；补齐保留模型规则及输入专属 Embedding 计价。
- Vertex Claude 的缓存读取、创建及5分钟／1小时字段已适配；Vertex ADC/native streaming 和百炼 GLM 配置为未启用的前置适配。**本轮没有切换生产模型，也没有调用未来模型进行付费验证。**
- 每次刷新仅 context + report 两个成本请求，不按 ticker 扇出；只读桶聚合、精确 SQL 归并、紧凑响应和缓存编译 schema 校验降低读取成本，不扩大现有请求超时。
- 成本贡献保存在 usage 的紧凑快照，避免每次调用增加几十条独立贡献行；保留原有 MVCC、调用身份与准入规则。原始调用账本未修改。

## 实际发布

以下代码均先同步 origin/main，再发布对应服务；仅构建相关后端及 Web 镜像，未整体 build 或切换 scheduler。

| 服务 | 发布提交 | 镜像 SHA256 |
| --- | --- | --- |
| API | 809bd4f43812a82b8fa32108174dc8924b032cc2 | f40c6eb22062d04f312ee5ab7b02ee999f44f6fdfa435fe28d98bc6e3d820f05 |
| Web | f4a77b07a1d93894322e97a365cfb527077bc954 | 957d89d75780efe5dbd9c764a735f78371845f0f2e33bd263fc3ff763b5bc1fd |
| Projector | 9317865a09afdbc432c8b8091536d86a412f089c | c27cce5248eddbdf1531029b991b7ecfc8412619dd53c124a095f253e7b2e3fc |

最终检查：API/Web healthy，Projector running；`/audit/cost` 返回 HTML，auth/config 返回成功，临时回填容器已停止。证据：`eval/cost_audit_implementation_20261021/final-smoke.json`、`deploy-web.txt`、`deploy-leaf.txt`。

迁移后若需回退，Projector 必须保留紧凑成本贡献实现（9317865a 或兼容后续版本），不能回退到旧逐行贡献写入器。

## 验证

- 后端相关完整回归 **304 passed**，前端 **67 passed**，typecheck/build 通过；1262px、1559px 浏览器交互通过，表头对齐且无整页横溢。修正一处测试固定 UTC16点导致视图过期的夹具时钟，产品 TTL 不变。
- 2026-10-20 黄金样本：2,121 次 API 调用，usage 已知2,030、未知91；input 56,346,800、output 2,242,135、缓存读取49,820,161。全局、逐行及逐 ticker 合计精确一致，已知折算费用约 USD2.53423948。

| 轮次 | input token | output token | 缓存读取 | 缓存创建 | 已知费用 |
| --- | ---: | ---: | ---: | ---: | --- |
| W2R1 | 32,845,922 | 537,115 | 30,345,184 | 1,769,343 | USD0.92792950 + CNY1.1958572，折合约 USD1.10379085 |
| W1R2 | 2,218,501 | 805,999 | 1,089,280 | 未在此表补推断值 | CNY3.1885021，折合约 USD0.46889737 |

上述费用是已知调用费用，不能代替缺失 usage 的最终账单。生产对账证据：`verify-leaf-golden.json`；测试：`tests-release-final.xml`。

最终生产数据读取检查沿用2个查询 worker、1秒队列、3秒查询预算：上一交易日报表预热1.315秒，随后串行0.118–0.391秒；4并发全部200，0.218–0.442秒。Overview metrics/tickers 均200，分别0.076／0.232秒。此前全历史样本在优化后4并发也全部200，0.422–1.766秒。使用临时可信测试身份注入真实 QueryRunner 和生产读库，未改变公网认证或服务配置；这不是公网登录端到端测试。证据：`http-final-default.json`、`http-compiled.json`。

## 历史数据与资源边界

按用户最新要求，**停止剩余历史回填**，不追求全历史补齐。已完成的归一化保留，断点保留在 `/data/read/cost-audit-full-9317865a09af.json`；最终一段已提交12,900条，缺原始 runtime 回执369条、冲突0、busy retries0，cursor 为 MU / turn_b56692d5fd354124b686e7897cb500a8。此前分段另有已完成记录，各段不能与黄金样本或试跑简单相加作为覆盖率。证据：`user-stop-history.json`。

历史未归一化、真实缺 usage／原始回执的记录继续显示相应缺口；不伪造缓存字段或费用。生产既有 runtime capture proof 的 closed_end_at 停在2026-09-29、complete=false，保留 SOURCE_GAP。页面显示统计不完整仍可能是这些真实缺口，不能以清除提示作为修复验收条件。

历史迁移产生了临时 MVCC/history 增量，**没有达到最初小于500MiB／10%的磁盘增量目标**。最后一次测量 serving DB 为5,185,544,192字节，迁移前约3,702,759,424字节；数值包含迁移期间数据库增长，不能等同于全部新增当前有效成本数据。现有维护 GC 继续运行；本轮没有强制 vacuum、truncate、复制全库或删除历史来隐藏增长。已停止回填，不再为边缘需求追加清理架构。

未来模型上线仍需真实 Vertex project/ADC、渠道端点、准确模型身份及实际回执核验；本轮适配不等于未来渠道已生产验收。

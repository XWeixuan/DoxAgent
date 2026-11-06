# W1R2 / W2R2：Luna high生产发布

产品提交：`f3f5a7becf2c3ad4ceb6fccd9b209ebd19007e19`，已推送origin/main并同步生产。

## 生效配置

|节点|主调用|推理/服务档位|固定提示词缓存|唯一备用|
|---|---|---|---|---|
|W1R2|OpenAI Platform `gpt-6-luna`|high / fast|原生explicit 30m|OpenRouter Haiku 5.5 high，Google Vertex Global，1h|
|W2R2|OpenAI Platform `gpt-6-luna`|high / fast|原生explicit 30m|OpenRouter Haiku 5.5 high，Google Vertex Global，1h|

新执行包：`e571f4f9b797666ea62cd5881b241324651a1a984441f0d81fbde30add3bc11a`，于北京时间2026-11-07 00:09:35激活。复制激活前执行包，仅更新上述两个round；逐项确认全部prompt、业务Schema、其他assets、W1R1/W2R1/W1R3/W2R3配置不变。

这两个节点不再走DeepSeek主调用或备用；没有删除既有DeepSeek适配，R3现有Sail主调用和Haiku备用保持原状。历史已冻结Case及已入队任务保留原模型manifest，不改写历史回执。

## 适配边界

- 使用OpenAI官方Responses API，原生strict JSON Schema；仅对发送副本递归补齐required，不改本地业务字段、类型或校验器。
- 固定system/prompt在最前，单独设置原生缓存断点；本条消息、事实/Policy详情、动态指令和重试修复说明位于断点后。cache key按稳定前缀生成。
- OpenAI该接口支持30m/12h，采用已验证的30m；不误传Anthropic的1h参数，不缓存不断变化的Case尾部。Haiku备用保持原生1h路径。
- 保留65536输出token上限和300秒请求超时，编排负责重试。认证/余额/限流/超时/断流/不完整或不可消费输出触发现有Haiku备用；取消、租约丢失和未知dispatch不被吞掉。
- 失败回执落库后、绑定备用前发生中断，也能恢复到Haiku，不重复支付原OpenAI请求。不新增dataclass请求字段，避免改变旧dispatch指纹。
- 保存原生输入/缓存读写/输出/推理token、实际模型和service_tier；成本按实际fast/default档及30m/12h缓存写入计算，标记ESTIMATED，不伪装成OpenAI已结算账单。

## 测试与真实验收

两个不重叠回归集：114项transport/主备恢复/历史适配测试；110项Runtime、事实桥接与成本API测试，全部通过。本地原工作区单独复核新增25项通过（与前述有重叠，不累加）。

正式适配器真实调用5次，未向消息总线投递测试消息、未创建交易Case：

|调用|结果|耗时|输入token|缓存读token|
|---|---|---:|---:|---:|
|W1R2样本1，Luna|high/fast，可消费|3.993秒|4038|2638|
|W1R2样本3，Luna|high/fast，可消费|4.419秒|3417|2638|
|W2R2样本1，Luna|high/fast，可消费|12.916秒|8352|0|
|W2R2样本3，Luna|high/fast，可消费|6.012秒|5288|3084|
|W1R2样本1，Haiku备用直接调用|high，可消费|5.902秒|7179|4784|

同节点两份不同Case的固定前缀hash一致。第二份W1输入缓存读取率77.2%，第二份W2为58.3%；不是声称全部动态输入均可命中缓存。所有5次用量均可在正式成本核算函数中得到有效费用。

自动故障切换/中断恢复由上述回归验证；Haiku真实调用证明备用端点当时可用，没有向生产注入故障。

截至北京时间00:13:18，发布后16次自然W1R2调用全部OK，实际为Luna high/fast，16次均有缓存读取，聚合输入读取命中率75.0%、均时2.878秒。16个新Case均使用新执行包。该窗口尚无自然W2R2回执，W2真实调用验收为上表两次；不把配置冻结当成自然W2已执行的证据。

## 发布与配置留存

Backend镜像：`sha256:d531b1a3634d10b37f5cf88029a39536b9783935e5176c13252ef968fe0e525c`。

更新API、Scheduler与成本投影Projector。三容器24次变更模块字节摘要与产品提交一致，restart_count=0；API healthy，另外两角色无Docker健康检查。Scheduler排空60秒后仍有进程，按发布超时终止，随后正常进入恢复机制；没有批量重放历史失败Case。

现有Codex Worker未重启，未注入本轮OpenAI key；W3原生运行时与其他角色配置保持不变。

生产使用`openai_luna_r2_high` profile，凭据仅注入API/Scheduler，保存在现有受限发布配置与台账中；相关私有文件权限600，发布目录700。未将凭据写入Git或公开回执。其他旧角色仍使用兼容的全局profile；实际API/Scheduler覆盖和激活执行包才是本次生效来源，不要仅修改全局.env后遗漏执行包。

公开回执：`artifacts/audits/luna_r2_release_20261107/`。远端私有构建/部署/回滚资料：`/var/lib/doxagent/releases/luna-r2-20261107/`。后续文档提交不改变镜像固定的产品提交。

官方参数依据：[Fast mode](https://developers.openai.com/api/docs/guides/fast-mode)、[OpenAI原生缓存](https://developers.openai.com/api/docs/guides/prompt-caching)。本次没有改prompt，也不将短窗口传输成功解释为投研判断质量提升。

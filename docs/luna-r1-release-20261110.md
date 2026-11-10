# W1R1/W2R1 Luna High Fast 生产发布验收

日期：2026-11-10。用户明确指示“切换并发布，haiku作为fallback”，本次据此上线，保留此前业务漏召回评估，不将其改写为准确率通过。

## 已生效配置

|节点|主调用|推理|服务档位|显式缓存|Fallback|
|---|---|---|---|---|---|
|W1R1|OpenAI Platform / gpt-6-luna|high|fast|12h，固定提示词+冻结索引+完成provisional分块|OpenRouter / Haiku 5.5 high / google-vertex/global / 1h|
|W2R1|OpenAI Platform / gpt-6-luna|high|fast|12h，固定提示词+完整Policy视图|OpenRouter / Haiku 5.5 high / google-vertex/global / 1h|

W1R1超时120秒、W2R1超时180秒；W1 provisional分块5条。原本Haiku的参数及缓存布局保留为单层fallback。延用每次尝试单独回执、持久化切换和中断恢复。结构合法的业务漏召回不会触发fallback。

R2/R3、提示词、业务Schema、交易规则未变，历史已冻结Case不改写。已对比发布前与最终active execution：仅两个R1及对应备用改变，唯一变动asset为runtime_models.json，prompts/contracts和其他模型字段均完全相同。

## 代码与镜像

- 实现提交：`9376a8de56b9ed2f3698dedef027e40b081bf475`，已推送origin/main，远端仓库已同步。
- profile：`openai_luna_r1_r2_high`，在API、Scheduler发布overlay中生效；全局.env.v2及其他角色不改，避免旧角色不识别新profile。
- 远端API的service.py及Scheduler的settings.py原有角色差异保留。用git中已提交的R1-only补丁作用于各自当前镜像提取的文件，检查补丁、编译后构建新镜像；仅新增5个相关文件的镜像层。没有向运行容器热拷应用源码，也未覆盖既有角色修复。
- 逐文件hash已与各角色构建的after清单核对。

- doxagent-v2-v2-api-1: `sha256:7802d8e89eb06be1026a6f2334a5af5a28c972f9042b8ea4cab4cb11034513fd`，health=healthy，restarts=0。
- doxagent-v2-v2-scheduler-1: `sha256:e1afe54cbde3e74828be99dd63023567381c0c0d29fc5540515699f49289081b`，health=无独立healthcheck，restarts=0。

其余21个容器的镜像及启动时间完全未变。

## 冻结配置发布

- 切换时间：2026-11-10T14:13:25.510038+00:00（UTC；北京时间约22:13）。
- 原execution：`c00bf6cc8ee31a63e4bb264bf9f85106a70210f9b48d9b4c85d42f54715b07dd`。
- 定向发布execution：`12269981548e85acd0157bd67c203c01115bf2c21122ca870e143a14187a7815`。
- Scheduler启动后正常重冻结的最终execution：`49601a112a4dc8632a40eecf42eb1212bdb3b0772bbc5476db42f8b69d4a3f5c`；再次比较确认仍只改两个R1、模型配置asset，提示词/契约/其他节点不变。
- Docker Compose采用已有受控release状态和完整overlay链，重建仅API/Scheduler；保留旧镜像及私有配置回滚记录。
- 远端changelog原有7384字节未提交追加内容已完整备份并保留，再进行代码fast-forward，没有丢弃其他会话的记录。

## 真实调用验收

API健康后，使用已部署代码分别调用两个R1的主模型和备用模型，共4次成功：

- W1R1 Luna：6556ms，原生返回gpt-6-luna / fast。
- W1R1 Haiku：5970ms。
- W2R1 Luna：8430ms，原生返回gpt-6-luna / fast。
- W2R1 Haiku：10863ms。

这是两个端点的真实成功验证，不是在生产业务Case上故意制造故障；自动fallback/中断恢复已由此前离线测试验证。

截至 2026-11-10T14:15:14.616348+00:00，切换后自然流量已记录 **14次R1调用，全部OK**，均为OpenAI gpt-6-luna、实际fast档位，无该采样窗口的R1失败/备用回退。首次冷写及后续读取均已有真实用量回执。

|ticker|节点|Case|输入token|缓存读取token|缓存写入token|
|---|---|---|---|---|---|
|RKLB|W2R1|case_b09cd991ded946c384d464b486b93cd7|29272|0|29214|
|RKLB|W1R1|case_b09cd991ded946c384d464b486b93cd7|12151|0|12044|
|RKLB|W1R1|case_38dbf96c6a02438da43b017ef6795b1e|13679|12044|0|
|MU|W1R1|case_b9b633b7bdb149caa491e31d7693f0e5|33874|0|31539|
|MU|W2R1|case_b9b633b7bdb149caa491e31d7693f0e5|57658|0|55432|
|INTC|W1R1|case_fa294e520f3046e0bf91f3829d7e0164|29641|0|28562|
|INTC|W1R1|case_ba06cb77f0ee4ee989004c5df844e3ba|29720|28562|0|
|INTC|W2R1|case_fa294e520f3046e0bf91f3829d7e0164|39540|38577|0|
|MU|W1R1|case_02ad6af9c2e34ce6af7b81ac1f91b39a|34021|31539|0|
|INTC|W2R1|case_ba06cb77f0ee4ee989004c5df844e3ba|39619|0|38577|
|MU|W2R1|case_02ad6af9c2e34ce6af7b81ac1f91b39a|57805|55432|0|
|RKLB|W2R1|case_38dbf96c6a02438da43b017ef6795b1e|30800|29214|0|
|INTC|W1R1|case_871e7c7f62c348aead7ed4051516bcd4|31052|28562|0|
|INTC|W2R1|case_871e7c7f62c348aead7ed4051516bcd4|40951|38577|0|

此为技术发布验收，不代替业务准确性验收。此前DDR5 Policy漏召回等证据仍见 `docs/luna-r1-12h-cache-evaluation-20261110.md`。

## 证据与回滚

- 本地：`eval/luna-r1-cache-20261110/`，包含release-activation.json、release-verification.json、release-execution-comparison.json、release-natural.json及构建/发布脚本。
- 远端：`/var/lib/doxagent/releases/luna-r1-20261110/`，包含两个基底/目标镜像、5文件变更前后hash、R1-only.patch、build.json、activation.json、smoke.json、verification.json。
- `manifest.private.json`、`compose.current.json.before.private`、`state.json.before.private`含原部署参数，只保存在远端受限权限目录，不提交仓库或输出内容。
- 回滚应恢复本次两个角色的旧compose配置/镜像，并在当前active仍属于本次发布时恢复原execution；不修改历史Case，不删除volume或业务记录。
- 前一轮按授权清理没有找到可安全退休旧镜像，仅回收约7MB构建缓存。此次轻量镜像构建没有全量依赖重编译；磁盘余量仍需独立持续关注。

此前离线250项通过，原脏checkout新增32项通过；发布阶段没有更改源代码，不重复整组模型评测。

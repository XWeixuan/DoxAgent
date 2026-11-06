# W1 输入异常膨胀紧急修复与发布

产品提交：`3974f98e79a753ce10e9f95e4c719784a78710e1`，已推送 `origin/main`。

## 修复

跨 Event Library 根目录补充事实时，使用当天冻结 Library 的完整 ACTIVE Fact 内容作为基线做差分，不仅比较筛选后 Known Index 中的事件。根目录变化不再导致所有已有事实被重复补入；无关库也不会盲目比较版本号。

已误写入的 `library-bridge:*` 中，属于冻结版已知事实的内容，从新 Case 可见 provisional 列表中过滤。原始记录、ID、已冻结 Case 输入保留；Runtime 原生 provisional、真实新增/实质变化事实、O2 结算后可见性保留。Known Index 内容、顺序和固定缓存前缀不变。没有删库、Schema迁移、历史重放或模型/提示词修改。

固定版本的内容集合在服务内缓存；不引入额外模型调用或逐消息全库重算。

## 验证

受影响路径回归 **86 passed**。覆盖同根与跨分支、只换root不新增、Reference外的旧Fact、独立库版本号、已有错误桥接过滤、原生事实保留、SQLite重启、历史记录不变、索引/提示词冻结及W1R2详情版本兼容。

新镜像挂载生产数据进行只读预检，7个ticker共过滤7365条冻结前桥接事实，全部原生 provisional 保留：

|ticker|原可见条数|修复后条数|provisional字符数变化|
|---|---:|---:|---:|
|MU|2652|135|382821 → 18722|
|INTC|2597|128|358149 → 15939|
|BE|714|66|115620 → 9312|
|RKLB|456|11|61249 → 1552|
|NBIS|856|151|121082 → 20256|
|COHR|556|124|78170 → 18653|
|IONQ|287|138|41233 → 19459|

这些是生产数据只读构造结果，不冒充新模型调用token或长期费用收益。

## 发布

远端仓库快进同步产品提交，使用既有发布构建工具重建 Backend，保留原生Codex运行时、现有凭据、模型与交易配置。

- 镜像：`sha256:3202dc11d0a33dd5f88b20ad2e158e64a0877246bbcdfa647e4d4b758f0bf095`。
- 更新 `v2-api`、`v2-scheduler`，其余角色未重启。
- Scheduler排空60秒后仍未退出，按发布超时终止旧进程；新进程已运行恢复机制。没有重放历史失败Case。
- API于北京时间19:02:26启动并healthy，Scheduler于19:03:10启动；两者restart_count=0。
- 两容器3个修改模块的字节摘要均与产品提交一致；Compose当前配置、发布台账及回滚镜像已记录。
- 19:06的调度健康快照：10ticker分析许可均保持开启，last_successful_tick与last_recovery_tick持续更新，admission_degraded_reason为空。
- 截至19:06:50，观察窗口还没有新建自然Case/模型回执；本次不声称已测得发布后真实token或费用降幅，输入缩减数字来自上述生产数据预检。

回执：`artifacts/audits/w1_bridge_release_20261106/`。远端构建/发布日志、私有回滚台账位于 `/var/lib/doxagent/releases/w1-bridge-20261106/`，凭据不进入Git。

文档提交不改变镜像固定的产品提交。

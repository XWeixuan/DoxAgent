# W3隐藏W2上下文开关

`DOXAGENT_W3_HIDE_W2_CONTEXT=true`启用，false关闭；默认false，生产本次显式设true。作用于Scheduler组装的Trade和Operations任务，同样覆盖双分支。

只改变模型可见投影：隐藏w2及历史w2字段、Policy正文/身份/目标、alignment和case_type；去除W2_LOW_CONFIDENCE以及无法区分Event/Policy来源的RECENT_TRADE标签。保留原始消息、W1、D1/D2、交易方向和独立PIN/WATCH状态，但删除其中的结构化Policy身份。不会扫描/改写自然文本：消息及独立WATCH/PIN、历史W3理由可能自身提到Policy，这不属于重新注入W2判断。后置运营不再拿到由W2关联生成的policy_targets；不提供虚构替代目标，原输出Schema和动作校验不变。

W2内部记录、冻结数据、HIT/RELATED、路由、交易资格及审计历史不变；当前v6 Research仍只读D1/D2，未新增工具权限。开关不改变模型、fast模式、双任务及Prompt/Skill。两分支均使用同一配置。

已启动的Thread历史不可撤回，不能声称旧线程已失忆；新Case独立线程按新配置构造输入。关闭开关并重启Scheduler恢复原路径，不清理历史。

本地验证24项输入/真实Runner组装测试、26项契约和配置回归通过，无真实模型测试。发布采用当前生产镜像的4文件Scheduler增量覆盖及显式环境变量；保留Worker、READY库存与所有业务数据。

## 发布验收

代码c0ef3c576已推送origin/main；远端fetch取得相同提交，保留远端其他会话未提交工作，不强制覆盖工作树。Scheduler基于实际生产镜像增量发布，4文件SHA-256吻合，容器运行正常；北京时间2026-11-25 07:38:53启动，07:38:58首poll覆盖10个ticker。容器内实际DoxAgentSettings读取开关为true，Trade和Operations投影断言均通过，检查窗口未见ERROR/Traceback。Worker不变。正式owner采用协作退出，无需旧版强制排空路径。

该验收证明配置和代码生效，不等同于真实业务准确率提升；未额外发起付费模型评测。自然生产新Case的偏差改善需要积累样本后再评估。关闭时将正式Compose覆盖中的DOXAGENT_W3_HIDE_W2_CONTEXT设false并按同一流程重建Scheduler，不改历史数据。

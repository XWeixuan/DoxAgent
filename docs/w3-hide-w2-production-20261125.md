# W3隐藏W2上下文开关

`DOXAGENT_W3_HIDE_W2_CONTEXT=true`启用，false关闭；默认false，生产本次显式设true。作用于Scheduler组装的Trade和Operations任务，同样覆盖双分支。

只改变模型可见投影：隐藏w2及历史w2字段、Policy正文/身份/目标、alignment和case_type；去除W2_LOW_CONFIDENCE以及无法区分Event/Policy来源的RECENT_TRADE标签。保留原始消息、W1、D1/D2、交易方向和独立PIN/WATCH状态，但删除其中的结构化Policy身份。不会扫描/改写自然文本：消息及独立WATCH/PIN、历史W3理由可能自身提到Policy，这不属于重新注入W2判断。后置运营不再拿到由W2关联生成的policy_targets；不提供虚构替代目标，原输出Schema和动作校验不变。

W2内部记录、冻结数据、HIT/RELATED、路由、交易资格及审计历史不变；当前v6 Research仍只读D1/D2，未新增工具权限。开关不改变模型、fast模式、双任务及Prompt/Skill。两分支均使用同一配置。

已启动的Thread历史不可撤回，不能声称旧线程已失忆；新Case独立线程按新配置构造输入。关闭开关并重启Scheduler恢复原路径，不清理历史。

本地验证24项输入/真实Runner组装测试、26项契约和配置回归通过，无真实模型测试。发布采用当前生产镜像的4文件Scheduler增量覆盖及显式环境变量；保留Worker、READY库存与所有业务数据。

# W3 后置运营模型切换发布记录

2026-11-12 02:49:27（北京时间）完成发布。

- origin/main提交：844fc1bdd5abf84bd8bcec747205a7bb3f385164。
- 后置运营：无论Trade原profile为gpt-6.1-sol或gpt-6-luna，独立选择gpt-6-luna、effort=max；Worker明确设置service_tier=default、features.fast_mode=false，覆盖线程继承的Fast设置。
- Trade：保留原冻结模型/档位；Luna继续service_tier=fast、features.fast_mode=true；Sol继续default。
- 不改写冻结Trade profile，不修改Prompt、Schema、路由、工具权限或交易规则；不重跑已成功Job。
- 定向回归21项通过，覆盖HIT/RELATED/OPEN实际三阶段请求、运营模型独立性和Worker各阶段Fast配置；新镜像源码哈希检查通过，生产容器内profile探测通过。未额外发起收费的模拟Case。
- 等待自然空闲窗口后按正式发布锁与组件清单替换，未主动取消在途Job。Worker healthy，Scheduler running。
- Scheduler镜像：sha256:42867c5a56828f619669185f2f65c6b7c6f97f90f3c806a36a8c1bf4b36ed910。
- Worker镜像：sha256:e8dcf289ec9d965f27a82844b720496c84318fe811945fb7d8034b3693a63224。
- 正式私有发布回执：/var/lib/doxagent/releases/w3-latency-844fc1bdd5ab/release.json；配置及密钥未导出。
- 原工作区存在其他会话未提交的changelog/tests修改，予以保留；本轮在D:/DoxW3OpsModel20261112隔离分支完成并推送origin/main。远端main已快进，远端本地changelog追加记录已保留。

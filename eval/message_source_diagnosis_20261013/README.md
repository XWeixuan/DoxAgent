# 2026-10-13 消息源与桌面维护只读取证

时间：UTC10:04–10:20 / 北京时间18:04–18:20。关联方案：`dev_plan/workflow_v2/message_sources_and_login_maintenance_root_cause_and_repair_plan_20261013.md`。

全部现场操作为状态、日志、SQLite mode=ro、TCP/RFB握手、有限HTTPS或Mihomo GET delay测试。没有发出login-open/close/recover、选代理、改Registry、重启或删除命令；host.json中的login-open错误属于既有现场日志。

- snapshot.py分别在Bus/Site容器运行；结果bus.json、site.json。
- details.py在Site容器运行；固定listener同一HTTPS端点对照及Registry只读状态，结果details.json。
- host_details.py在宿主机root只读运行；截取已有异常栈，不读取网站密码、Cookie或maintenance token，结果host.json。
- config_provenance.py比较实际bind配置与宿主机文件，凭据只在内存，输出hash/节点计数等，结果config-provenance.json。
- clash_live.py从Clash网络命名空间访问其loopback控制接口；只GET状态和有限delay探测，不切selector，结果clash-live.json。
- clash.py是前置宿主机对照，controller仅监听容器loopback，宿主机Connection refused不是服务失效证据；以clash-live.json为准，避免重复错误推论。
- read_projection.py在API容器读取Read与Bus当前坐标，结果read-projection.json；采样非原子，4事件差不代表永久零延迟。

额外核实：桌面用户通过正常受限sudo调用bridge list成功、session=null、vnc_ready=true；Site共享namespace内5910及5920–5925均返回RFB003.008。直接以ubuntu身份调用bridge得到CALLER_DENIED是预期安全限制，不是桌面权限故障。

运行文件、镜像与磁盘/生命周期修复的比对结论已记录于方案。保存的JSON不含管理令牌、代理密码或订阅URL。脚本会进行有限网络探测，不应作高频轮询执行。

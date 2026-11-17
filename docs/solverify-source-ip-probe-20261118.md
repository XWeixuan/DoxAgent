# Solverify 一次性 TCP 来源探测与正常代理验收（2026-11-18）

## 实际完成

服务器产品类型以用户确认的腾讯云 Lighthouse 为准。之前仅凭 metadata 实例 ID 推断 CVM 不成立；没有继续要求不可用的 VPC 流日志。

1. 在宿主机18792启动一次性accept/close监听。非root用户、900秒TTL、32MiB MemoryMax、5% CPU、最多64个唯一公网IP；不recv、不发送HTTP、不转发，不读取或留存认证数据。只将来源IP写入0600私有文件。
2. 用户临时放行TCP18792后提交一笔明确标注TCP_SOURCE_DISCOVERY_ONLY的Solverify datadome网络fixture任务。它没有真实验证码参数，结果禁止应用到任何Chrome；目的只是在相同任务类型下触发真实代理连接。
3. UTC2026-11-17 19:16:40提交后短窗，记录到158.180.12.56。随即停止systemd临时监听，ss确认18792无LISTEN，用户确认已撤销临时防火墙规则。
4. 用户新增18791的158.180.12.56/32，保留138.201.188.166/32。宿主机Squid新增同样精确来源，仍要求Basic认证、仅CONNECT/443、限定目标域名、拒绝私网目标、never_direct且无TLS MITM。
5. 仅将reuters-backup2的私有代理绑定更新为它实际使用的jp-standard-6（历史别名，德国205.198.126.114），Main/Backup绑定逐项相同。Squid parse通过后HUP热载，没有重启代理、Site Access、Supervisor或Chrome。

来源158.180.12.56是本次提交短窗的连接样本，不是平台承诺的永久、完整worker IP集合；后续仍需外部受认证CONNECT验证归属与可达性，不扩展/24或开放全网代理。

## 实测结果与费用

| 测试 | 结果 | 边界 |
|---|---|---|
| 来源探测任务006d90b3-1040-4c52-a83d-6d81b232e9ef | ERROR_TASK_FAILED，最终cost=0 | 18792故意不提供代理，失败符合探测性质；不是实际验证码失败样本 |
| 服务器内认证代理预检 | CONNECT TCP_TUNNEL/200，api.ipify.org返回205.198.126.114 | 父代理172.18.0.5:18080，与候选Profile登记出口相同；这不是外部worker链路证明 |
| 外部18791网络fixture任务4775483f-fb0d-44ca-b907-b28cef26d94e | ERROR_TASK_FAILED，最终cost=0 | 没有真实Challenge参数，不能据此判断供应商解码质量或网站验证拒绝 |
| 18791外部受认证转发 | **尚未证实** | 任务窗口的Squid证据仅见内部预检，没有关联的外部worker CONNECT；一次30秒SYN-only观察无捕获，不是完整提交时窗抓包 |
| 账户收尾 | balance=0.05 | 两笔处理中显示0.0006是预期费用，终态均为0；未追加第三笔任务 |
| 浏览器/生产 | 未写Cookie、未登录、未换Main、未改旧Backup、未改变生产Provider选择 | 继续保留2Captcha，不能声称Solverify已经接替或恢复Reuters |

上述两笔网络fixture不进入生产恢复/solver job统计，分别保留私有幂等回执（O_EXCL创建，避免重复提交）及本地脱敏证据；核对平台账单时应单列，不混入真实Challenge解码成功率。即使网络fixture返回结果，也禁止Cookie应用。

## 代码与实际配置

- 源码提交d2777c32a：scripts/ops/solver_source_ip_probe.py、deploy/configure-datadome.py、tests/test_solver_source_proxy.py，changelog已记录。
- squid_config支持显式solver_source_ips，只接收公网单IP并生成/32；默认2Captcha来源、内部网段及认证/目标限制保留。bootstrap保留现有额外身份绑定和私有Provider字段，避免重跑抹掉第三身份或Solverify配置。
- 7项相关配置回归通过。宿主机使用的配置生成器SHA256与该提交一致。无需重建容器，持久squid.conf和credentials配置已保存。
- 18792临时监听已停止，云规则由用户确认删除；没有新增长期公开入口。IP证据保留在/var/lib/doxagent/solver-source-probe/20261118-source-ips.txt及本地source-ips.txt。
- 原配置私有备份：credentials/datadome-solver.pre-source-probe-20261118.json，0600。回退来源时仅移除新增IP并重新生成/parse/HUP，保留所有现有身份绑定与认证；不得重跑旧版bootstrap覆盖新配置。

## 下一次有效验收

目前三个Reuters恢复记录均已RESOLVED，最后候选Device Check自行通过，没有现成的有效人工Challenge；没有主动制造挑战或重放失效的原页参数。

下一次自然出现真实可处理DataDome时，使用候选Profile的一笔任务，配合提交时窗的轻量SYN观察与现有Squid CONNECT账：

- 无到达服务器的SYN：仍需区分平台未发起连接、worker来源变化和云端过滤，不根据ERROR_TASK_FAILED直接归因。
- SYN到达但没有认证隧道：定位TCP/HTTP代理握手、认证及ACL，以实际回执判断。
- 外部CONNECT/200且父代理正确：再追踪真实原页解除、Provider Cookie写入、首次搜索API实际携带、搜索200与FULL正文；APPLIED不是完成。

不因本轮网络fixture失败而改变Chrome UA、清Profile、扩大目标域名或盲目增加重试。没有新的有效现场时不继续购买、反复守候或切换生产默认Provider。

证据：eval/solverify_tcp_source_probe_20261118/（source-ips.txt、submission.json、discovery-result.json、normal-submission.json、normal-result-final.json、proxy-configuration.json、proxy-acceptance-after-terminal.json、account-final.json）。

官方契约：[DataDome](https://solverify.net/docs/datadome)、[结果状态和费用语义](https://solverify.net/docs/get-task-result)。

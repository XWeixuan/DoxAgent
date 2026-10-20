# 企业 IR / Press Release 开发与生产回执

日期：2026-10-20，北京时间。执行依据：[已批准开发方案](corporate-ir-press-release-development-plan-20261020.md)。

## 实现

外部来源 `corporate_ir_press_releases`，`builtin:corporate_ir@1`，API / by_ticker。九 ticker 对应十二内部入口：INTC 1、MU 2、BE 1、COHR 2、NBIS 1、CEG 1、RKLB 2、SNOW 1、ONDS 1。正常路径五 RSS、一 JSON、六 SSR HTML；无新增 Profile、身份或常驻页面。

`message_bus_v2/corporate_ir/` 实现 typed catalog、独立endpoint状态、公开transport/parser、7×24 coordinator、有界历史分页、公开正文与惰性双Context池。HTTP六并发/每host二并发；每入口单inflight。浏览器错误先确认控制器是否失活，不因单页超时重启健康sibling，停止时只操作本pool的进程句柄。

内容入队、endpoint cursor/validator及binding聚合在同一Owner事务内提交；嵌套既有repository操作借用该事务，存储失败整笔回滚。取消仍遵守StorageLane admitted commit收尾。精确时间/DATE、过期准入、bootstrap和既有去重保留。一个endpoint失败不覆盖另一个endpoint状态。

原始feed全文按受控recipe和既有质量合同验证，其他详情通过公开HTTP完成，不经Site Access的自动publisher身份分配；正文作业冻结endpoint spec/revision。CEG正文仅取文章列，去除告警订阅表单。NBIS模板午夜和RKLB坏JSON-LD不产生伪精确时间。

endpoint状态以独立复合ID纳入CDC及快照重建，UI从同事务更新的poll_state读取内部状态。沿用当前V4 Read有界history及source transport/native retention；没有另建无界成功日志或重复业务历史。前端在原消息源配置页显示各内部入口，复用Table组件。

## 配置与迁移

`scripts/ops/configure_corporate_ir_sources.py` 默认SQLite mode=ro dry-run；`--apply` 通过既有Bus Owner service、一次写事务完成注册/绑定/精确重叠迁移。重复执行不提升不必要revision。

迁移前已读取生产九ticker，均为running。旧BE绑定有PR/presentation/SEC三路，保留后两路；旧INTC PR移入新源后仅停用其空绑定。ONDS旧`/press-releases/rss`和新`/rss`逐项比较了20篇编号/标题/时间，旧路径作为已证实别名迁移。其他ticker现有非同URL官方PR的通用RSS保持。

容器内应用命令使用服务器已同步的精确提交文件，经stdin运行，避免假定运维脚本已打进应用镜像：

```sh
sudo docker exec -i doxagent-v2-v2-message-bus-1 python - --apply < scripts/ops/configure_corporate_ir_sources.py
```

## 验证

- 首次合并定向后端回归：156 passed，覆盖IR、Bus、admission、存储、正文、去重、orchestration及API配置/新鲜度。
- Read CDC/独立ID/快照映射/技术history回收及现有Read生命周期回归另有定向结果，最终结果记录于下方发布验收。
- 前端typecheck、正式build通过；2项消息展示单测通过；1262px与1559px两项Playwright浏览器检查通过，包含失败与成功并列、无整页横向溢出、展开/收起和外链。
- 真实公开正文样本十一篇均达到FULL；ONDS/RKLB的RSS原生全文无需二次详情请求；CEG改用正文列后仍FULL且不含订阅表单。
- 相关结果与生产快照放在`eval/corporate_ir_implementation_20261020/`。不相加重复运行的测试数量。

## 发布与自然验收

首次生产HTTP canary识别了真实客户端差异：Micron IR/Bloom以及部分Rocket Lab详情在httpx下返回Cloudflare `cf-mitigated: challenge`，相同URL/出口/固定UA用标准库公共HTTP返回200；移除Accept-Language并未解决。按实测给四个endpoint recipe增加精确`urllib_hosts`（Micron newsroom只对最终IR host生效），不引入Chrome、代理轮换或Cookie。同步请求在有限HTTP槽中offload，取消等待真实线程结束，重复取消不提前归还permit。Owner工具仅升级原始revision1且参数为空的已知recipe，保留人工配置。

本节在完成实际发布、九ticker配置及不少于30分钟自然观察后填写。当前此初稿仅记录已完成的本地实现和测试，不代表生产已经启用。

发布组件仅限实际消费者：Message Bus、Content Enrichment、API、Projector、Web；保持Chrome Supervisor、Site Access、既有Profile/原页、维护Worker开关。使用canonical storage_release工具并保留current/previous。

必须分别报告：提交/镜像/源码指纹；9 binding/12 endpoint启用及旧feed迁移；自然轮询的状态/间隔/错误；正文质量；是否有合时效新公告。缺新公告时，新raw→FULL→stream自然实例明确待验证，不用历史公告补造业务验收。

# 全局资源精益修复实施记录

依据：`host-resource-lean-architecture-and-repair-plan-20261029.md`（历史坏例复审版）。

本轮范围：R0—R8逐包实施、整体review、正式build发布，启动前清退用户授权的历史负担，上线后90分钟低频观察。24/72小时稳定性仍是后续验收边界，不能以90分钟代替。

|包|状态|证据/结果|
|---|---|---|
|R0|本地/发布前取证完成|05:50五角色仍停止；随后发布前PSI没有持续内存/I/O停顿，磁盘87GiB可用；冻结容器版本、配置、数据挂载及其它会话改动指纹|
|R1|实现、回归完成，待生产验证|普通publisher恢复转按需；删除全scope冷canary、重复旧恢复迁移；贯通真实请求半开，保留人工/付费/必要source恢复|
|R2|实现、真实本地Chromium验证完成|必要身份独立worker/Node不合并；显式public_context或public_identity，零需求退出；匿名Profile一旦人工证明FULL则转冷Profile复用|
|R3|实现、回归完成，待生产成本验证|物理关闭成功才归还槽；失败保留占用并可重试；页面池按真实并发/下次周期保留，不通用冻结；Reuters业务页继续不冻结；减少Barron's重复整DOM质量解析|
|R4|实现、回归完成，待启动清理|兼容生产者进入live；一次旧任务迁移后领取不再查旧表；provider期限不截180秒；Raw/Standard准备与短提交分离；不可变result有限缓存，ticker元数据独立|
|R5|实现、读/搜索回归完成|展开前跳过无Serving消费者的effect/namespace和task inputs/receipt；正文搜索每digest一次，历史/权限/Unicode保留；派生索引按有界cursor维护|
|R6|实现、Worker回归完成，待生产切换|owner一次归属快照、CLOSED退出恢复；启动PID取新快照，发信号保留启动时刻核验；只维护已退休Profile；宿主旧Reuters消费者待最终核对|
|R7|容量机制/受控回归完成，真实成本待观察|生产全局8页、BODY2/borrow=false，已是8核配置；删除borrow额外魔数。现有20ticker共享正文/ARTICLE_ONCE/事务内0Native I/O回归通过，不把该回放冒充三站20ticker/30s实网证明|
|R8|待正式build/发布|正式角色重建、离线历史清理、低频90分钟观察，结果追加下文|

清理边界：清退旧文例行复查、已完成/重复工作、过期无交付义务任务与失效历史补扫；保留唯一发布身份、完整正文和checkpoint。尚未交付的有效消息和真实内容更新先对账，不能按文章年龄一刀切删除。清理收据记录原因和数量，不伪造成功。

## 实现与整体review

1. 不恢复旧共享driver故障域。普通publisher的共享仅限其匿名执行分区；必要source和凭证身份保留原Profile、启动方式、出口与独立driver。公开站并不被自动认定为无状态：已验证匿名身份保留冷Profile；既有非聚合消息源对应site不迁移。
2. 公共分区真实双Context测试使用本机受控Chromium：同一Browser、Cookie隔离，最后任务结束后Browser断开、运行域为空，约0.61—1.03秒。它是本地生命周期证明，不是公网站点耗时或Linux生产资源证明。清理错误不丢弃已下载结果；只针对精确PID/启动时刻/公共Profile参数收尾，未确认则保留占用，不重复创建另一套环境。
3. 原有HTTP publisher隔离池、live prepare/commit、结果spool、配置周期和独立频道合同继续使用，不再增加一套队列或压力门禁。本轮没有改出口、降低FULL标准、关闭必要频道或修改W1/W2/W3模型/并发/ready契约。
4. `lean_startup`只在正式迁移角色执行：先安装小型Read派生表/索引；退旧复查并保留真实更新检查责任；旧BODY按原捕获准入迁移有效续作、保留not_before；清退确已过期/不可读旧任务且留收据；离线接纳持久列表delta，已接纳仅ack，过期空delta不重新BODY；完成已有Raw后链路；只清理SWEEP/SOURCE_SWEEP历史任务。不全库备份/VACUUM，不清Profile/业务内容。
5. 旧Raw/Standard兼容收尾拆出prepare和标量commit，正常live路径不再回到旧job/raw恢复轮询。历史兼容方法仍保留供离线迁移及已有接口使用，不能把它们的全部旧事务实现误称为已删除；本次生产热路径退出旧表以迁移完成标记证明。
6. 网络guard原本已对停止容器返回DEFERRED，本轮保留这一行为，并修正重绑时丢失正式Compose覆盖文件的问题。没有新增“看到PSI就停业务”的策略。
7. Worker代码没有现成的无损drain接口；不能把方案文字当成已实现协议。发布前核对真实active/cold/queue，在实际空闲窗口替换，若仍有在途则等待其完成，不能强制取消W任务。仅管理发布owner切换，不新增日常业务限制。
8. 本地另有未提交交易/前端及W3提示词改动，属于其它会话工作；保留文件及指纹，不将未review内容夹带进本功能镜像。远端唯一server Compose私有差异为Collector的host-gateway，与本地一致，纳入核对。

## 回归记录（计数有交集，不相加为唯一测试数）

- Identity/Recovery/Reuters/Storage/Pipeline/Read第一组124项通过。
- Message Bus/live/BODY/Worker/W3和生产读契约综合141项通过（269秒）。
- 周期兑现、读模型、维护与新精益测试57项通过；最终安全专项31项通过。
- 新精益测试覆盖清理失败保留槽、共享公共执行/退出、成功下载不被清理错误覆盖、provider600秒下界、owner扫描、搜索历史与Unicode、原捕获有效续作、Site版本化迁移、不可变正文复用。
- 综合回归发现测试冻结时钟在懒加载模块中泄漏，修正测试导入边界后全组通过；另更新已过期的源清单、pressure门禁及expire_runs mock契约，没有为迁就旧测试恢复已取消的硬阻断。
- 证据：`eval/host-resource-redesign-20261029/implementation/`；生产发布、清理收据和90分钟窗口尚待追加，当前不宣称性能目标或长期稳定性达标。

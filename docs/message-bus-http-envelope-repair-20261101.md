# 消息总线统计仍为空：HTTP 外层响应修复

日期：2026-11-01，北京时间。

## 根因

用户截图的五个新指标已经加载，浏览器提示 `返回数据不符合 V2 契约`，请求编号错误显示为 undefined。正式 dashboard 的业务数据本体通过了 Python BusDashboard 校验，但 HTTP 外层 Resource 错用了 `data.coverage`：它实际是轮询覆盖率 `{expected,succeeded,ratio}`，外层要求的是数据完整性 Coverage `{state,reasons,...}`。接口返回200，但前端严格校验拒绝整份响应，KPI与来源卡片因此都没有数据。

上轮只验证了生产计算函数与业务 DTO；浏览器 fixture 又直接替换 api.request，绕过了真实 HTTP 封装及前端 validator。该验收缺口已用能先复现失败的完整 HTTP 回归补齐。

## 并行发布核对

排查时生产 release 登记版本为 `55eb29e3`（交易配置/弹窗修复与消息总线的正式合并），但 API/Web 的镜像内 revision/环境标签仍是旧的 `466edcc3` / `b4720b2c`。镜像实际ID也已更换。因而不能仅凭环境标签判断是否被覆盖。

已将**实际在生产运行的已提交版本**55eb29e3合并到本轮隔离分支，保留另一会话已部署的交易配置、报价和弹窗修复；没有引入共享目录未提交修改。后续从统一 Git 提交构建 API/Web，先进入 origin/main，再部署，让新的镜像、环境标签与 release 记录一致。Collector/Bus/BODY/Projector等角色无需为本次外层响应修复重建。

## 最小修改

- Dashboard endpoint 显式传递 HTTP Resource Coverage；业务轮询覆盖率继续保留在内部 data.coverage，不放宽前端或后端校验。
- 前端响应校验失败保留服务端 request_id；没有编号时不输出 undefined。
- 首次请求失败不声称“已保留上次结果”，也不把读取失败解释为“没有已绑定消息源”。
- 浏览器 fixture 改为模拟 fetch HTTP 响应，使用真实 ApiClient 与 wire validator；继续验证分页/续期/滚动保留。

## 回归

- FastAPI完整GET回归：修复前外层Coverage必现校验失败，修复后通过。
- Python真实HTTP响应样本经过前端ApiClient接受；旧错误响应被拒绝且保留请求编号。
- 相关后端20项通过；前端69项通过；消息总线3项浏览器测试（含200错误响应与恢复、两种宽度分钟更新/续期）及交易配置2项浏览器回归通过。
- Typecheck和生产构建通过后仅发布API/Web。最终Git提交、运行版本及用户已登录生产页面验证在部署后追加。

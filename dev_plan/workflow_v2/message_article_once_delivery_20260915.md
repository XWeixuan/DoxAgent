# 同稿仅发布一次：本地交付

## 行为调整

- 保留现有保守稿件身份匹配：可信文章 URL、稳定稿件 ID、验证别名及完整正文证据。标题单独相同不作为硬去重，通用链接、身份冲突和 ticker 边界不改变。
- 确认同稿后，`CONTENT_SUPPLEMENT` / `BUSINESS_UPDATE` 只更新 canonical Raw 与既有 Standard/待发布 buffer 内容，不新增消息流或 Runtime Case。内容修订与首次业务发布版本分离。
- 新增 append-only `message_content_revisions` 保存内容快照，观察/原始 payload/来源关联继续留痕。空正文不覆盖已有正文；明确出版商修订保留旧正文证据。旧 Raw/Stream/Runtime ID、hash 和已发布历史不清理、不重放。
- REALTIME task 第一次领取时，只读加载所属 Stream 的最新成员内容，在 Runtime 领取事务内冻结 source snapshot 与内容修订号。首次领取失败回滚，资源预约释放；重试/恢复不再刷新。CLOSED roster 保持冻结输入。
- 此规则明确取消同稿自动内容更新再处理：运行中和已完成 Case 不因后续正文补充或真实修订而自动重跑。因此后补新事实可能不进入此前已完成的模型处理，这是本次确认的行为取舍。

## 验证与边界

- Message Bus 与去重定向回归 39 项通过；原有 lease 恢复/旧积压边界包含在 21 项复验中；新增并发覆盖用例后，去重及核心版本行为再次复验 22 项通过。
- 变更模块静态检查通过；journal 原有长 SQL 行保持不动，另做非行长检查。没有模型调用、远端请求或订单测试。
- 仅本地修改，未 commit/push/deploy，未修改 O1/O2/O3 prompt/skill。保留同一工作区其他对话的资源预算、Yahoo transport 及 O3 改动。
- 历史 Kioxia 两版卡片不删除；新规则只防止后续新增同稿版本发布。前端读取既有 Standard 时可显示最新正文，实际线上刷新表现待部署后检查。

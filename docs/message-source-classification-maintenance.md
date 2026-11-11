# 消息源分类维护说明

2026-11-12。此目录只读展示，分类更新不改变采集、分发或Ticker订阅。

## 维护入口

唯一分类配置：`src/doxagent/message_bus_v2/source_catalog.json`。目录API读取该文件；不通过运行日志、当前成功路径、SourceKind或已订阅Ticker自动推断标签。

源开发/修改会话在同一提交中同步检查以下内容：

| 修改内容 | 必须同步 |
| --- | --- |
| 新增消息源 | 用正式source_id添加名称以外的用途说明、公共入口补充、列表/正文主备方式、适用范围与L1；核对登记与分类覆盖测试 |
| 列表采集从RSS改HTTP、浏览器迁移、增加备用/赛马 | 更新listing.primary/fallback；并行路径属于primary，实际故障没有改变配置时不改静态标签 |
| 正文策略、Identity类型、原站路由或跳过补全改变 | 更新body.primary/fallback；外部Chrome经Playwright控制仍标Chrome/CDP，不按客户端库名分类 |
| by ticker/search/distribution/massive distribution改变 | 修改现有SourceDefinition.acquisition_mode，并核对目录映射及说明；不维护第二份分发模式 |
| 扩大/缩小覆盖行业、合并专题源 | 更新applicability和有序sector_ids、用途/入口；第一行业为默认展示区，其他行业仍可筛选 |
| Ticker订阅、启停、测试转正式改变 | 使用既有绑定/源配置流程；目录自动读取这些事实，不手写Ticker标签或在分类文件存enabled |
| 源下线或旧源被合并 | 保留真实登记及启停状态；分类继续能展示旧ID，不因“清理分类”删除业务历史或绑定 |

## 分类规则

- 列表与正文分别维护；主方式与fallback分别保存。RSS包含Atom，不因为下载用HTTP就重复挂HTTP。公开网页/同源JSON列表归HTTP；独立供应商服务归API。
- 浏览器标记只区分托管Playwright与外部Chrome/CDP的实际配置。备用HTTP也不能与主Chrome摆成同级主路径。
- 正文可明确为“随源提供”“按原文站点策略”“不适用”。不要把有RSS摘要当已获得完整正文，也不要让不适用源显示100%正文率。
- GLOBAL表示通用能力面向全部Ticker；它不会自动订阅全部Ticker。实际订阅完全以未删除的TickerSourceBinding为准。
- INDUSTRY使用附件V5的L1-01到L1-13，可多选，不增加同义行业名。不要根据现有订阅Ticker推断行业。
- **L1-13“政策敏感型产业”是行业分类，不是“政府来源/政策消息”的同义词。** DOE、NRC、DARPA、NIST等依实际覆盖产业归类；不能把所有政府源塞进L1-13。
- 多行业数组第一项只决定默认分区，不限制其他行业的适用性；全局源sector_ids为空，不重复列13项。
- 公共URL/模板可以显示；API Key、Cookie、登录态、签名参数及CDP连接地址不放进目录元数据。

## 提交前检查

1. 人工对照现有源配置、实际调用代码与正文/Site配置；不为更新标签触发真实抓取、登录或模型请求。
2. 静态校验source_id/L1引用、主备重叠、空行业、无凭证公共入口及已知源覆盖。新字段同步契约与schema生成根类型。
3. 验证源卡方法标签、分发方式、行业过滤和实际订阅标签；业务采集及分发语义不被展示配置改变。
4. 重要产品代码改动按AGENTS要求追加changelog。发布只更新实际受影响角色；纯展示标签随API镜像发布，不为此重建采集器或重跑历史。

不要开发标签编辑后台、自动分类服务、定时同步、逐次路径遥测或历史标签回填。

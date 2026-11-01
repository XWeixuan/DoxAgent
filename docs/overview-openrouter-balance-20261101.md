# Overview OpenRouter余额

日期：2026-11-01。

在IB Gateway卡片的Live、Paper两行下方新增“OpenRouter余额”，USD两位小数；正常0显示$0.00。后端按账户已购额度减累计用量计算，使用OpenRouter官方[GET /api/v1/credits](https://openrouter.ai/docs/api/api-reference/credits/get-remaining-credits)。已用management key查询远端实际调用key的管理记录，HTTP200确认属于同一账号；未输出、记录或提交任何密钥。

实现保持轻量：独立的已认证GET `/api/doxagent/v2/overview/openrouter-balance`，不依赖交易日或所选期间，不进入SQL查询worker。父API进程共用一次在途请求及5分钟内存缓存，HTTP连接/读取设置短超时。页面每5分钟读取一次；切换期间不会重复查询上游。没有新增数据库表、历史扫描、后台服务或持久化余额。

失去上游响应时保留最后成功余额并显示“旧”；无成功数据时显示读取失败，未配置时明确提示，均不冒充0。前端余额Query独立，失败不影响Gateway或其他KPI。

密钥位于服务器root专用目录 `/var/lib/doxagent/overview-credits/management_key`，文件0600、目录0700。可选部署文件 `deploy/docker-compose.openrouter-balance.yml` 只向API容器挂载只读Docker secret `/run/secrets/openrouter_management_key`；不写入Git、Docker镜像、浏览器包、响应或日志。代码默认读这个路径，本地开发可用OPENROUTER_MANAGEMENT_KEY_FILE指定测试文件。

上线前对齐当时实际生产提交 b36e59ba，保留其他会话已部署的收益审计/7卡片布局等修改；共享工作目录未提交修改不纳入。只构建及发布API/Web，复用标准release登记和Compose基础命令，其他角色不重建。

验证：30项后端定向测试（含余额、Gateway与收益审计），69项前端单测、8项浏览器回归（含1262/1559px余额位置/对齐/溢出和交易配置）、typecheck及生产构建通过。部署后的真实页面与版本验收另行追加。

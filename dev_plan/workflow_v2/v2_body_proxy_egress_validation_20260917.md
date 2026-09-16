# V2 正文与新闻爬虫代理出口验证（2026-09-17）

## 边界与部署

将本机 Clash Verge 的 `Flower_SS.yaml` 订阅配置复制到远端 `/opt/doxagent-egress-clash/config.yaml`（600 权限）。远端运行 `metacubex/mihomo:v1.19.31` 独立容器，固定版本镜像摘要 `sha256:739edd73a352d6beb82fad6790ef9d417a4d6f061584cc3cab1bd4536d1c60e5`，只加入 `doxagent-v2_default` 内部网络，未映射主机端口、未使用 privileged、未启用 TUN、未修改主机默认路由。容器只被显式配置代理 URL 的正文/Yahoo/Reuters 客户端使用。

测试期间 RKLB 初始化 `init-rklb-9631e4071e75470a97313eafbbdc51aa` 仍为 RUNNING / D2。未重启、重建或修改 initialization、message-bus、content-enrichment 及其他业务容器；代理 sidecar 目前不被生产进程引用。

## 节点筛选

远端从订阅识别 90 个 Shadowsocks 节点，逐节点对 `gstatic generate_204` 做 4.5 秒轻量测试：44 个可达，46 个失败。远端结果与本机 GUI 延迟不能互换：香港 0/12、新加坡 0/12、台湾 0/6 可达；日本 10/12、美国 11/12 可达。韩国、德国、荷兰、法国、英国、加拿大等也有可达节点。

从 44 个可达节点中抽取 15 个跨地区/档位节点，对 9 个代表站点做 135 次串行低频矩阵测试，均使用浏览器 UA：

| 站点 | 节点矩阵结果 | 结论 |
|---|---|---|
| TheStreet | 15/15 为 403 | 换出口无改善，更像客户端/WAF 路径问题 |
| 24/7 Wall St. | 13/15 为 200，2 个节点连接失败 | 明确为出口可改善；真实正文解析 5,312 字符 |
| Yahoo Finance 文章 | 15/15 为 200 | 明确为远端原 IP 风控；浏览器 UA 必需 |
| InvestorHub | 15/15 为 200 | 明确为出口可改善；真实正文解析 1,881 字符 |
| Fool | 15/15 为 200 | 出口普遍可用，个别 curl_cffi TLS 失败可重试/故障转移 |
| Reuters 搜索 | 荷兰 NL2 为 200/232,681 字节；13 个为 401，1 个连接失败 | 强节点依赖，单独走荷兰节点 |
| Barchart | 14 个为 202 空响应，韩国为 403 | 换 IP 不够，仍需浏览器执行；不能把 202 当正文成功 |
| Investopedia | 12 个为 402、3 个为 403 | 仅换节点不直接得到普通 HTTP 200；curl_cffi Chrome 指纹经日本节点可解析 1,853 字符 |
| Benzinga | 15/15 curl 为 403 | curl 客户端无改善；curl_cffi Chrome 指纹经日本节点真实解析 6,434 字符，说明是“出口 + TLS/UA”组合 |

## 真实正文管线

使用现有 `ArticlePipeline`、`curl_cffi AsyncSession(impersonate=chrome)` 和显式内部代理复测原 15 条代表样本，不写业务库：

- 日本实验节点成功 8/15，较原远端 4/15 增加 24/7、InvestorHub、Benzinga、Investopedia、Finnhub 转发页、Axios/Yahoo 等；原 Yahoo 与 Investment Monitor 继续成功。Barchart 的 202 空响应未误标成功，TheStreet 继续 403。
- 荷兰节点成功 7/15；Reuters 适合荷兰，但正文综合表现不如日本。日本节点的个别 Fool/Yahoo TLS 失败在同地区其他节点/后续请求恢复，需健康故障转移而非永久失败。
- 代理后的 Yahoo Search API 与 Reuters 搜索使用 curl_cffi Chrome 指纹均实测 HTTP 200。无浏览器 UA 的 Yahoo 控制请求返回 429，证明节点成功不能脱离真实客户端配置来判断。

## 推荐路由与代码

Mihomo 使用 rule 模式：`reuters.com` 固定荷兰标准 2；其他代理流量进入 `DoxAgent-Egress` fallback 组，按日本标准 6、日本高级 1、美国标准 5、荷兰标准 2 的顺序做 5 分钟健康故障转移。配置语法已用 Mihomo `-t` 验证。当前 sidecar running、restart=0、OOM=false。

代码新增单一 `DOXAGENT_CRAWLER_EGRESS_PROXY_URL`，只注入：

1. 正文 `curl_cffi` 会话及非账号浏览器 context；
2. Yahoo 专用长寿命会话；
3. Reuters/Yahoo Crawler Plane 的独立 Playwright context。

连接 operator CDP 时，账号域名继续使用已登录默认 context；公开站点创建带代理的临时 context，关闭 worker 时只关闭自己创建的 context，不关闭用户 Chrome。其他 API、Codex、数据库、初始化和主机流量不读取该变量。

79 项相关测试通过，Ruff 通过；Mihomo 配置、节点延迟、站点矩阵和正文结果分别留存在 `exports/body_delivery_20260915/proxy_all_latency.json`、`proxy_site_matrix.json`、`body-free-proxy-results/`、`body-free-proxy-results-nl/`。原始订阅含凭据，不进入 Git。

## 激活约束

RKLB 初始化尚在运行，因此本轮先完成 sidecar、代码和隔离实测，不重启 message-bus/content-enrichment。生产激活必须等该初始化结束后，再设置 `DOXAGENT_CRAWLER_EGRESS_PROXY_URL=http://doxagent-egress-clash:7893`，仅重建/重启 message-bus 与 content-enrichment，随后检查自然 Yahoo/Reuters 轮询、正文成功率、429 冷却和代理 fallback 状态。不得把 sidecar 存活等同于正文链路已切换。

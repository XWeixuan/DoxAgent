# W3 Luna-only Fast 发布记录

日期：2026-11-11（北京时间）。源代码提交 `17c8e4b6118a78ee61d26f332514fb1016c065ab` 已推送 origin/main。

## 生效范围

- W3 实际执行模型为 `gpt-6-luna` 时使用 `service_tier=fast`、`features.fast_mode=true`；max不变。
- `gpt-6.1-sol` / medium 保持 `default`、Fast关闭；旧Sol也保持default。Policy HIT/RELATED与OPEN的模型选择、冻结profile均不改写。
- 同一规则覆盖模板预热、Fork/Resume、交易研判与后续运营；W1/W2、D3及其他节点没有新增档位设置。提示词、schema、交易动作与历史Case不改动。

## 测试与发布

85项SDK/权限回归通过，覆盖三模型与四种阶段输入，断言实际发送的SDK配置。使用从origin/main创建的独立worktree提交，原开发目录的其他未提交工作未混入。

生产Worker现用sdk_runtime.py与更新前origin/main一致；基于实际运行镜像仅替换该文件，保留包括W3 Resume不回传历史在内的已发布修复。没有重建完整main镜像，也未更新Scheduler或其他服务。

Worker于2026-11-11 04:48:28完成切换并健康，Compose持久配置与host release state已同步。镜像：`sha256:14121db49ff670bcad9d015c3c090b17c8aac2c1fce61d4e4c281196c6e15a39`；文件SHA256：`0eb359c4e79531fb8d7b923a923c22e9d975978040eff810851d01e75d48ad2b`。远端发布回执：`/var/lib/doxagent/releases/w3-luna-fast-17c8e4b6118a/release.json`，reconcile errors为空。

## 生效证据

部署代码的零模型调用配置探针确认：Luna出站fast/fast_mode=true，Sol出站default/fast_mode=false。探针只做配置Fork，没有启动模型turn；不能冒充已完成的Sol实盘模型turn。

发布后自然生产Case `case_3a5d9927a42c434cb15ec96e17ad38d8`，thread `01a1c738-e0b8-7050-a6ea-be7b6f6391f3`，原生设置为`gpt-6-luna / max / priority`。交易阶段于04:50:36完成COMMITTED，运营阶段于04:51:53完成COMMITTED。没有人工重放历史交易或额外发起交易模型探针。

## Worker重启的在途影响

发布前5个D3 O3 BUILD（IONQ四个Shell、COHR一个Shell）在Worker重启中被取消。现有编排自动用同一run_id/单元身份的第2次attempt重试，验收快照中5个均已重新running；尚未全部完成，不将其称为无中断发布或已完成研究恢复。原在途W3 job已成功结束。未修改这些研究任务的业务输入、研究版本或终态数据。

## 本地证据

`eval/w3_luna_fast_release_20261111/`：发布回执、actual-image源文件基线、配置验收、自然运行日志设置、业务阶段COMMITTED记录、5个后台任务前后attempt对应关系。源码改动与已发布文件均可按提交/镜像/摘要核对。

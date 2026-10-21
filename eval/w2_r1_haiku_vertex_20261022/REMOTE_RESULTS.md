# 远端凭据保管与 W2R1 Vertex 实测

日期：2026-10-22（北京时间）。测试在 `doxagent-sg` 完成，项目 `gen-lang-client-0328762493`。

## 凭据已落盘并验证

服务账户凭据位于远端：

```text
/etc/doxagent/credentials/google-cloud/gen-lang-client-0328762493-5d70d08b98dc.json
```

- 文件 root:root、0600；credentials/google-cloud 目录及上层 credentials 为0700。
- 本地/远端 SHA-256 一致，内容符合 service_account/project/token_uri，私钥可解析。
- 通过 SSH 字节流直接写入受限目录；没有经过仓库、公开临时目录或模型上下文。
- 测试容器仅将该文件只读挂载到 `/run/secrets/google-cloud.json`，通过 `GOOGLE_APPLICATION_CREDENTIALS` 读取。未烘焙进镜像、未复制私钥到测试报告，OAuth token 只保留进程内存。
- 本地原始 Downloads 文件保留，未做删除/迁移；未将密钥正文写入 changelog、Git、命令参数或日志。

后续远端调用沿用上述受限文件与只读挂载。不应将它放回 `/home/ubuntu/doxagent` 仓库或直接当模型 API key 使用。

## 执行环境

独立测试目录：`/home/ubuntu/doxagent-eval/w2r1-vertex-20261022`。

容器 `doxagent-eval-w2r1-vertex-adc-20261022` 使用当前 scheduler 镜像 ID `sha256:9ca070bc1b5588ae2f1de750ad94182de1d4f9d4e216b377ccc0707e799988f1`，只读 rootfs、凭据只读、CPU 0.75、内存768MiB、128 PID、去掉 capabilities，串行请求。不挂载任何 `/data` 生产数据库，不继承其他模型/交易密钥，未切换生产provider或重启生产服务。测试容器已退出。

该运行镜像缺少 google-auth，且 venv 没有 pip。最终使用镜像内 uv 将 `google-auth[requests]>=2.40,<3` 安装到独立 `eval/.deps`（实际2.62.0），通过测试容器 PYTHONPATH 使用，未修改生产镜像/venv。正式迁移需重建包含项目已声明依赖的运行镜像，不能把本次隔离 overlay 当成生产部署。

本轮只读挂载当前 Vertex/OpenRouter/transport 代码与定价规范化模块进行验证；参数仍为 Haiku5.5、global、high、max_tokens16384、timeout180s、system+完整Policy projection两处1h缓存。

## 实际结果

1. **OAuth ADC 成功**：服务账户可读取，google-auth 取得有效访问令牌，项目身份一致。预检只记录 valid/project/expiry，未输出 token。
2. **10/10 请求均被429配额拦截**：召回5例、未召回5例，10个不同历史Case，串行发送，无模型自动重试。
3. Google 明确指出：`aiplatform.googleapis.com/global_online_prediction_requests_per_base_model`，`base_model=anthropic-claude-haiku`，提示申请 quota increase。
4. **成功模型结果0条**；input/output/cache usage 均缺失，加权缓存命中率、模型TPS与计费费用均不可测，不当成0命中/零费用。
5. 全部错误发生在推理之前，尚不能完成新provider的在线参数/Schema/输出质量/缓存验收。不是服务账户继续401，也没有证据说明提示词或缓存内容本身导致失败。

代码包/依赖容器的准备失败日志保留；本轮模型尝试计数仅指最终 `remote-adc` 的10次推理请求。Launcher进程exit0只表示程序正常运行到结束，不能当作10例推理成功，以每例status和summary为准。

## 配额进一步核对

使用同一服务账户只读查询 Service Usage quota API 返回403 `SERVICE_DISABLED`：项目号748816333453尚未启用Service Usage API。因此实际额度值未取得，**不能断言配额一定为0**，只能确认Google此次按上述global请求配额拒绝了所有调用。未擅自启用其他Cloud API、提高限额或创建付费资源。

下一步应在 [项目配额页](https://console.cloud.google.com/iam-admin/quotas?project=gen-lang-client-0328762493) 核对 Vertex AI 的 global Haiku 请求配额，维度 `base_model=anthropic-claude-haiku`，并核对该模型的输入/输出token额度。按需申请提升。Google按模型族共享配额，global与regional/multi-region的配额桶不同；本轮不切换端点规避用户指定的global路径。

配额可用后，继续在远端使用同一凭据，以新label启动10例；保留本轮失败回执，不覆盖后再声称首次成功。冷缓存写入与同prefix后续命中分别统计。

## 证据

- `remote_receipts/launch.json`、`remote_receipts/run.log`：隔离环境、逐Case错误。
- `remote_receipts/eval/w2_r1_haiku_vertex_20261022/adc_preflight.json`：无token的OAuth成功预检。
- `remote_receipts/eval/w2_r1_haiku_vertex_20261022/remote-adc/summary.json` 与01—10回执：10次429，usage=null。
- 同目录 `quota_audit.json`：Service Usage未启用，实际配额值未知。
- `launch_remote.py`、`remote_entry.py`、`quota_audit.py`：可审查的远端执行/预检/只读配额脚本。

官方参考：[Claude配额与模型族](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/partner-models/claude/quotas)、[Service Usage配额查询](https://docs.cloud.google.com/service-usage/docs/reference/rest/v1beta1/services.consumerQuotaMetrics/list)。

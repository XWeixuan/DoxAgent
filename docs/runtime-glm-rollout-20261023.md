# GLM 四节点上线与凭据轮换（2026-10-23）

生产及本地启用 `DOXAGENT_PERSISTENT_RUNTIME_V2_API_PROFILE=bailian_glm`：W1R2、W1R3、W2R2、W2R3 均为百炼 `ZHIPU/GLM-5.3-FlashX high`，超时 120s，stream/include_usage、JSON-object 传输和本地严格 Schema/语义校验。使用已开通 GLM 的独立 key；其他百炼模型和 Qwen fallback 沿用原 key。W1R1/W2R1 保持 OpenRouter Haiku high/global、1h 缓存及既有 Qwen fallback。

GLM system 采用固定 Output Contract（Schema 按 key 排序）+ 原提示词，随后是冻结 R2 父轮回放和当前 Case。W2R2 Policy details 仍在动态消息之前；W1R3 每 Case Event ID 后缀位于固定提示词之后。无业务信息删减，无提示词内容/输出 Schema 改写；不填充无意义 token，不新增显式缓存标记/管理层。旧冻结 Case 的模型设置继续复用，新的执行 bundle 冻结新配置。

GLM 使用[官方隐式缓存](https://help.aliyun.com/zh/model-studio/context-cache)，智谱部署公共前缀门槛为 512 tokens，Provider 不保证每次命中；以原生 cached_tokens 回执验收。北京[标准域名仍可使用](https://help.aliyun.com/zh/model-studio/glm-zhipu)，修正原 opt-in 配置仅允许 workspace 域名的问题。

## 验证

- OpenRouter 新 key：本地 Haiku 最小请求 HTTP 200，16 input / 4 output tokens，返回 OK；实际 Google 端点，无 fallback。远端容器替换后重复最小调用并核对 key 指纹。
- 四个 GLM 节点均完成真实结构化调用，未推进 W3 或交易。三个节点首次两次未命中，预热后第 3–6 次全部命中：W2R2 为 2304/2435、W2R3 为 1024/1126、W1R3 为 2432/2532。小样本用于确认通道和缓存能力，包含重复父轮数据，不宣称这些比例等同于生产跨 Case 命中率或质量评测。
- 117 项相关回归通过，覆盖 GLM 传输、严格 Schema、R3 显式父上下文、输入视图、Recall fallback、Vertex 历史兼容及固定 system 前缀跨 Case 保持不变。

密钥仅更新忽略的本地 env 和远端私有 env/Compose 配置；不进入 Git、镜像、冻结 Case 或回执。生产通过精确提交构建/发布并核对运行镜像和 active execution。原始探测记录保存在本地 `eval/glm_runtime_release_20261023`，部署回执保存在远端私有 releases 目录。

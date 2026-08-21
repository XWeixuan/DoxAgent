# CDECR MU300 失败韧性、Package V3 容错与百炼 JSON Schema Strict 全面迁移方案

> 日期：2026-08-16  
> 状态：可直接执行的修复方案；本文件不代表已经修改运行时代码或完成真实验收。  
> 范围：相关性 Gate、Grounder、N9、共享 provider 调度、Package Workflow V3，以及全部 LLM 节点的百炼 JSON Schema strict 迁移。

## 1. 结论与目标

本轮不回滚现有业务流程，也不通过下调节点并发来掩盖问题。修复集中在五个共享缺口：

1. 相关性 Gate 明确告诉模型目标字符串是美股 ticker，而不是注入公司业务画像。
2. 将“模型已返回但局部输出非法”和“provider/transport 整体失败”分成两条完全不同的恢复路径。
3. 在现有 Tier/Stage lanes 之外增加真正覆盖所有模型请求的 provider 全局在途上限、启动速率整形和 API key 熔断。
4. Package V3 将可确定性修正的模型瑕疵改为局部规范化，不再把整批扩大为 `ValueError`；Prompt 同步说明关键滚动合并约束。
5. 全部 LLM 节点迁移到百炼 JSON Schema strict，同时保留 Responses continuation/session 语义和局部、可审计的紧急降级能力。

最终目标不是“所有异常都成功”，而是：

- provider 整体失败不产生 N 倍 item repair 请求；
- 单个非法输出不丢弃同批合法结果；
- 同一 provider 的实际并发始终不超过 100；
- `invalid_json` 基本消失；
- strict 迁移不显著增加钟墙、Token 或业务回归风险；
- 恢复失败时保留原任务为 retryable，不伪造成业务性 `CREATE_NEW`、空 Mention 或有效 Package。

## 2. 已确认问题与修复边界

### 2.1 相关性目标上下文不足

当前 `target_profiles={}` 时只向模型传入 `target="MU"`。这无法明确 MU 是美股 ticker，同时上游 corpus/manifest 会把检索标签直接写成 `ticker_hints=["MU"]`，并不证明正文实体就是 Micron。

本轮不恢复业务 profile，不注入 Micron 的行业、产品、客户或竞争关系。只增加一句固定模板，解决字符串类型歧义。

### 2.2 Grounder 与 N9 把 provider 整体失败扇出成 repair 风暴

当前两类错误混用了相似恢复动作：

- 模型正常返回，但 JSON、Schema、候选覆盖或局部业务字段不合法；
- 请求没有取得可用响应，例如 401/403/429、`provider_arrearage`、5xx、timeout、connection error。

后者不包含任何可局部修复的模型结果。把一个失败批次拆成多个 item repair 只会在 provider 仍异常时放大请求量。

### 2.3 lanes 不是 provider 全局保护

M1/M2/M3/M4 和各 Stage lane 只能约束各自队列。多个 lane 同时活跃、失败批次内部再扇出 repair 时，短时启动量仍可能叠加。需要一个位于所有真实 provider 调用之前的共享控制器。

### 2.4 Package V3 把可恢复瑕疵扩大为整批失败

当前动态 validator 对 occurrence 完整覆盖、MCP 引用和 merge 图要求严格，但部分要求没有进入 Prompt。模型响应通过 JSON Schema 后，仍可能因一个重复 ID 或一个非法 merge 引用使整批 `FAILED_RETRYABLE`。

本轮只放宽模型响应规范化，不放松 SQLite CAS、唯一 active membership、redirect 防环、版本一致性等持久化硬边界。

### 2.5 百炼 strict 只接入 Package V3

Package V3 已证明百炼 Chat Completions JSON Schema 模式可用；普通节点仍主要走 `StructuredModelRequest -> json_object`。这属于调用架构尚未统一，不是 provider 的明确能力障碍。

## 3. 总体设计

```text
Logical LLM task
      |
      v
Node batch planner
      |
      v
Global Provider Controller
  - global in-flight <= 100
  - start-rate shaping
  - per-key circuit breaker
  - whole-request retry
      |
      v
Bailian JSON Schema strict transport
      |
      +-- transport/provider failure --> retry same logical request; never item fan-out
      |
      +-- valid structured response
              |
              +-- all items legal --> Apply
              |
              +-- local output defect --> keep legal items; enqueue defective items
                                             |
                                             v
                                      deferred batched repair
```

只保留一套共享 provider controller 和一套错误分类，不在 Grounder、N9、Field、Judge 中复制 provider 重试逻辑。节点仍负责自身业务 validator 与局部 repair DTO。

## 4. 修改项 A：相关性 Gate ticker 语义说明

### 4.1 Prompt 修改

在 `relevance_filter.md` 开头职责说明后加入一句固定模板，由编排层安全替换 ticker：

> The target is the U.S.-listed company identified by stock ticker `{ticker}`; judge whether each candidate is relevant to that ticker's company or security.

约束：

- `{ticker}` 只能来自已经规范化的单一 `ticker_hints`；
- 多 ticker 或缺 ticker 继续 fail-open，不猜主目标；
- 不注入公司名称、业务描述、产业链、产品或别名；
- `target` DTO 保留 ticker 字符串，便于审计；
- Prompt hash/version 升级，旧 session/checkpoint 不得复用。

### 4.2 预期效果与风险

- 解决模型把 `MU` 当普通缩写或实体简称的问题；
- 不会引入固定 Micron 业务知识导致开放世界偏置；
- 无新增输出字段，输入只增加约 15–25 tokens；
- 不能修复上游 corpus 的错误 ticker 关联，因此明显歧义文章仍应由 Gate 判为不相关。

## 5. 修改项 B：统一错误分类与恢复协议

### 5.1 错误类别

新增共享 `ModelFailureClass`，每次失败只归入一个类别：

| 类别 | 典型错误 | 是否有模型结果 | 恢复方式 |
| --- | --- | --- | --- |
| `OUTPUT_LOCAL_INVALID` | Pydantic/字段/ID/coverage 局部失败 | 有 | 保留合法项，非法项进入延迟批量 repair |
| `OUTPUT_WHOLE_INVALID` | 整体 JSON/Schema 输出非法但 provider 已返回 | 有原始文本或响应 | 原请求短 repair 一次；仍失败则按任务粒度进入延迟 repair |
| `PROVIDER_THROTTLED` | 429、明确 rate/quota overload | 无 | 整个原请求退避重试，不拆 item |
| `KEY_AUTH` | 401/403、key invalid/permission | 无 | 当前 key 熔断，原请求切换健康 key 整体重试 |
| `KEY_ARREARAGE` | provider code `Arrearage` | 无 | 当前 key 记失败并可能冻结；原请求整体重试 |
| `PROVIDER_TRANSIENT` | 5xx、timeout、connection/reset | 无 | 整体退避重试 |
| `REQUEST_CONTRACT_INVALID` | 明确的 Schema/payload/参数非法 400 | 无 | 不盲目重发、不拆 item；节点 strict 降级或标 retryable |
| `UNKNOWN_PROVIDER_FAILURE` | 无法归类的 provider 错误 | 无 | 最多一次健康 key 整体重试，之后 retryable |

### 5.2 对 HTTP 400 的必要区分

不能把所有 400 都视为瞬时错误。相同非法 Schema 或payload反复整体重试不会成功。处理规则：

- provider code 指向 rate、quota、overload：归 `PROVIDER_THROTTLED`；
- provider code 指向 auth/account：归 `KEY_AUTH/KEY_ARREARAGE`；
- provider code 指向 JSON Schema unsupported、invalid parameter、context exceeded：归 `REQUEST_CONTRACT_INVALID`；
- 无明确 provider code：只允许换一个健康 key 重试一次，仍失败即停止，不扇出。

### 5.3 整体请求重试

对于 provider/transport 整体失败：

1. 保留原逻辑请求 ID、input hash 和item集合；
2. 不生成 `missing candidates` 或 `invalid tasks`；
3. 不进入 Grounder missing item recovery 或 N9 item repair；
4. 按 `1s -> 3s -> 8s` 加 0–300ms jitter，最多 3 次物理尝试；
5. 429 优先尊重 `Retry-After`；
6. 每次尝试重新经过全局 controller，并选择健康 key；
7. 全部失败后将原逻辑请求记为 `FAILED_RETRYABLE_PROVIDER`；
8. 文档/epoch可继续处理其他独立任务，但不得把该请求伪装成业务拒绝、空结果或 `CREATE_NEW`。

### 5.4 输出局部失败与延迟批量 repair

模型已返回结构化结果时：

- 先逐item规范化和校验；
- 合法item立即保留；
- 只把非法item写入节点内 `DeferredRepairQueue`；
- queue达到阈值或主wave结束时统一flush；
- repair仍使用原节点Schema、Prompt和业务 validator；
- repair响应继续逐item Apply，单条失败不扩大。

批量策略复用节点现有 packer，不新增跨业务的超大 batch：

| 节点 | Repair兼容范围 | 单repair批建议上限 |
| --- | --- | ---: |
| Grounder | 同文档、同上下文窗口 | 4 candidates，沿用现有chunk |
| N9 | 同wave、同DTO与Prompt版本 | 3 tasks，沿用当前主批 |
| Field | 同 field type/policy | 沿用当前field batch上限 |
| Judge | 同文档/同draft协议 | 4 drafts |
| Parent/Package description | 同节点Schema | 当前节点上限，不扩大 |

Queue触发条件：兼容item累计达到批上限、估算payload达到节点软预算，或主wave完成。不得为凑batch等待其他文档超过 250ms。

### 5.5 Grounder 特别处理

- 主 Grounder provider失败：整体重试主批；不生成 missing recovery。
- 主请求成功但漏候选/局部draft非法：合法draft保留，漏项进入延迟批量 missing recovery。
- missing recovery provider失败：整体重试该 recovery batch；不再切成4条一组继续扇出。
- recovery响应局部非法：仅非法候选进入下一次兼容batch，最多一轮。

### 5.6 N9 特别处理

- `atomic_coreference` provider失败：原2–3 task批整体重试；不得生成每Mention repair。
- 批响应缺少个别decision或个别assessment非法：只收集相关Mention，在主wave末按3条重新打包。
- repair仍非法：该Mention才落 `UNJUDGEABLE_FAILED`；其他合法decision不受影响。
- M3 escalation provider失败继续保留合法M2 decision，不改变现有业务语义。

## 6. 修改项 C：全局 provider 并发、启动整形与 key 熔断

### 6.1 全局硬上限

新增/收紧共享配置：

```text
CDECR_PROVIDER_GLOBAL_ACTIVE_REQUESTS=100
```

它是所有发送到同一百炼provider的真实LLM请求总和硬上限，不是各lane额度之和。M1 embedding若使用同一provider账户也进入controller；若是不同 endpoint/account，可配置独立 provider scope。

保留现有 M1/M2/M3/M4 和 Stage lane上限，用于相对优先级与公平性。最终准入条件同时满足：

```text
tier lane available
AND stage lane available
AND repair lane available when applicable
AND provider global in-flight < 100
AND start-rate token available
AND selected key healthy
```

### 6.2 短延时改为启动速率整形

不在每批后固定 sleep 1–2秒，因为这会累积显著钟墙。使用共享 token bucket：

```text
CDECR_PROVIDER_START_RATE_PER_SECOND=60
CDECR_PROVIDER_START_BURST=12
CDECR_PROVIDER_START_JITTER_MS=0..20
```

效果：

- 最多12个请求瞬时启动；
- 持续启动间隔平均约16.7ms；
- 100个请求约1.5秒平滑发出，而不是同一毫秒撞向网关；
- 对几十秒至数分钟的模型调用，新增钟墙接近可忽略；
- repair与normal共用同一start-rate，repair不能绕过整形。

### 6.3 自适应过载保护

复用现有 provider controller，不再新建第二套调度器：

- 10秒内出现3个429：动态global limit `100 -> 70`，start rate `60 -> 35/s`；
- 再出现3个：降至 `50` 和 `20/s`；
- 连续60秒无throttle且成功率正常：每30秒恢复 `+5 active`、`+5 starts/s`；
- 恢复不超过配置上限100/60；
- 单个key的 `Arrearage` 优先触发key熔断，不因一个坏fallback错误下调所有健康key；
- 所有key都异常时controller快速失败为 `PROVIDER_ALL_KEYS_UNAVAILABLE`，不让队列无限等待。

### 6.4 API key健康状态与冻结

共享 `ProviderKeyHealthRegistry`，只保存key的不可逆fingerprint，不记录明文：

- 同一key在10分钟内3次返回明确 `Arrearage`：冻结4小时；
- 401/403明确认证失败：立即冻结4小时；
- 429不冻结key，只进入限流冷却并遵守 `Retry-After`；
- 冻结期不再选择该key，也不把它作为每个请求的最后一次失败来源；
- 4小时后只允许一个低并发probe请求；probe成功再恢复；
- 进程重启后从短TTL运行时状态恢复冻结信息，避免长任务重启后再次轰击坏key；
- 运维可显式清除某个key fingerprint的冻结状态。

### 6.5 错误审计补强

每个逻辑请求新增但不进入模型payload的编排审计：

```json
{
  "logical_request_id": "...",
  "physical_attempt": 2,
  "failure_class": "KEY_ARREARAGE",
  "http_status": 400,
  "provider_code": "Arrearage",
  "key_fingerprint": "...",
  "key_state_after": "QUARANTINED",
  "global_in_flight": 72,
  "start_rate_limit": 35,
  "retry_action": "WHOLE_REQUEST_RETRY"
}
```

禁止只保留fallback的最后一个异常；逻辑请求汇总需保留每次物理尝试的安全错误序列和provider request ID。

## 7. 修改项 D：Package V3 容错规范化

### 7.1 原则

- JSON结构由strict Schema保证；
- 动态ID/coverage/merge图由编排层规范化；
- 安全方向始终偏向少合并、保留singleton，不偏向猜测性重定向；
- 可恢复的模型瑕疵只写audit，不使整批 `FAILED_RETRYABLE`；
- SQLite CAS、registry version、唯一active membership、redirect防环仍是硬失败。

### 7.2 Initial输出规范化

按模型响应顺序确定性处理：

1. 非输入 occurrence ID：删除并审计 `UNKNOWN_OCCURRENCE_DROPPED`；
2. occurrence重复：第一次归属生效，后续删除并审计 `DUPLICATE_OCCURRENCE_FIRST_WINS`；
3. 输入 occurrence遗漏：每条创建独立 provisional singleton MCP，审计 `MISSING_OCCURRENCE_SINGLETON`；
4. `mcp_id`重复：模型ID只是本次临时ref，按响应位置重编号，不影响永久MCP序列；
5. 空cluster：删除；
6. canonical纯空白：用该cluster首条 `parent_occurrence` 的确定性短截断作为provisional canonical，并标记description待补；
7. 规范化后再次检查所有输入 occurrence恰好一次；只有编排器自身仍无法形成完整partition时才抛内部错误。

### 7.3 Rolling输出规范化

顺序：先清洗assignment，再清洗merge，最后补遗漏。

#### Assignment

1. 非本批新 occurrence ID：删除；
2. occurrence重复分配：第一次出现位置生效，后续删除；
3. assignment引用未知/非active MCP：不接受该assignment，将对应occurrence转为新singleton MCP；
4. 新MCP canonical空白：从首条parent occurrence确定性生成provisional canonical；
5. 遗漏occurrence：每条新建singleton MCP。

#### Merge

1. 未知或非active keep/source：删除当前merge或非法source，不影响assignment；
2. self merge：从source列表删除keep本身；
3. 同一source出现多次：第一次合法merge生效，后续删除；
4. 某MCP既是source又在另一条merge中作为keep：**keep角色优先**，从其他merge的source列表删除它；这保留稳定hub并避免链式redirect；
5. source列表清空：删除该merge；
6. 规范化后仍出现redirect环或CAS冲突：整批不Apply，标 `FAILED_RETRYABLE_INTERNAL`，不得部分提交。

所有删除、补singleton和first-wins动作写入 orchestration audit，不增加LLM reasoning字段。

### 7.4 Prompt补漏：Initial

保留现有Prompt，仅追加两句：

> Use only occurrence IDs provided in the input, and place each one in exactly one cluster.  
> Every `mcp_id` must be unique within this response.

现有“Every occurrence_id must belong to exactly one MCP”可被第一句替换，避免重复。

### 7.5 Prompt补漏：Rolling

保留现有Prompt，追加以下短句：

> Use only new occurrence IDs provided in this request, and assign each one exactly once.  
> Existing assignments and merges may reference only active `mcp_id` values provided in the registry.  
> Each merge source may appear only once in the response.  
> An MCP used as a merge source must not also be used as a `keep_mcp_id` in another merge.

不向模型解释redirect实现、CAS或修复策略，避免增加不必要注意力负担。

### 7.6 Repair提示

Package V3若仍需要一次repair，请将实际validator差异以短结构传入，而不是只重复原Prompt：

```json
{
  "missing_occurrence_ids": ["..."],
  "duplicate_occurrence_ids": ["..."],
  "unknown_mcp_ids": ["..."],
  "duplicate_merge_sources": ["..."]
}
```

但确定性normalizer已经能安全修正的问题不再调用repair。repair只用于strict结构响应失败或无法形成顶层DTO，降低Token和钟墙。

## 8. 修改项 E：全部 LLM 节点迁移百炼 JSON Schema strict

### 8.1 迁移原则

1. strict是默认、可观测的正式路径，不只是settings里的未消费开关；
2. stateless与continuation节点分别选择兼容transport，不能为了strict丢失session语义；
3. domain Pydantic Schema不为provider修改；使用provider-only wire compiler；
4. strict只替代JSON语法/静态Schema repair，不替代业务validator；
5. 单节点Schema能力问题不得阻塞全workflow；允许有审计的节点级紧急fallback，但验收正式路径fallback必须为0。

### 8.2 统一请求契约

将普通 `StructuredModelRequest` 与 `ResponsesModelRequest` 的结构化输出能力统一到共享字段：

```text
json_schema
output_mode = json_schema | json_object
schema_name
strict
reasoning_effort
previous_response_id
session_cache
metadata
```

兼容旧调用方，但百炼provider默认编译为 `json_schema + strict=true`。删除“settings显示strict=true、adapter实际忽略”的状态分裂。

### 8.3 两种百炼transport

#### Stateless节点

Dreamer首轮、Grounder、Judge、Field、N9、Parent、Package等无continuation依赖的请求使用百炼 Chat Completions JSON Schema：

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "...",
      "strict": true,
      "schema": {}
    }
  }
}
```

#### Continuation/session节点

Dreamer→Relevance等依赖 `previous_response_id` 或session cache的节点，优先使用百炼 Responses JSON Schema，并保留原response continuation。不得默认切到Chat后重新发送整篇上下文。

若某模型/endpoint不支持Responses JSON Schema：

1. 先验证是否可在同一Responses会话中让第二轮使用strict格式；
2. 若确实不支持，只对该continuation节点使用Chat strict，并发送现有candidate-local最小上下文；
3. 评估输入Token与相关性质量；超过门槛则该节点暂时保留Responses `json_object` 作为受控例外，而不是牺牲业务质量伪造“100% strict”；
4. 例外必须在release gate显式列出并设消除期限。

### 8.4 异步 strict 路径

新增与同步 `complete_response` 对称的 `acomplete_response`，接入：

- Bulk Epoch async executor；
- scheduler Tier/Stage/provider lanes；
- 全局provider controller；
- model call token/latency/request ID审计；
- fallback key健康选择；
- whole-request retry。

禁止在大量节点中用线程包装同步strict请求作为长期实现，否则会增加线程、钟墙和取消恢复复杂度。

### 8.5 Provider wire Schema compiler

共享 `bailian_strict_wire_schema()`：

- 递归保证object `additionalProperties=false`；
- 保留业务 `description/default/examples`，不修改domain schema；
- 规范化nullable、local `$ref/$defs` 和百炼不支持的组合关键字；
- 保留required、枚举、长度和数值约束；
- 生成稳定schema hash和schema name；
- 同一Schema只编译/缓存一次；
- provider Schema不得再次附加进Prompt，避免输入重复。

### 8.6 逐节点能力probe

正式切换前，对每个唯一输出Schema至少做一次真实最小调用：

```text
Dreamer
Relevance
Grounder
Judge
Field coreference各策略公共Schema
N9 core / escalation / late
Parent induction / repartition / resolution
Package V3 initial / rolling / description
其他实际启用的LLM节点
```

Probe必须验证：

- provider接受Schema；
- 返回内容可直接 `json.loads + Pydantic`；
- thinking参数有效；
- Responses continuation没有丢失；
- input/output/reasoning token可审计；
- async与sync结果协议一致。

### 8.7 运行时紧急fallback

仅 `REQUEST_CONTRACT_INVALID` 可触发一次节点级fallback：

1. 记录 `STRICT_SCHEMA_FALLBACK` 及Schema hash/provider code；
2. 同一Schema版本第一次失败可尝试修正后的wire schema；
3. 仍失败才临时走 `json_object`；
4. fallback响应继续严格Pydantic/业务校验；
5. fallback不允许扩散为全provider关闭strict；
6. acceptance正式运行要求fallback=0，否则strict迁移不算通过。

provider 429、Arrearage、timeout等不得触发 `json_object` fallback，因为它与输出模式无关。

## 9. 持久化、幂等与恢复

### 9.1 必须持久化

- logical request与physical attempts映射；
- failure class和安全provider错误序列；
- key fingerprint健康状态与TTL；
- deferred repair item、原batch、validator code、flush batch ID；
- Package V3 normalization actions和受影响ID；
- strict schema hash、transport、fallback状态；
- provider controller的最大在途、启动速率、降压/恢复事件。

### 9.2 幂等规则

- 整体重试复用logical request ID和input hash，每次physical attempt使用不同attempt ID；
- 延迟repair item以 `stage + prompt/schema version + source task ID + validation code` 去重；
- Package first-wins依据模型响应顺序和稳定ID，resume不得改变；
- frozen/quarantined key状态不参与业务artifact hash；
- Prompt/Schema/transport版本必须进入checkpoint hash，防止旧 `json_object` 成功结果伪装为strict结果。

## 10. 实施顺序与主要文件

执行上一次性落地，但按以下顺序降低联调风险：

1. 共享 failure classifier、logical-attempt审计、key health和global controller；
2. 同步/异步百炼JSON Schema strict transport与wire compiler；
3. Grounder、N9接入“provider整体重试 / 输出延迟批repair”；
4. 其余节点迁移strict；
5. relevance ticker Prompt对齐；
6. Package V3 normalizer、Prompt和validator分层；
7. checkpoint/version/telemetry升级；
8. focused测试、Schema probes、固定30篇和MU300验收。

主要修改面：

| 文件/模块 | 预期修改 |
| --- | --- |
| `src/cdecr/models.py` | strict sync/async transport、failure classifier、per-attempt错误保留 |
| `src/cdecr/ports.py` | 统一structured request/response协议 |
| `src/cdecr/scheduler.py` | global in-flight、start-rate、key health、whole-request retry |
| `src/cdecr/config.py` | 全局并发、速率、退避、key quarantine配置 |
| `src/cdecr/cli.py` | 百炼strict参数真正接线、共享controller构造 |
| `src/cdecr/single_document.py` | Grounder延迟repair、provider失败不扇出、relevance模板注入 |
| `src/cdecr/cross_document.py` | N9延迟batch repair、整体失败重试 |
| `src/cdecr/package_global_clustering.py` | initial/rolling normalizer、软失败audit |
| `src/cdecr/prompts/v1/relevance_filter.md` | ticker固定说明句 |
| `src/cdecr/prompts/v1/package_v3_*clustering.md` | ID与merge约束补漏 |
| `src/cdecr/registry.py` | provider attempt/key health/deferred repair审计与恢复 |
| `tests/cdecr/` | failure fan-out、strict、并发、Package normalization回归 |

## 11. 测试方案

### 11.1 确定性单元/集成测试

必须新增：

1. `MU`目标请求中明确出现“U.S.-listed company identified by stock ticker MU”，且不含业务profile；
2. 多ticker仍fail-open；
3. Grounder主请求429后只整体重试，不产生missing/item repair；
4. N9三任务批provider失败后仍只重试三任务原批；
5. N9仅一个decision缺失时，合法两条保留，缺失项进入延迟repair；
6. 多个invalid item按原batch上限flush，不逐条突发；
7. 全部lane同时压测时provider最大active不超过100；
8. 启动burst不超过12且长期rate不超过60/s；
9. fallback key三次Arrearage后冻结4小时，冻结期请求不再选它；
10. primary 429 + fallback Arrearage保留两跳错误，不只记录最后Arrearage；
11. Package initial重复/未知/遗漏occurrence均可确定性规范化；
12. Package rolling未知MCP、重复source、source/keep冲突均局部删除且不整批失败；
13. redirect环/CAS冲突仍硬失败且零部分Apply；
14. 所有启用节点默认请求为 `json_schema/strict=true`；
15. async strict请求仍受scheduler和provider controller约束；
16. strict Schema失败只影响对应节点，不全局关闭strict；
17. resume/idempotency不重复模型调用或repair。

### 11.2 故障注入场景

| 场景 | 预期 |
| --- | --- |
| 主key 429，fallback成功 | 原请求整体成功，0 item repair |
| 主key 429，fallback Arrearage | 保存两跳；fallback计数/冻结；原请求退避 |
| 所有key timeout | 原请求retryable；无业务性空结果 |
| strict response一个item漏字段 | 合法item保留，坏item延迟batch repair |
| Package initial occurrence重复 | first wins；重复删除；partition完整 |
| Package rolling source同时keep | keep优先；冲突source删除；无redirect链 |
| provider连续恢复 | global limit逐步恢复，不瞬间回到100 |

## 12. 真实验收与发布门槛

### 12.1 Gate 0：Schema与transport

- 全部唯一Schema真实probe成功；
- 正式节点调用 `json_schema/strict=true` 覆盖率100%；
- `STRICT_SCHEMA_FALLBACK=0`；
- Responses continuation/session行为与迁移前一致；
- provider key或secret不进入报告。

### 12.2 固定30篇A/B

使用迁移前最后一个正常、无provider污染的30篇结果作为基准：

- 30/30完成；
- `invalid_json`下降至少90%；
- provider整体失败产生的item repair数=0；
- 总Input Token不高于基准 +3%，目标下降；
- 总Output Token不高于基准；
- fresh wall不高于基准 +5%；
- Mention/Atomic/Package主要Precision或Recall单项不得下降超过1pp；
- Grounder candidate disposition覆盖100%，技术失败局部可重试；
- N9不得因整批provider错误写成大规模 `UNJUDGEABLE_FAILED`；
- Package V3可恢复模型瑕疵不再产生整批 `ValueError`。

### 12.3 MU300压力验收

- provider实际最大in-flight `<=100`；
- start burst `<=12`、稳定start rate `<=60/s`；
- 同一逻辑请求的物理尝试 `<=3`；
- provider失败请求放大系数不超过1.2，不出现 N倍repair；
- key quarantine真实生效；
- 300篇文档处理状态与有效Mention/Atomic/Package覆盖分开报告；
- 相关性全滤文档逐篇区分“合理ticker歧义”与“Micron相关假阴性”；
- strict fallback=0；若不为0则本轮不能作为正式strict验收；
- Token和钟墙必须与上一份正常MU300或按成功产出归一化比较，provider污染轮不得作为效率基准。

## 13. 风险与回滚单位

| 修改 | 主要风险 | 控制与回滚单位 |
| --- | --- | --- |
| ticker说明句 | 可能让模型过度保留只提ticker的候选 | 单独Prompt版本开关；固定相关性Gold A/B |
| deferred batch repair | repair等待最多250ms | 节点级开关；紧急恢复原item模式，但provider错误仍禁止扇出 |
| global cap/rate | 低估provider能力导致小幅增墙钟 | cap与rate独立配置；不得取消hard cap |
| key quarantine | 暂时冻结误报码key | probe恢复和运维清除；保留fingerprint审计 |
| Package first-wins | 响应顺序影响归属 | 稳定响应顺序审计；安全偏向singleton |
| strict迁移 | Schema不兼容或continuation变化 | 节点级fallback；不能全局回退 |

不得回滚的安全语义：provider整体失败不扇出、全局in-flight硬上限、错误尝试链留痕、Package持久化CAS/防环。

## 14. 最终实施判断

本方案可以一次性落地，且不需要重构CDECR业务对象或增加模型reasoning字段。复杂度主要集中在共享provider controller和统一strict transport，能够替代当前分散在Grounder、N9等节点中的错误放大逻辑，而不是继续叠加节点特例。

预期净效果：

- 鲁棒性：显著提升；provider故障不再转化为业务性空结果或数百repair；
- 钟墙：正常运行预计持平或下降，启动整形带来的约1–2秒波次成本应被invalid JSON/repair减少抵消；
- Token：预计下降，Schema不再重复附在Prompt，结构repair减少；
- 质量：strict本身不改变业务Prompt，ticker说明和Package Prompt补漏是唯一必要语义调整；
- 风险：主要在continuation节点transport兼容和Package软规范化边界，已通过节点级probe、singleton安全降级和发布门槛控制。


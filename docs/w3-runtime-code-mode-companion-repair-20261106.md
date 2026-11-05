# W3 自定义运行时 code-mode 配套修复

2026-11-06，全量回测中发现此前打包的自定义Codex只包含主二进制，没有同目录的 `codex-code-mode-host`。真实工具轨迹持续返回 `failed to spawn code-mode host ... No such file or directory`，使Agent无法通过代码模式发现或读取研究工具。即使45条都产出合法schema、Worker状态为SUCCEEDED，也不能据此评价提示词质量或耗时。

此次故障轮次位于 `eval/w3_backtest/runs/luna_full45_timeaware_20261106/run_final/`，已明确作废。前面的依赖启动失败与系统Skill目录混入轮次也已分别标记；最终对照只采用工具链修正后的 `run_verified/`。

## 根因和修复

显式指定Codex主二进制后，原生code-mode按主二进制所在目录寻找配套host。把SDK helper目录加入PATH不能满足这个同目录查找；此前直接 `exec_command` 的模拟测试也没有经过code-mode host，因而没有覆盖此依赖。

本轮评测直接复用已锁定的官方 `openai-codex-cli-bin==0.159.3` Linux wheel中的配套host，与时间补丁主二进制并置。补丁不改变code-mode通信协议；真实链路预检已验证该组合。主二进制摘要保持不变，配套host摘要为：

`fe64bd08c7844ca1fca91d8cb825e62012a03a05ae8040c635bbb61e5be0b4c5`

为避免后续构建再次遗漏：

- `deploy/codex-patches/Dockerfile` 改为从同一固定上游commit同时构建、导出主二进制与host，并分离调试符号。
- `prepare_build.py`及构建封装记录、验证host摘要；`Dockerfile.v2`明确为两个可执行文件设置执行位。
- 回测显式开启时间感知时，必须找到可执行的同目录host，并将host摘要一并纳入run身份；在派发模型前发现缺失。
- 新增原生code-mode回归，要求真实执行custom exec并读回输出，防止仅凭schema或普通工具模拟测试误判链路可用。

上游固定commit中的目标定义见 [code-mode-host/Cargo.toml](https://raw.githubusercontent.com/openai/codex/01fc69f4026735edfdf6789820549727a4867b11/codex-rs/code-mode-host/Cargo.toml)。

## 已完成的验证

1. 真实补丁二进制、真实code-mode host、真实W3 Research MCP、三次本地模拟Responses：成功执行代码、读取研究哨兵内容，并记录时间片段；没有真实模型调用。证据：`toolchain_preflight.json`及对应请求记录。
2. 原生SDK专项3项通过，包括新增code-mode配套测试，以及原有档位、恢复、Fork/Resume、压缩和阶段隔离测试。证据：本轮 `native_regression.xml`。
3. 回测运行时选择、缺失依赖、摘要冻结及计时协议专项17项通过，涉及文件Ruff通过。
4. 工具完整的新评测已开始；正式分析逐条检查原生工具输出，不把出现缺少code-mode host错误的结果当作有效比较。

## 边界

本轮没有重新编译整套Rust，也没有部署生产；本次可用产物是原有时间补丁主二进制加官方同版host。新的完整构建定义已纠正，但不能称为已经执行了这次完整构建。

原开发报告中的真实Elapsed片段和模拟Responses机制证据仍需按各自覆盖范围理解；那些未验证code-mode研究读取的真实模型烟测，不能单独证明研究环境完整。本轮故障评分尤其不能被解释为“新提示词让模型更保守”。最终效果以 `run_verified/`完整工具链下的结果为准。

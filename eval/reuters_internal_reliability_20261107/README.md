# Reuters 内部可靠性修复证据

运行源码1b3a744f；最终验收见acceptance-final.json和docs/reuters-internal-reliability-repair-20261107.md。

- final-release.json：仅Site Access窄发布、10模块摘要、Supervisor未变。
- passive-targets.json / ownership.json：原生存活但登记CLOSED的业务页8552…，无人工会话或Challenge引用，FrameTree/DOM读取均超时。
- passive-targets-after.json：该遗留页消失，Chrome实例/PID保持，后续自然恢复成功。
- acceptance-final.json：完整最终窗口、当前acquisition.sqlite3采集Owner状态/周期、Registry业务与恢复回执、资源样本；不是主动网站/Provider探测。
- final-regression.xml：29 passed；orphan-final.xml为108 passed/1 skipped/2个既有失败，orphan-baseline.xml复现这2项。regression.xml与baseline.xml另有3项同样的既有失败。选择集重叠，不求和。
- backup-cleanup.json：仅本轮3份冗余中间Registry备份删除记录，首份与最终备份保留。

早期initial-observation/observation-1/observation-2/final-window/whole-window的Registry事件仍可作对应时刻证据，但其中poll_states/query_coverage引用了已退役bus轮询投影，**不得作为当前ticker可用性结论**。这一口径已在最终audit.py中改为acquisition.sqlite3。旧快照保留原样，不回填。

jobs按updated_at筛选，可能是旧作业的晚恢复或诊断更新；判断新付费提交需使用submitted_at/task_id，不可直接计jobs长度。Provider返回、结果应用、原页解除、fresh_probe、自然CRAWLER和正式BODY分别记录。

未修改浏览器身份/出口/认证数据，未主动提交付费任务，未增加验证码绕过能力。主身份仍阻断、排队与BODY验收仍有缺口；未完成双身份并行需求。

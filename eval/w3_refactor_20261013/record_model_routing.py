from pathlib import Path

with Path('changelog').open('a', encoding='utf-8') as file:
    file.write('\n2026-10-14 W3 Case 模型分配：新进入 W3 的 Policy HIT Case 固定 gpt-6-sol / medium，其余固定 gpt-6-luna / max；入口冻结配置，Template、Fork 与两个阶段统一读取，并纳入子 Thread 兼容键。覆盖新旧 W3 Runner；已入场旧 Case 保留原参数恢复。新 Case 配置优先于旧全局 model override。新增 Worker 请求/关联线程测试及相关回归共36项通过；未改 prompt/skill、其他节点模型或生产部署。\n')

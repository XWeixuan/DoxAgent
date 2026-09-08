import type { MonitorMode } from "@contract";

export const modes: Record<MonitorMode, string> = {
  MESSAGE_MONITORING: "消息监测",
  PAPER_TRADING: "模拟交易",
  LIVE_TRADING: "实盘交易",
};

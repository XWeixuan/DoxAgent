import type { ParameterType, StateValueData } from "@contract";
export const roles: Record<string, string> = {
  ACTUAL: "实际",
  MANAGEMENT: "管理层",
  SELL_SIDE: "卖方",
  INDUSTRY_CHAIN: "产业链",
  MARKET_IMPLIED: "市场隐含",
  CURRENT: "当前",
  SUPERSEDED: "已替代",
  DISPUTED: "存在争议",
  RETRACTED: "已撤回",
  REQUIRED: "必要条件",
  BLOCKER: "阻碍因素",
  MODIFIER: "修正因素",
};
export function stateValue(type: ParameterType, value: StateValueData) {
  switch (type) {
    case "NUMBER": {
      const v = value as Extract<StateValueData, { number: number }>;
      return `${v.number} ${v.unit}`;
    }
    case "RANGE": {
      const v = value as Extract<StateValueData, { lower: number }>;
      return `${v.lower}–${v.upper} ${v.unit}`;
    }
    case "TIME": {
      const v = value as Extract<StateValueData, { precision: string }>;
      return v.point ?? `${v.start ?? "未记录"} — ${v.end ?? "未记录"}`;
    }
    case "STAGE":
      return (value as { stage: string }).stage;
    case "DIRECTION":
      return (value as { direction: string }).direction;
    case "EVIDENCE": {
      const v = value as { stance: string; strength: string };
      return `${v.stance} · ${v.strength}`;
    }
  }
}

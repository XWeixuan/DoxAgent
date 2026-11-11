import { NavLink } from "react-router-dom";
import { ChartNoAxesCombined, LibraryBig } from "lucide-react";
import "./global-actions.css";

export function GlobalActions() {
  return (
    <nav className="global-actions" aria-label="全局工具">
      <NavLink to="/message-bus/sources">
        <LibraryBig aria-hidden="true" />
        全部消息源
      </NavLink>
      <NavLink to="/audit/cost">
        <ChartNoAxesCombined aria-hidden="true" />
        收益 / 成本审计
      </NavLink>
    </nav>
  );
}

import { Fragment } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Check, ChevronRight, Pause, RotateCcw, Trash2 } from "lucide-react";
import type { TickerOverview } from "@contract";
import { Button } from "@/components/ui/button";
import { TableRow, TableCell } from "@/components/ui/table";
import { StatusBadge } from "@/components/state";
import { formatInstant, metricText, valueText } from "@/core/format";
import { operationsFor, type PendingCommand } from "@/core/operations";
import { useRuntime } from "@/core/runtime";
import { MetricNote } from "./metrics";
import { Initialization } from "./initialization";
import { OperationNotice } from "./operation-notice";
import { modes } from "./labels";

export function MonitorRow({
  row,
  prefix,
  pending,
  canOperate,
}: {
  row: TickerOverview;
  prefix: string;
  pending?: PendingCommand;
  canOperate: boolean;
}) {
  const operations = operationsFor(useRuntime());
  const state = row.state;
  const busy =
    !!pending &&
    ["submitting", "tracking", "unknown", "waiting"].includes(pending.phase);
  const accessible = !state.initialization_incomplete;
  const navigate = useNavigate();
  const destination = `/ticker/${state.ticker}/research`;
  return (
    <Fragment>
      <TableRow
        className="monitor-row"
        data-navigable={accessible}
        onClick={(event) => {
          if (
            !accessible ||
            (event.target as Element).closest("a, button, [data-row-control]")
          )
            return;
          if (window.getSelection()?.toString()) return;
          if (event.ctrlKey || event.metaKey)
            window.open(destination, "_blank", "noopener,noreferrer");
          else navigate(destination);
        }}
      >
        <TableCell>
          <div className="ticker-cell">
            {accessible ? (
              <Link to={destination} className="ticker-link">
                {state.ticker}
                <ChevronRight aria-hidden="true" />
              </Link>
            ) : (
              <strong>{state.ticker}</strong>
            )}
          </div>
        </TableCell>
        <TableCell>
          <span className="mode-label">
            {valueText(state.effective_mode, (v) => modes[v])}
          </span>
          {(state.effective_mode.state !== "AVAILABLE" ||
            state.effective_mode.value !== state.requested_mode) && (
            <small className="mode-pending">
              待生效 · {modes[state.requested_mode]}
            </small>
          )}
        </TableCell>
        <TableCell>
          <div className="status-stack">
            <StatusBadge dimension="run" value={state.run_state} />
            <StatusBadge
              dimension="health"
              value={state.health}
              title={state.health_reasons.join(" · ")}
            />
          </div>
        </TableCell>
        <TableCell className="time-cell">
          {valueText(row.last_standard_message_at, formatInstant)}
        </TableCell>
        <TableCell>
          <div className="bus-counts">
            <span title="正常消息源">
              <Check aria-hidden="true" />
              {valueText(row.source_counts.normal)}
            </span>
            <span
              className={
                row.source_counts.abnormal.state === "AVAILABLE" &&
                row.source_counts.abnormal.value > 0
                  ? "error-text"
                  : ""
              }
              title="异常消息源"
            >
              <span aria-hidden="true">!</span>
              {valueText(row.source_counts.abnormal)}
            </span>
          </div>
        </TableCell>
        <TableCell
          className="numeric"
          data-label={`${prefix}交易 · 执行 / 触发`}
          title={`${prefix}成功执行 / 实际触发`}
        >
          {metricText(row.metrics.trade_executed)}
          <span className="slash"> / </span>
          {metricText(row.metrics.trade_triggered)}
        </TableCell>
        <TableCell className="numeric" data-label={`${prefix}消息`}>
          {metricText(row.metrics.messages)}
        </TableCell>
        <TableCell className="numeric cost-cell" data-label={`${prefix}成本`}>
          {metricText(row.metrics.api_token_cost, true)}
          <MetricNote metric={row.metrics.api_token_cost} />
        </TableCell>
        <TableCell data-row-control>
          <div className="row-actions">
            {(
              [
                { kind: "PAUSE", action: "pause", label: "暂停", icon: Pause },
                {
                  kind: "RESTART",
                  action: "restart",
                  label: "重启",
                  icon: RotateCcw,
                },
                {
                  kind: "REMOVE",
                  action: "remove",
                  label: "删除",
                  icon: Trash2,
                },
              ] as const
            ).map(({ kind, action, label, icon: Icon }) => (
              <Button
                key={kind}
                variant="ghost"
                size="icon-sm"
                disabled={busy || !canOperate || !state.actions[action].allowed}
                aria-label={`${label} ${state.ticker}`}
                title={
                  state.actions[action].reason || `${label} ${state.ticker}`
                }
                className={kind === "REMOVE" ? "remove-action" : ""}
                onClick={() =>
                  void operations.submit({ ticker: state.ticker, kind, state })
                }
              >
                <Icon data-icon="inline-start" />
                <span className="action-label">{label}</span>
              </Button>
            ))}
          </div>
        </TableCell>
      </TableRow>
      {state.initialization_incomplete && (
        <TableRow className="init-row">
          <TableCell colSpan={9}>
            <Initialization
              ticker={state.ticker}
              progress={row.initialization?.data ?? null}
              busy={busy}
              canOperate={canOperate}
              pending={pending}
            />
          </TableCell>
        </TableRow>
      )}
      {!state.initialization_incomplete && pending && (
        <TableRow className="receipt-row">
          <TableCell colSpan={9}>
            <OperationNotice entry={pending} />
          </TableCell>
        </TableRow>
      )}
    </Fragment>
  );
}

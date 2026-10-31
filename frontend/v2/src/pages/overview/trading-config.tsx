import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Settings, X, Plus, Wallet } from "lucide-react";
import type { TradingSettings, TradingCapital } from "@contract";
import { DetailPanel } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useRuntime } from "@/core/runtime";

export function TradingConfigButton({
  ticker,
  disabled,
}: {
  ticker: string;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="ghost"
        size="icon-sm"
        disabled={disabled}
        aria-label={`交易配置 ${ticker}`}
        onClick={() => setOpen(true)}
      >
        <Settings />
      </Button>
      {open && <TradingDialog ticker={ticker} close={() => setOpen(false)} />}
    </>
  );
}
function TradingDialog({
  ticker,
  close,
}: {
  ticker: string;
  close: () => void;
}) {
  const runtime = useRuntime();
  const [draft, setDraft] = useState<TradingSettings>();
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const path = `/tickers/${encodeURIComponent(ticker)}/trading-config`;
  const query = useQuery({
    queryKey: [runtime.scope, "trading-config", ticker],
    queryFn: ({ signal }) =>
      runtime.api.request("TradingConfiguration", path, { signal }),
    refetchInterval: waiting ? 2000 : false,
  });
  const data = query.data?.data.data;
  useEffect(() => {
    if (data && !draft) {
      setDraft(data.configuration);
      setRevision(data.revision);
    }
  }, [data, draft]);
  const dirty =
    !!draft &&
    !!data &&
    JSON.stringify(draft) !== JSON.stringify(data.configuration);
  const applied =
    !!data?.active &&
    data.active.ticker_revision === data.revision &&
    data.active.account_revision ===
      data.accounts[data.configuration.environment]?.revision;
  useEffect(() => {
    if (applied) setWaiting(false);
  }, [applied]);
  function field<K extends keyof TradingSettings>(
    key: K,
    value: TradingSettings[K],
  ) {
    setDraft((old) => (old ? { ...old, [key]: value } : old));
  }
  async function save() {
    if (!draft) return;
    setSaving(true);
    setError("");
    try {
      const result = await runtime.api.request(
        "TradingConfigurationWrite",
        path,
        {
          method: "PUT",
          body: { configuration: draft, expected_revision: revision },
        },
      );
      setRevision(result.data.revision);
      setDraft(result.data.configuration);
      setWaiting(true);
      await query.refetch();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  const accountType =
    data?.accounts[draft?.environment || "PAPER"]?.account_type;
  const cash = accountType === "CASH";
  const text = (key: keyof TradingSettings, label: string) => (
    <label>
      {label}
      <Input
        value={String(draft?.[key] ?? "")}
        onChange={(e) => field(key, e.target.value.toUpperCase() as never)}
      />
    </label>
  );
  const toggle = (
    key:
      | "hedge_enabled"
      | "trailing_profit_enabled"
      | "cash_short_substitute_enabled",
    label: string,
  ) => (
    <label className="trading-check">
      <input
        type="checkbox"
        checked={!!draft?.[key]}
        onChange={(e) => field(key, e.target.checked)}
      />
      {label}
    </label>
  );
  return (
    <DetailPanel
      label={`${ticker} 交易配置`}
      className="trading-dialog"
      close={close}
    >
      <div className="trading-dialog-heading">
        <h2>{ticker} · 交易配置</h2>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="关闭交易配置"
          onClick={close}
        >
          <X />
        </Button>
      </div>
      {query.isError && <p role="alert">{query.error.message}</p>}
      {draft && (
        <div className="trading-fields">
          <label>
            交易账户
            <select
              aria-label="交易账户"
              value={draft.environment}
              onChange={(e) =>
                field("environment", e.target.value as "PAPER" | "LIVE")
              }
            >
              <option value="PAPER">Paper</option>
              <option value="LIVE">Live</option>
            </select>
          </label>
          <p>
            {data?.accounts[draft.environment]?.account_type ?? "账户未配置"}
          </p>
          {cash && (
            <>
              {toggle("cash_short_substitute_enabled", "SHORT 替代")}
              {draft.cash_short_substitute_enabled &&
                text("inverse_single_stock_etf", "反向单股 ETF")}
            </>
          )}
          {toggle("hedge_enabled", "开启对冲")}
          {draft.hedge_enabled && (
            <>
              <label>
                对冲名义金额 / 主仓名义金额
                <Input
                  type="number"
                  min="0.01"
                  step="0.1"
                  value={draft.hedge_ratio}
                  onChange={(e) => field("hedge_ratio", e.target.value)}
                />
              </label>
              {cash ? (
                <>
                  {text(
                    "cash_positive_index_etf",
                    "正向（LONG）指数 ETF · SHORT 信号时买入",
                  )}
                  {text(
                    "cash_inverse_index_etf",
                    "反向（SHORT）指数 ETF · LONG 信号时买入",
                  )}
                </>
              ) : accountType === "MARGIN" ? (
                text(
                  "margin_index_etf",
                  "指数 ETF · LONG 信号时卖空，SHORT 信号时买入",
                )
              ) : (
                <p>账户接入后，按 CASH / MARGIN 类型配置对冲 ETF。</p>
              )}
            </>
          )}
          {toggle("trailing_profit_enabled", "滑动止盈")}
          <div>
            <div className="trading-dialog-heading">
              <span>特殊日期禁止交易</span>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="添加禁交易日期"
                onClick={() =>
                  field("banned_semantic_dates", [
                    ...draft.banned_semantic_dates,
                    "",
                  ])
                }
              >
                <Plus />
              </Button>
            </div>
            {draft.banned_semantic_dates.map((date, i) => (
              <div className="trading-date" key={i}>
                <Input
                  type="date"
                  aria-label={`禁交易日期 ${i + 1}`}
                  value={date}
                  onChange={(e) =>
                    field(
                      "banned_semantic_dates",
                      draft.banned_semantic_dates.map((v, j) =>
                        j === i ? e.target.value : v,
                      ),
                    )
                  }
                />
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`移除禁交易日期 ${i + 1}`}
                  onClick={() =>
                    field(
                      "banned_semantic_dates",
                      draft.banned_semantic_dates.filter((_, j) => j !== i),
                    )
                  }
                >
                  <X />
                </Button>
              </div>
            ))}
            <small>美东当日 02:00 至次日 02:00；已有持仓继续退出。</small>
          </div>
          <p role="status">
            {dirty
              ? "有未保存的修改"
              : applied
                ? "执行端已应用"
                : data?.revision
                  ? "已保存，等待执行端应用"
                  : "尚未保存"}
            {data?.error ? ` · ${data.error.error}` : ""}
          </p>
          {error && <p role="alert">{error}</p>}
          <Button disabled={saving} onClick={() => void save()}>
            {saving ? "保存中…" : "保存配置"}
          </Button>
          <small>
            账户与策略配置影响后续新交易；禁交易日期同时拦截尚未发出的开仓。
          </small>
        </div>
      )}
    </DetailPanel>
  );
}

export function CapitalButton({ disabled }: { disabled: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="outline"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        <Wallet />
        策略资金
      </Button>
      {open && <CapitalDialog close={() => setOpen(false)} />}
    </>
  );
}
function CapitalDialog({ close }: { close: () => void }) {
  const runtime = useRuntime();
  const [waiting, setWaiting] = useState(false);
  const query = useQuery({
    queryKey: [runtime.scope, "trading-capital"],
    queryFn: ({ signal }) =>
      runtime.api.request("TradingCapital", "/trading-capital", { signal }),
    refetchInterval: waiting ? 2000 : false,
  });
  const accounts = query.data?.data.data?.accounts;
  useEffect(() => {
    if (
      accounts?.every(
        (a) => !a.desired || a.desired.identity === a.applied?.identity,
      )
    )
      setWaiting(false);
  }, [accounts]);
  return (
    <DetailPanel label="账户策略资金" className="trading-dialog" close={close}>
      <div className="trading-dialog-heading">
        <h2>账户策略资金</h2>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="关闭策略资金"
          onClick={close}
        >
          <X />
        </Button>
      </div>
      {query.isError && <p role="alert">{query.error.message}</p>}
      {accounts?.map((account) => (
        <CapitalAccount
          key={account.environment}
          account={account}
          saved={() => {
            setWaiting(true);
            void query.refetch();
          }}
        />
      ))}
    </DetailPanel>
  );
}
function CapitalAccount({
  account,
  saved,
}: {
  account: TradingCapital["accounts"][number];
  saved: () => void;
}) {
  const { api } = useRuntime();
  const [amount, setAmount] = useState(
    account.desired?.amount ?? account.book?.allocated ?? "",
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    setError("");
    try {
      await api.request(
        "TradingCapitalWrite",
        `/trading-capital/${account.environment}`,
        {
          method: "PUT",
          key: crypto.randomUUID(),
          body: { amount, reason: "前端调整账户策略额度" },
        },
      );
      saved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="trading-fields capital-account">
      <h3>
        {account.environment} · {account.account_type}
      </h3>
      <label>
        分配本金（USD）
        <Input
          type="number"
          min="0"
          step="1"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
      </label>
      <p>
        已实现净盈亏：{account.book?.realized_net_pnl ?? "—"} · 当日/周期额度：
        {account.book?.trading_equity ?? "—"}
      </p>
      <p>
        占用预算：{account.book?.occupied_budget ?? "—"} · 策略净值：
        {account.book?.net_value ?? "—"}
      </p>
      <p>
        浮动盈亏：{account.book?.unrealized_pnl ?? "—"}
        {account.book?.provisional ? " · 账本待核实" : ""}
      </p>
      <p role="status">
        {amount !== (account.desired?.amount ?? account.book?.allocated ?? "")
          ? "有未保存的修改"
          : account.desired?.identity === account.applied?.identity &&
              account.applied
            ? "执行端已应用"
            : account.desired
              ? "已保存，等待执行端应用"
              : account.book
                ? "当前额度已应用"
                : "尚未分配"}
      </p>
      {error && <p role="alert">{error}</p>}
      <Button disabled={busy} onClick={() => void save()}>
        保存额度
      </Button>
    </section>
  );
}

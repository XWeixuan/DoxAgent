import {
  Layers3,
  ChartNoAxesCombined,
  GitPullRequestArrow,
  Split,
  FileCheck2,
  History as HistoryIcon,
  ChevronDown,
  CalendarDays,
} from "lucide-react";
import { useState, useEffect } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import type {
  ExpectationsSummary,
  ShellSummary,
  ExpectationUnit,
  StateValueData,
  ParameterType,
  ContentRef,
} from "@contract";
import { usePageContext, useRead, tickerPath, id } from "@/core/page-query";
import { usePages } from "@/core/paged-query";
import { queryString } from "@/core/api";
import { formatInstant, valueText } from "@/core/format";
import { Button } from "@/components/ui/button";
import { PageTitle, History } from "@/components/page-kit";
import { Module, Notice } from "@/components/state";
import { Citations } from "@/components/citations";
const modeIcons = {
  ALL: Layers3,
  STATE: ChartNoAxesCombined,
  FACTORS: GitPullRequestArrow,
  GAPS: Split,
};
const modes = [
  ["ALL", "全部内容"],
  ["STATE", "当前状态"],
  ["FACTORS", "实现因素"],
  ["GAPS", "潜在差异"],
] as const;
export default function Expectations() {
  const { ticker = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const run = params.get("run");
  const [history, setHistory] = useState(false);
  const context = usePageContext(ticker, "EXPECTATIONS"),
    view = context.data?.data.view_id;
  const summary = useRead(
    "Expectations",
    run
      ? tickerPath(ticker) + `/expectations/runs/${id(run)}?limit=20`
      : view
        ? tickerPath(ticker) +
          "/expectations/current" +
          queryString({ view_id: view, limit: "20" })
        : null,
    run ? undefined : view,
  );
  const selectRun = (value: string) => {
    const p = new URLSearchParams();
    if (value) p.set("run", value);
    setParams(p);
    setHistory(false);
  };
  return (
    <>
      <PageTitle title={`${ticker} 预期研究`} refresh={context.refetch}>
        <Button variant="outline" onClick={() => setHistory(!history)}>
          <HistoryIcon aria-hidden="true" />
          历史文档
        </Button>
        {run && (
          <Button variant="outline" onClick={() => selectRun("")}>
            返回现行
          </Button>
        )}
      </PageTitle>
      {context.error && <Notice danger>{context.error.message}</Notice>}
      {history && (
        <History
          ticker={ticker}
          kind="expectations"
          onSelect={selectRun}
          close={() => setHistory(false)}
          selected={run ?? undefined}
        />
      )}
      <Module query={summary} label="预期研究文档">
        {(data) => (
          <ExpectationsBody
            key={`${ticker}:${data.run.run_id}`}
            ticker={ticker}
            data={data}
          />
        )}
      </Module>
    </>
  );
}
function ExpectationsBody({
  ticker,
  data,
}: {
  ticker: string;
  data: ExpectationsSummary;
}) {
  const [params, setParams] = useSearchParams();
  const [more, setMore] = useState(false);
  const shells = usePages<ShellSummary, "Shells">(
    "Shells",
    more
      ? tickerPath(ticker) +
          `/expectations/runs/${id(data.run.run_id)}/shells?limit=20`
      : null,
    (r) => r.data,
    undefined,
    data.shells,
  );
  const items = shells.data?.data.data?.items ?? data.shells.items;
  const shell =
    items.find((s) => s.shell_id === params.get("shell")) ?? items[0];
  const choose = (value: string) => {
    const p = new URLSearchParams(params);
    p.set("shell", value);
    p.delete("unit");
    p.delete("content");
    p.delete("item");
    setParams(p);
  };
  return (
    <>
      <div className="shell-tabs">
        {items.map((s) => (
          <button
            key={s.shell_id}
            aria-pressed={shell?.shell_id === s.shell_id}
            onClick={() => choose(s.shell_id)}
          >
            <span>{s.ordinal + 1}</span>
            {s.core_question}
            {s.status === "FAILED" && <em>失败</em>}
          </button>
        ))}
        {(shells.hasNextPage || (!more && data.shells.has_more)) && (
          <Button
            variant="outline"
            onClick={() => {
              if (!more) setMore(true);
              else void shells.fetchNextPage();
            }}
          >
            更多 Shell
          </Button>
        )}
      </div>
      {shell ? (
        <ShellBody
          key={shell.shell_id}
          ticker={ticker}
          data={data}
          shell={shell}
        />
      ) : (
        <Notice>文档中没有 Shell</Notice>
      )}
    </>
  );
}
function ShellBody({
  ticker,
  data,
  shell,
}: {
  ticker: string;
  data: ExpectationsSummary;
  shell: ShellSummary;
}) {
  const [params, setParams] = useSearchParams();
  const mode = params.get("content") ?? "ALL",
    unit = params.get("unit");
  const units = usePages<ExpectationUnit, "ShellContent">(
    "ShellContent",
    tickerPath(ticker) +
      `/expectations/runs/${id(data.run.run_id)}/shells/${id(shell.shell_id)}/units?limit=20`,
    (r) => r.data?.units ?? null,
  );
  const { hasNextPage, isFetching, error, fetchNextPage } = units;
  useEffect(() => {
    if (hasNextPage && !isFetching && !error) void fetchNextPage();
  }, [hasNextPage, isFetching, error, fetchNextPage]);
  const listed = units.data?.data.data?.items ?? [];
  const listedUnit = listed.find((u) => u.expectation_id === unit);
  const focused = useRead(
    "Unit",
    unit && units.data?.data.data && !listedUnit
      ? tickerPath(ticker) +
          `/expectations/runs/${id(data.run.run_id)}/shells/${id(shell.shell_id)}/units/${id(unit)}`
      : null,
  );
  const activeUnit = unit ? (listedUnit ?? focused.data?.data.data) : listed[0];
  const indexed =
    activeUnit && !listed.includes(activeUnit)
      ? [...listed, activeUnit]
      : listed;
  const selectedItem = params.get("item");
  useEffect(() => {
    if (selectedItem)
      document
        .getElementById("d2-" + selectedItem)
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selectedItem, mode, activeUnit]);
  const select = (mode: string, unit?: string, item?: string) => {
    const p = new URLSearchParams(params);
    p.set("content", mode);
    if (item) p.set("item", item);
    else p.delete("item");
    if (unit) p.set("unit", unit);
    else p.delete("unit");
    setParams(p);
  };
  return (
    <div className="document-layout">
      <div>
        {shell.status === "FAILED" && (
          <Notice danger>
            {shell.failure?.message ?? "Shell 研究失败"} · {shell.failed_stage}
          </Notice>
        )}
        <Module query={units} label="预期单元">
          {(page) => (
            <>
              {unit && !listedUnit && (
                <Module query={focused} label="指定预期单元">
                  {(u) => (
                    <Unit
                      unit={u}
                      mode={mode}
                      ticker={ticker}
                      content={shell.content.data}
                    />
                  )}
                </Module>
              )}
              {page.items
                .filter((u) => u.expectation_id === activeUnit?.expectation_id)
                .map((u) => (
                  <Unit
                    key={u.expectation_id}
                    unit={u}
                    mode={mode}
                    ticker={ticker}
                    content={shell.content.data}
                  />
                ))}
              {!page.items.length && <Notice>没有已发布的 Unit 内容</Notice>}
            </>
          )}
        </Module>
      </div>
      <aside className="reading-sidebar">
        <section className="document-meta">
          <h2>
            <FileCheck2 aria-hidden="true" />
            文档状态
          </h2>
          <dl>
            <dt>版本</dt>
            <dd>
              <span
                className={
                  "document-version" + (data.run.is_active ? " current" : "")
                }
              >
                {data.run.is_active ? "现行文档" : "历史文档"}
              </span>
            </dd>
            <dt>D2 运行状态</dt>
            <dd>{data.run.run_status}</dd>
            <dt>发布状态</dt>
            <dd>{data.run.publication_state ?? "未发布"}</dd>
            <dt>发布时间</dt>
            <dd>{valueText(data.run.published_at, formatInstant)}</dd>
          </dl>
        </section>
        <nav className="content-index" aria-label="Shell 内容路由">
          <h2>
            <Layers3 aria-hidden="true" />
            内容索引
          </h2>
          <h3>预期单元</h3>
          <div className="unit-picker">
            {indexed.map((u, index) => (
              <button
                key={u.expectation_id}
                aria-current={
                  activeUnit?.expectation_id === u.expectation_id
                    ? "page"
                    : undefined
                }
                onClick={() => select("ALL", u.expectation_id)}
              >
                <span className="unit-index-number">{index + 1}</span>
                <span>{u.proposition}</span>
              </button>
            ))}
          </div>
          {activeUnit && (
            <div className="unit-content-tree">
              {modes
                .filter(([key]) => key !== "ALL")
                .map(([key, label]) => {
                  const Icon = modeIcons[key];
                  const children =
                    key === "STATE"
                      ? activeUnit.state.values.map((v) => ({
                          id: v.state_value_id,
                          label:
                            (activeUnit.state.parameters.find(
                              (p) => p.parameter_id === v.parameter_id,
                            )?.definition ?? v.parameter_id) +
                            " · " +
                            roles[v.source_role],
                        }))
                      : key === "FACTORS"
                        ? activeUnit.realization_factors.map((f) => ({
                            id: f.factor_id,
                            label: f.condition,
                          }))
                        : activeUnit.potential_gaps.map((g) => ({
                            id: g.gap_id,
                            label: g.possible_occurrence,
                          }));
                  return (
                    <details key={key} open>
                      <summary>
                        <ChevronDown />
                        <Icon />
                        {label}
                        <small>{children.length}</small>
                      </summary>
                      {children.map((child) => (
                        <button
                          key={child.id}
                          className="tree-leaf"
                          title={child.label}
                          aria-current={
                            selectedItem === child.id ? "location" : undefined
                          }
                          onClick={() =>
                            select(key, activeUnit.expectation_id, child.id)
                          }
                        >
                          {child.label}
                        </button>
                      ))}
                    </details>
                  );
                })}
            </div>
          )}
        </nav>
      </aside>
    </div>
  );
}
const roles: Record<string, string> = {
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
function stateValue(type: ParameterType, value: StateValueData) {
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
function Unit({
  unit,
  mode,
  ticker,
  content,
}: {
  unit: ExpectationUnit;
  mode: string;
  ticker: string;
  content: ContentRef | null;
}) {
  return (
    <article className="unit-panel">
      <header>
        <h2>{unit.proposition}</h2>
        <span>
          <CalendarDays aria-hidden="true" />
          {unit.horizon}
        </span>
      </header>
      {["ALL", "STATE"].includes(mode) && (
        <section>
          <h3>
            <ChartNoAxesCombined aria-hidden="true" />
            当前状态<span>{unit.state.values.length}</span>
          </h3>
          <div className="domain-table-scroll">
            <table className="domain-table state-table">
              <colgroup>
                {["32%", "7%", "20%", "10%", "16%", "7%", "8%"].map(
                  (width, i) => (
                    <col key={i} style={{ width }} />
                  ),
                )}
              </colgroup>
              <thead>
                <tr>
                  {[
                    "参数",
                    "来源",
                    "当前值",
                    "前值",
                    "时间范围",
                    "有效性",
                    "引用",
                  ].map((x) => (
                    <th key={x}>{x}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {unit.state.values.map((v) => {
                  const parameter = unit.state.parameters.find(
                    (p) => p.parameter_id === v.parameter_id,
                  );
                  return (
                    <tr key={v.state_value_id} id={"d2-" + v.state_value_id}>
                      <td>{parameter?.definition ?? "参数定义未记录"}</td>
                      <td>{roles[v.source_role]}</td>
                      <td className="state-number">
                        {parameter
                          ? stateValue(parameter.value_type, v.value)
                          : "未记录"}
                      </td>
                      <td>
                        {parameter && v.previous_value
                          ? stateValue(parameter.value_type, v.previous_value)
                          : "—"}
                      </td>
                      <td>
                        {v.time_scope}
                        <small>{v.as_of}</small>
                      </td>
                      <td>{roles[v.validity_state]}</td>
                      <td>
                        <Citations
                          ticker={ticker}
                          content={content}
                          aliases={v.citation}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {!unit.state.values.length && <p>未记录当前状态</p>}
        </section>
      )}
      {["ALL", "FACTORS"].includes(mode) && (
        <section>
          <h3>
            <GitPullRequestArrow aria-hidden="true" />
            实现因素<span>{unit.realization_factors.length}</span>
          </h3>
          <div className="factor-grid">
            {unit.realization_factors.map((f) => (
              <article key={f.factor_id} id={"d2-" + f.factor_id}>
                <span className="domain-tag">{roles[f.structural_role]}</span>
                <h4>{f.condition}</h4>
                <dl>
                  <dt>当前状态</dt>
                  <dd>{f.current_status}</dd>
                  <dt>影响</dt>
                  <dd>{f.impact}</dd>
                  <dt>可观测条件</dt>
                  <dd>{f.observability.match_condition}</dd>
                </dl>
                <Citations
                  ticker={ticker}
                  content={content}
                  aliases={f.citation}
                />
              </article>
            ))}
          </div>
          {!unit.realization_factors.length && <p>未记录实现因素</p>}
        </section>
      )}
      {["ALL", "GAPS"].includes(mode) && (
        <section>
          <h3>
            <Split aria-hidden="true" />
            潜在差异<span>{unit.potential_gaps.length}</span>
          </h3>
          {unit.potential_gaps.map((g) => (
            <article className="gap-card" key={g.gap_id} id={"d2-" + g.gap_id}>
              <h4>{g.possible_occurrence}</h4>
              <p>{g.derivation}</p>
              <dl>
                <dt>预期修正</dt>
                <dd>{g.expected_revision}</dd>
                {g.recognition_criteria && (
                  <>
                    <dt>识别条件</dt>
                    <dd>{g.recognition_criteria}</dd>
                  </>
                )}
              </dl>
              <Citations
                ticker={ticker}
                content={content}
                aliases={g.citation}
              />
            </article>
          ))}
          {!unit.potential_gaps.length && <p>未记录潜在差异</p>}
        </section>
      )}
    </article>
  );
}

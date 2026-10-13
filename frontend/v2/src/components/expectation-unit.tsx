import {
  ChartNoAxesCombined,
  GitPullRequestArrow,
  Ruler,
  Split,
  CalendarDays,
} from "lucide-react";
import type {
  ExpectationUnitV21,
  ParameterType,
  StateValueData,
} from "@contract";
import { Fragment } from "react";
import { CitationText, ReferenceMarks } from "./citation-text";
import { roles, stateValue } from "@/core/expectation-format";

export const expectationModes = [
  "ALL",
  "STATE",
  "BASELINE",
  "FACTORS",
  "GAPS",
] as const;
export type ExpectationMode = (typeof expectationModes)[number];
type NativeState = ExpectationUnitV21["state"];
const Text = CitationText;
function inferredType(value: StateValueData): ParameterType {
  if ("number" in value) return "NUMBER";
  if ("lower" in value) return "RANGE";
  if ("stage" in value) return "STAGE";
  if ("direction" in value) return "DIRECTION";
  if ("stance" in value) return "EVIDENCE";
  return "TIME";
}
export function StateTable({ state }: { state: NativeState }) {
  const groups = new Map<string, NativeState["values"]>();
  for (const row of state.values)
    groups.set(row.parameter, [...(groups.get(row.parameter) ?? []), row]);
  const parameters = [...state.parameters];
  for (const [name, values] of groups)
    if (!parameters.some((p) => p.name === name))
      parameters.push({
        name,
        definition: "参数定义未提供",
        value_type: inferredType(values[0].value),
        ref: [],
      });
  return (
    <div className="domain-table-scroll d2-state-scroll">
      <table className="domain-table d2-state-table">
        <thead>
          <tr>
            {[
              "参数",
              "来源口径",
              "当前值",
              "先前值",
              "时间范围 · 截至",
              "有效性",
              "引用",
            ].map((label) => (
              <th scope="col" key={label}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {parameters.map((p, index) => {
            const rows = groups.get(p.name) ?? [];
            return (
              <Fragment key={p.name}>
                {(rows.length ? rows : [null]).map((v, i) => (
                  <tr
                    key={i}
                    id={i === 0 ? `d2-state-${index}` : undefined}
                    className={i === 0 ? "d2-parameter-start" : undefined}
                  >
                    {i === 0 && (
                      <th scope="rowgroup" rowSpan={Math.max(rows.length, 1)}>
                        <strong>
                          <Text text={p.name} />
                        </strong>
                        {p.definition && (
                          <details className="d2-definition">
                            <summary>定义</summary>
                            <Text text={p.definition} />
                          </details>
                        )}
                        <ReferenceMarks refs={p.ref} />
                      </th>
                    )}
                    <td>{v ? (roles[v.source_role] ?? v.source_role) : "—"}</td>
                    <td
                      className={
                        ["NUMBER", "RANGE"].includes(p.value_type)
                          ? "d2-numeric"
                          : undefined
                      }
                    >
                      {v ? (
                        <>
                          <Text text={stateValue(p.value_type, v.value)} />
                          {v.name && (
                            <small>
                              <Text text={v.name} />
                            </small>
                          )}
                        </>
                      ) : (
                        "未提供"
                      )}
                    </td>
                    <td
                      className={
                        ["NUMBER", "RANGE"].includes(p.value_type)
                          ? "d2-numeric"
                          : undefined
                      }
                    >
                      {v?.previous_value ? (
                        <Text
                          text={stateValue(p.value_type, v.previous_value)}
                        />
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      {v ? (
                        <>
                          <Text text={v.time_scope} />
                          <small>{v.as_of}</small>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      {v ? (
                        <span
                          className="d2-validity"
                          data-state={v.validity_state}
                        >
                          {roles[v.validity_state] ?? v.validity_state}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{v ? <ReferenceMarks refs={v.ref} /> : "—"}</td>
                  </tr>
                ))}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      {!parameters.length && <p className="d2-empty">未提供当前状态</p>}
    </div>
  );
}
function Fields({ entries }: { entries: Array<[string, string]> }) {
  return (
    <dl className="d2-fields">
      {entries.map(([label, value]) => (
        <Fragment key={label}>
          <dt>{label}</dt>
          <dd>
            <Text text={value || "未提供"} />
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}
export function BaselinePanel({
  baseline: b,
  index,
}: {
  baseline: ExpectationUnitV21["expectation_baseline"][number];
  index: number;
}) {
  return (
    <article id={`d2-baseline-${index}`} className="d2-baseline-panel">
      <header>
        <h4>
          <Text text={b.name} />
        </h4>
        <span>{b.time_scope}</span>
      </header>
      <Fields
        entries={[
          ["当前预期", b.baseline],
          ["正常推进", b.ordinary_progress],
          ["开放前沿", b.open_frontier],
        ]}
      />
      <ReferenceMarks refs={b.ref} />
    </article>
  );
}
export function FactorCard({
  factor: f,
  index,
}: {
  factor: ExpectationUnitV21["realization_factors"][number];
  index: number;
}) {
  return (
    <article id={`d2-factors-${index}`} className="d2-factor-card">
      <h4>
        <Text text={f.name} />
      </h4>
      <Fields
        entries={[
          ["作用机制", f.mechanism],
          ["当前进展", f.current_status],
          ["影响分量", f.materiality_context],
          ["适用边界", f.scope_boundary],
        ]}
      />
      <ReferenceMarks refs={f.ref} />
    </article>
  );
}
export function GapCard({
  gap: g,
  index,
}: {
  gap: ExpectationUnitV21["potential_gaps"][number];
  index: number;
}) {
  return (
    <article id={`d2-gaps-${index}`} className="d2-gap-card">
      <h4>
        <Text text={g.name} />
      </h4>
      <Fields
        entries={[
          ["仍开放的原因", g.why_live],
          ["预期修订逻辑", g.revision_logic],
        ]}
      />
      {g.possibility_space.length > 0 && (
        <div className="d2-possibilities">
          <h5>可能演化路径</h5>
          <ol>
            {g.possibility_space.map((p, i) => (
              <li key={i}>
                <h6>
                  <Text text={p.name} />
                </h6>
                <Fields entries={[["预期影响", p.implication]]} />
              </li>
            ))}
          </ol>
        </div>
      )}
      <ReferenceMarks refs={g.ref} />
    </article>
  );
}
export function UnitV21({
  unit,
  mode,
}: {
  unit: ExpectationUnitV21;
  mode: ExpectationMode;
}) {
  return (
    <article className="unit-panel d2-native-unit">
      <header>
        <h2>
          <Text text={unit.name} />
        </h2>
        <span>
          <CalendarDays aria-hidden="true" />
          {unit.horizon}
        </span>
      </header>
      <div className="d2-unit-overview">
        <p>
          <Text text={unit.scope} />
        </p>
        <ReferenceMarks refs={unit.ref} />
      </div>
      {["ALL", "STATE"].includes(mode) && (
        <section>
          <h3>
            <ChartNoAxesCombined aria-hidden="true" />
            当前状态
          </h3>
          <StateTable state={unit.state} />
        </section>
      )}
      {["ALL", "BASELINE"].includes(mode) && (
        <section>
          <h3>
            <Ruler aria-hidden="true" />
            预期基线
          </h3>
          {unit.expectation_baseline.map((b, i) => (
            <BaselinePanel key={i} baseline={b} index={i} />
          ))}
        </section>
      )}
      {["ALL", "FACTORS"].includes(mode) && (
        <section>
          <h3>
            <GitPullRequestArrow aria-hidden="true" />
            实现因素
          </h3>
          {unit.realization_factors.map((f, i) => (
            <FactorCard key={i} factor={f} index={i} />
          ))}
        </section>
      )}
      {["ALL", "GAPS"].includes(mode) && (
        <section>
          <h3>
            <Split aria-hidden="true" />
            潜在差异
          </h3>
          {unit.potential_gaps.map((g, i) => (
            <GapCard key={i} gap={g} index={i} />
          ))}
        </section>
      )}
    </article>
  );
}

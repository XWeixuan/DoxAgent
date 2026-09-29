import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import type { MonitoringTermsConfig, MonitoringTermsValue, MonitoringL2Term } from "@contract";
import { useRead, tickerPath, unwrap } from "@/core/page-query";
import { useRuntime } from "@/core/runtime";
import { ApiFailure } from "@/core/api";
import { blankTerms, newTerm, ruleSummary, sameTerms, parseRulesJson } from "@/core/monitoring-terms";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/state";

const fields = [["all", "全部"], ["title", "标题"], ["summary", "摘要"], ["body", "正文"]] as const;
const buckets = [["any", "至少命中一项"], ["all", "必须全部命中"], ["none", "排除任一项"]] as const;
type Bucket = (typeof buckets)[number][0];

export default function MonitoringTermsSettings({ ticker }: { ticker: string }) {
  const { api, query, scope } = useRuntime();
  const draftKey = `monitoring-terms:${scope}:${ticker}`;
  const path = tickerPath(ticker) + "/message-bus/monitoring-terms";
  const read = useRead("MonitoringTerms", path);
  const saved = unwrap<MonitoringTermsConfig>(read.data);
  const [baseline, setBaseline] = useState<MonitoringTermsConfig>();
  const [draft, setDraft] = useState<MonitoringTermsValue>();
  const [language, setLanguage] = useState("en");
  const [mode, setMode] = useState<"RULES" | "JSON">("RULES");
  const [jsonText, setJsonText] = useState("");
  const [jsonInvalid, setJsonInvalid] = useState(false);
  const [error, setError] = useState("");
  const [issues, setIssues] = useState<{ path: string; message: string }[]>([]);
  const [preview, setPreview] = useState<MonitoringTermsConfig["consumers"]>([]);
  const [busy, setBusy] = useState(false);
  const retry = useRef<{ signature: string; key: string } | undefined>(undefined);
  const dirty = draft && baseline && !sameTerms(draft, baseline.configuration);

  useEffect(() => {
    if (!saved) return;
    let stored: { baseline: MonitoringTermsConfig; draft: MonitoringTermsValue } | undefined;
    try { stored = JSON.parse(sessionStorage.getItem(draftKey) ?? "null") ?? undefined; } catch { /* ignore damaged draft */ }
    if (stored?.baseline?.ticker === ticker && stored.draft) {
      setBaseline(stored.baseline);
      setDraft(stored.draft);
    } else {
      setBaseline(saved);
      setDraft((current) => current && !sameTerms(current, baseline?.configuration ?? null)
        ? current : structuredClone(saved.configuration ?? blankTerms(saved.required_languages)));
    }
    if (mode === "JSON" && !dirty) setJsonText(JSON.stringify(saved.configuration?.l2 ?? {}, null, 2));
  // A successful save explicitly resets the draft; refetches preserve edits.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved]);
  useEffect(() => {
    if (dirty && baseline && draft) sessionStorage.setItem(draftKey, JSON.stringify({ baseline, draft }));
    else sessionStorage.removeItem(draftKey);
  }, [dirty, baseline, draft, draftKey]);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (dirty) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  if (read.isPending && !draft) return <section className="terms-settings">正在加载统一监测词…</section>;
  if (read.error && !draft) return <section className="terms-settings"><Notice danger>{read.error.message}</Notice><Button onClick={() => void read.refetch()}>重试</Button></section>;
  if (!draft || !baseline) return null;

  const edit = (change: (value: MonitoringTermsValue) => void) => {
    setDraft((prior) => {
      if (!prior) return prior;
      const next = structuredClone(prior);
      change(next);
      return next;
    });
    setIssues([]); setPreview([]); setError("");
  };
  const languages = Array.from(new Set([...baseline.required_languages, ...Object.keys(draft.l2), ...draft.l1_concepts.flatMap((c) => Object.keys(c.expressions))])).sort();
  const remoteChanges = saved && saved.revision !== baseline.revision ? [
    ["搜索监测词", "l1_concepts"], ["分发匹配规则", "l2"], ["Jev 定义", "definition"],
  ].filter(([, key]) => JSON.stringify(baseline.configuration?.[key as keyof MonitoringTermsValue]) !== JSON.stringify(saved.configuration?.[key as keyof MonitoringTermsValue])).map(([label]) => label) : [];
  const check = async () => {
    setError(""); setBusy(true);
    try {
      const result = await api.request("MonitoringTermsValidation", path + "/validate", { method: "POST", body: { configuration: draft } });
      setIssues(result.data.issues);
      setPreview(result.data.search_previews);
      if (result.data.valid) toast.success("监测词校验通过");
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  const apply = async () => {
    if (!dirty) return;
    setError(""); setBusy(true);
    const signature = JSON.stringify([baseline.control_etag, draft]);
    if (retry.current?.signature !== signature) retry.current = { signature, key: crypto.randomUUID() };
    try {
      const result = await api.request("MonitoringTermsWrite", path, {
        method: "PUT", body: { configuration: draft }, etag: baseline.control_etag, key: retry.current.key,
      });
      const next = result.data;
      retry.current = undefined;
      sessionStorage.removeItem(draftKey);
      setBaseline(next); setDraft(structuredClone(next.configuration!));
      setIssues([]); setPreview([]);
      toast.success(`监测词已应用 · 版本 ${next.revision}`);
      await query.invalidateQueries({ predicate: (item) => item.queryKey[0] === scope && ["MonitoringTerms", "Binding"].includes(String(item.queryKey[2])) });
    } catch (cause) {
      const failure = cause as ApiFailure;
      setIssues(failure.fields ?? []);
      setError(failure.status === 412 ? "词表已被其他会话更新。请读取最新版本并检查差异后重新应用。" : failure.message);
      if (failure.status === 412) void read.refetch();
    } finally { setBusy(false); }
  };
  const rule = draft.l2[language]?.groups ?? [];
  const setTerm = (group: number, bucket: Bucket, number: number, change: (term: MonitoringL2Term) => void) => edit((next) => change(next.l2[language].groups[group][bucket][number]));
  return <section className="terms-settings" aria-label={`${ticker} 统一监测词`}>
    <header><h2>{ticker} 统一监测词</h2><span>版本 {baseline.revision || "未配置"}</span></header>
    {saved && saved.revision !== baseline.revision && dirty && <Notice>远端已有版本 {saved.revision}，变更模块：{remoteChanges.join("、") || "配置元数据"}。当前草稿已保留。</Notice>}
    <section className="terms-section" id="monitoring-keywords">
      <h3>搜索监测词 · By Keyword</h3>
      <div className="terms-table-wrap"><table><thead><tr><th>概念</th>{languages.map((lang) => <th key={lang}>{lang}</th>)}<th>操作</th></tr></thead><tbody>
        {draft.l1_concepts.map((concept, index) => <tr key={concept.concept_id}><td>{index + 1}</td>{languages.map((lang) => <td key={lang}><input aria-label={`概念 ${index + 1} ${lang}`} value={concept.expressions[lang] ?? ""} onChange={(event) => edit((next) => { next.l1_concepts[index].expressions[lang] = event.target.value; })} /></td>)}<td><Button variant="outline" disabled={draft.l1_concepts.length === 1} onClick={() => edit((next) => { next.l1_concepts.splice(index, 1); })}>删除</Button></td></tr>)}
      </tbody></table></div>
      <div className="terms-actions"><Button variant="outline" disabled={draft.l1_concepts.length >= 3} onClick={() => edit((next) => { next.l1_concepts.push({ concept_id: crypto.randomUUID(), expressions: Object.fromEntries(languages.map((lang) => [lang, ""])) }); })}>新增概念</Button>
      <input aria-label="新增语言代码" placeholder="语言代码，如 ko" id="new-terms-language" />
      <Button variant="outline" onClick={() => { const input = document.getElementById("new-terms-language") as HTMLInputElement; const lang = input.value.trim(); if (!lang || languages.includes(lang)) return; edit((next) => { next.l2[lang] = { groups: [] }; next.l1_concepts.forEach((concept) => { concept.expressions[lang] = ""; }); }); input.value = ""; }}>添加语言</Button></div>
      {languages.filter((lang) => !baseline.required_languages.includes(lang)).map((lang) => <Button key={lang} variant="outline" onClick={() => edit((next) => { delete next.l2[lang]; next.l1_concepts.forEach((concept) => { delete concept.expressions[lang]; }); if (language === lang) setLanguage("en"); })}>移除 {lang}</Button>)}
    </section>
    <section className="terms-section" id="monitoring-distribution">
      <h3>分发匹配规则 · By Distribution</h3>
      <div className="terms-actions"><label>语言 <select value={language} onChange={(event) => setLanguage(event.target.value)}>{languages.map((lang) => <option key={lang}>{lang}</option>)}</select></label><Button variant={mode === "RULES" ? "default" : "outline"} onClick={() => {
        if (mode === "JSON") { try { parseRulesJson(jsonText); setMode("RULES"); } catch (cause) { setError((cause as Error).message); } }
        else setMode("RULES");
      }}>规则视图</Button><Button variant={mode === "JSON" ? "default" : "outline"} onClick={() => { if (mode !== "JSON") { setJsonText(JSON.stringify(draft.l2, null, 2)); setJsonInvalid(false); setMode("JSON"); } }}>原始 JSON</Button></div>
      {mode === "JSON" ? <textarea aria-label="分发规则 JSON" className="terms-json" spellCheck={false} value={jsonText} onChange={(event) => { const text = event.target.value; setJsonText(text); try { const parsed = parseRulesJson(text); edit((next) => { next.l2 = parsed; }); setJsonInvalid(false); } catch (cause) { setJsonInvalid(true); setError((cause as Error).message); } }} /> : <>
        <p className="terms-note">当前语言与英语规则取并集；满足任一规则组即可。排除项只作用于所在组。</p>
        {rule.map((group, index) => <details key={index} className="terms-rule-group"><summary>{group.id} · {ruleSummary(group)}</summary><div className="terms-actions"><label>组 ID <input value={group.id} onChange={(event) => edit((next) => { next.l2[language].groups[index].id = event.target.value; })} /></label><Button variant="outline" onClick={() => edit((next) => { next.l2[language].groups.splice(index, 1); })}>删除组</Button></div>
          {buckets.map(([bucket, title]) => <div key={bucket} className="terms-rule-bucket"><h4>{title}</h4>{group[bucket].map((term, number) => <div className="terms-rule-row" key={number}>
            <select aria-label="条件类型" value={term.regex === null ? "literal" : "regex"} onChange={(event) => setTerm(index, bucket, number, (value) => { const text = value.literal ?? value.regex ?? ""; value.literal = event.target.value === "literal" ? text : null; value.regex = event.target.value === "regex" ? text : null; })}><option value="literal">文本</option><option value="regex">正则</option></select>
            <input aria-label={`${title} 条件 ${number + 1}`} value={term.literal ?? term.regex ?? ""} onChange={(event) => setTerm(index, bucket, number, (value) => { if (value.regex === null) value.literal = event.target.value; else value.regex = event.target.value; })} />
            <select aria-label="匹配范围" value={term.field} onChange={(event) => setTerm(index, bucket, number, (value) => { value.field = event.target.value as MonitoringL2Term["field"]; })}>{fields.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
            <label><input type="checkbox" checked={term.case_sensitive} onChange={(event) => setTerm(index, bucket, number, (value) => { value.case_sensitive = event.target.checked; })} />区分大小写</label>
            <label title={term.regex !== null ? "仅对文本条件生效" : undefined}><input type="checkbox" disabled={term.regex !== null} checked={term.whole_word} onChange={(event) => setTerm(index, bucket, number, (value) => { value.whole_word = event.target.checked; })} />整词</label>
            <Button variant="outline" onClick={() => edit((next) => { next.l2[language].groups[index][bucket].splice(number, 1); })}>删除</Button>
          </div>)}<Button variant="outline" onClick={() => edit((next) => { next.l2[language].groups[index][bucket].push(newTerm()); })}>添加条件</Button></div>)}
        </details>)}
        <Button variant="outline" onClick={() => edit((next) => { next.l2[language] ??= { groups: [] }; next.l2[language].groups.push({ id: crypto.randomUUID(), any: [newTerm()], all: [], none: [] }); })}>新增规则组</Button>
      </>}
    </section>
    <section className="terms-section" id="monitoring-jev"><h3>Jev 相关性定义 · By Distribution</h3>
      <div className="terms-jev"><label>相关条件<textarea value={draft.definition.relevant} onChange={(event) => edit((next) => { next.definition.relevant = event.target.value; })} /></label><label>不相关条件<textarea value={draft.definition.irrelevant} onChange={(event) => edit((next) => { next.definition.irrelevant = event.target.value; })} /></label></div>
      <p className="terms-note">{baseline.consumers.filter((c) => c.acquisition_mode === "by_distribution").map((c) => `${c.name}：源配置${c.jev_source_enabled ? "已开启" : "未开启"}`).join("；") || "尚无分发消息源"}。实际运行还取决于 Worker 配置。</p>
    </section>
    {issues.length > 0 && <Notice danger><ul>{issues.map((issue, index) => <li key={index}>{issue.path}：{issue.message}</li>)}</ul></Notice>}
    {error && <Notice danger>{error}</Notice>}
    {saved && saved.revision !== baseline.revision && <div className="terms-actions"><Button variant="outline" onClick={() => { setBaseline(saved); setDraft(structuredClone(saved.configuration ?? blankTerms(saved.required_languages))); setError(""); retry.current = undefined; }}>读取最新版本</Button><Button variant="outline" onClick={() => { setBaseline(saved); retry.current = undefined; setError(""); }}>基于最新版本应用我的草稿</Button></div>}
    <div className="terms-actions"><Button variant="outline" disabled={busy || jsonInvalid} onClick={() => void check()}>检查并预览</Button><Button disabled={!dirty || busy || jsonInvalid} onClick={() => void apply()}>{busy ? "处理中" : "应用更新"}</Button><Button variant="outline" disabled={!dirty || busy} onClick={() => { sessionStorage.removeItem(draftKey); setDraft(structuredClone(baseline.configuration ?? blankTerms(baseline.required_languages))); setJsonText(JSON.stringify(baseline.configuration?.l2 ?? {}, null, 2)); setJsonInvalid(false); setError(""); setIssues([]); retry.current = undefined; }}>放弃修改</Button></div>
    {preview.length > 0 && <div className="terms-previews"><h4>实际搜索语句预览</h4>{preview.map((c) => <div key={c.binding_id}><strong>{c.name} · {c.content_language ?? "en"} · {c.search_policy_mode ?? "separate"}{!c.binding_enabled || !c.source_enabled ? " · 已停用" : ""}</strong>{c.queries.map((q) => <code key={q.query_key}>{q.query}</code>)}{c.preview_issue && <span>{c.preview_issue}</span>}</div>)}</div>}
  </section>;
}

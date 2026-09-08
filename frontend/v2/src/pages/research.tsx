import { useState, useEffect } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  History as HistoryIcon,
  Building2,
  Factory,
  ScanLine,
  CalendarDays,
} from "lucide-react";
import type { ResearchSummary, FutureNodeRow } from "@contract";
import { usePages } from "@/core/paged-query";
import { usePageContext, useRead, tickerPath, id } from "@/core/page-query";
import { queryString } from "@/core/api";
import { formatInstant, valueText } from "@/core/format";
import { Module, Notice } from "@/components/state";
import { Button } from "@/components/ui/button";
import {
  PageTitle,
  Choices,
  History,
  RunInfo,
  ContentReader,
  DownloadButton,
} from "@/components/page-kit";
const sectionIcons = {
  C1: Building2,
  C3: Factory,
  C5: ScanLine,
  FUTURE_NODES: CalendarDays,
};
const sections = [
  ["C1", "基本面研究"],
  ["C3", "行业研究"],
  ["C5", "市场隐含预期研究"],
  ["FUTURE_NODES", "未来节点"],
] as const;
export default function Research() {
  const { ticker = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const section =
      sections.find(([v]) => v === params.get("section"))?.[0] ?? "C1",
    run = params.get("run");
  const [history, setHistory] = useState(false);
  const context = usePageContext(ticker, "RESEARCH");
  const view = context.data?.data.view_id;
  const summary = useRead(
    "Research",
    run
      ? tickerPath(ticker) + `/research/runs/${id(run)}`
      : view
        ? tickerPath(ticker) +
          "/research/current" +
          queryString({ view_id: view })
        : null,
    run ? undefined : view,
  );
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };
  return (
    <>
      <PageTitle title={`${ticker} 基础投研`} refresh={context.refetch}>
        <Button variant="outline" onClick={() => setHistory(!history)}>
          <HistoryIcon />
          历史文档
        </Button>
        {run && (
          <Button variant="outline" onClick={() => set("run", "")}>
            返回现行
          </Button>
        )}
      </PageTitle>
      <div className="center-tabs">
        <Choices
          value={section}
          onChange={(v) => set("section", v)}
          items={sections}
          label="Document 1 内容"
          icons={sectionIcons}
        />
      </div>
      {context.error && <Notice danger>{context.error.message}</Notice>}
      {history && (
        <History
          ticker={ticker}
          kind="research"
          close={() => setHistory(false)}
          selected={run ?? undefined}
          onSelect={(v) => {
            set("run", v);
            setHistory(false);
          }}
        />
      )}
      <Module query={summary} label="基础投研文档">
        {(data) => (
          <ResearchBody
            key={`${ticker}:${data.run.run_id}`}
            ticker={ticker}
            data={data}
            section={section}
          />
        )}
      </Module>
    </>
  );
}
function ResearchBody({
  ticker,
  data,
  section,
}: {
  ticker: string;
  data: ResearchSummary;
  section: string;
}) {
  const selected = data.sections.find((s) => s.section === section)?.content;
  return (
    <div className="document-layout">
      <section className="report-panel">
        {section === "FUTURE_NODES" ? (
          <FutureNodes ticker={ticker} run={data.run.run_id} />
        ) : selected?.data ? (
          <ContentReader
            loadFully
            key={selected.data.content_id}
            ticker={ticker}
            content={selected.data}
          />
        ) : (
          <Notice>{selected?.reason ?? "该报告尚未生成"}</Notice>
        )}
      </section>
      <RunInfo
        run={data.run}
        updated={valueText(data.updated_at, formatInstant)}
      >
        <dt>Global Research</dt>
        <dd>{data.global_research_status}</dd>
        <dt>文档 ID</dt>
        <dd className="identifier">
          {data.document.data?.artifact_id ?? "未生成"}
        </dd>
        <dt>下载</dt>
        <dd>
          <DownloadButton
            path={
              tickerPath(ticker) +
              `/research/runs/${id(data.run.run_id)}/download`
            }
          />
        </dd>
      </RunInfo>
    </div>
  );
}
function FutureNodes({ ticker, run }: { ticker: string; run: string }) {
  const response = usePages<FutureNodeRow, "FutureNodes">(
    "FutureNodes",
    tickerPath(ticker) +
      `/research/runs/${id(run)}/future-nodes` +
      queryString({ limit: "20" }),
    (resource) => resource.data,
  );
  const { hasNextPage, isFetching, error, fetchNextPage } = response;
  useEffect(() => {
    if (hasNextPage && !isFetching && !error) void fetchNextPage();
  }, [hasNextPage, isFetching, error, fetchNextPage]);
  return (
    <Module query={response} label="未来节点">
      {(page) => (
        <>
          <div className="future-nodes">
            {page.items.map((node) => (
              <article key={node.item_key}>
                <time>{node.time}</time>
                <div>
                  <h2>{node.future_event}</h2>
                  <p>{node.relationship_to_target}</p>
                  <p className="muted">
                    {node.source} · {node.source_published_at}
                  </p>
                </div>
              </article>
            ))}
          </div>
          {!page.items.length && <p>暂无未来节点</p>}
        </>
      )}
    </Module>
  );
}

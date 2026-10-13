import { useState, useEffect, useMemo, type ReactNode } from "react";
import { Dialog } from "radix-ui";
import {
  RefreshCw,
  Download,
  ChevronDown,
  FileCheck2,
  X,
  History as HistoryIcon,
} from "lucide-react";
import type { ContentRef, ContentChunk, RunSummary, Json } from "@contract";
import { Button } from "./ui/button";
import { ToggleGroup, ToggleGroupItem } from "./ui/toggle-group";
import { Notice, Module } from "./state";
import { useRead, tickerPath, id } from "@/core/page-query";
import { useRuntime } from "@/core/runtime";
import { saveArtifact, appendContent } from "@/core/content";
import { queryString } from "@/core/api";
export function DetailPanel({
  label,
  className,
  close,
  children,
}: {
  label: string;
  className: string;
  close: () => void;
  children: ReactNode;
}) {
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="detail-overlay" />
        <Dialog.Content asChild aria-describedby={undefined}>
          <aside className={className}>
            <Dialog.Title className="sr-only">{label}</Dialog.Title>
            {children}
          </aside>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
import { formatInstant, valueText } from "@/core/format";
import { Document } from "./document";
import { Citations } from "./citations";

export function PageTitle({
  title,
  refresh,
  children,
}: {
  title: string;
  refresh: () => Promise<unknown>;
  children?: ReactNode;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <>
      <div className="business-heading">
        {title && <h1>{title}</h1>}
        <div className="business-actions">
          {children}
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              setError("");
              void refresh()
                .catch((e) => setError(e.message))
                .finally(() => setBusy(false));
            }}
          >
            <RefreshCw className={busy ? "spin" : ""} />
            刷新
          </Button>
        </div>
      </div>
      {error && <Notice danger>{error}</Notice>}
    </>
  );
}
export function Choices({
  value,
  onChange,
  items,
  label,
  disabled = [],
  icons,
}: {
  value: string;
  onChange: (v: string) => void;
  items: readonly (readonly [string, string])[];
  label: string;
  disabled?: readonly string[];
  icons?: Record<string, import("lucide-react").LucideIcon>;
}) {
  return (
    <ToggleGroup
      type="single"
      value={value}
      onValueChange={(v) => v && onChange(v)}
      aria-label={label}
      className="business-tabs"
    >
      {items.map(([key, text]) => (
        <ToggleGroupItem
          key={key}
          value={key}
          disabled={disabled.includes(key)}
        >
          {icons?.[key] &&
            (() => {
              const Icon = icons[key];
              return <Icon aria-hidden="true" />;
            })()}
          {text}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
export function DownloadButton({ path }: { path: string }) {
  const { api } = useRuntime();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Button
        variant="outline"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          setError("");
          void saveArtifact(api, path)
            .catch((e) => setError(e.message))
            .finally(() => setBusy(false));
        }}
      >
        <Download />
        下载
      </Button>
      {error && <Notice danger>{error}</Notice>}
    </>
  );
}
export function History({
  ticker,
  kind,
  onSelect,
  close,
  selected,
}: {
  close: () => void;
  selected?: string;
  ticker: string;
  kind: "research" | "expectations";
  onSelect: (run: string) => void;
}) {
  const [cursor, setCursor] = useState<string>();
  const query = useRead(
    "Runs",
    tickerPath(ticker) + `/${kind}/runs` + queryString({ limit: "20", cursor }),
  );
  return (
    <DetailPanel className="history-drawer" label="历史文档" close={close}>
      <header>
        <h2>
          <HistoryIcon />
          历史文档
        </h2>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="关闭历史文档"
          onClick={close}
        >
          <X />
        </Button>
      </header>
      <Button variant="outline" onClick={() => onSelect("")}>
        查看现行文档
      </Button>
      <Module query={query} label="历史文档">
        {(page) => (
          <>
            {page.items.length === 0 && <Notice>尚无历史文档记录</Notice>}
            <div className="history-list">
              {page.items.map((run) => (
                <button
                  key={run.run_id}
                  aria-pressed={selected === run.run_id}
                  onClick={() => onSelect(run.run_id)}
                >
                  <span className="history-card-type">
                    <FileCheck2 />
                    {run.is_active ? "现行文档" : "历史文档"}
                  </span>
                  <strong>
                    {kind === "expectations"
                      ? valueText(run.published_at, formatInstant)
                      : formatInstant(run.created_at)}
                  </strong>
                  <span>
                    {kind === "expectations"
                      ? run.publication_state
                      : run.run_status}
                  </span>
                  <small>生成 {formatInstant(run.created_at)}</small>
                  {run.quality_annotations.length > 0 && (
                    <small>{run.quality_annotations.join(" · ")}</small>
                  )}
                </button>
              ))}
            </div>
            {page.has_more && (
              <Button
                variant="outline"
                onClick={() => setCursor(page.next_cursor!)}
              >
                下一页
              </Button>
            )}
          </>
        )}
      </Module>
    </DetailPanel>
  );
}
export function RunInfo({
  run,
  updated,
  children,
}: {
  run: RunSummary;
  updated?: string;
  children?: ReactNode;
}) {
  return (
    <aside className="document-meta">
      <h2>
        <FileCheck2 aria-hidden="true" />
        文档状态
      </h2>
      <dl>
        <dt>版本</dt>
        <dd>
          <span
            className={
              run.is_active ? "document-version current" : "document-version"
            }
          >
            {run.is_active ? "现行文档" : "历史文档"}
          </span>
        </dd>
        <dt>Document Run</dt>
        <dd>{run.run_status}</dd>
        <dt>发布状态</dt>
        <dd>{run.publication_status}</dd>
        <dt>运行 ID</dt>
        <dd className="identifier">{run.run_id}</dd>
        <dt>生成时间</dt>
        <dd>{formatInstant(run.created_at)}</dd>
        {updated !== undefined && (
          <>
            <dt>更新时间</dt>
            <dd>{updated}</dd>
          </>
        )}
        {children}
      </dl>
      {run.quality_annotations.length > 0 && (
        <Notice>{run.quality_annotations.join(" · ")}</Notice>
      )}
    </aside>
  );
}
const fieldNames: Record<string, string> = {
  title: "标题",
  summary: "摘要",
  description: "说明",
  research_text: "研究正文",
  content: "正文",
  sections: "章节",
  name: "名称",
  source: "来源",
  date: "日期",
  reasoning: "分析依据",
  value: "数值",
  citations: "引用",
  future_event: "未来事件",
  time: "时间",
  relationship_to_target: "标的关联",
  source_published_at: "来源发布时间",
};
export function StructuredDocument({
  value,
  depth = 0,
}: {
  value: Json;
  depth?: number;
}) {
  if (value === null) return <span className="muted">未记录</span>;
  if (typeof value !== "object")
    return typeof value === "string" ? (
      <Document markdown={value} />
    ) : (
      <span>{String(value)}</span>
    );
  if (Array.isArray(value))
    return (
      <div className="structured-list">
        {value.map((item, i) => (
          <section key={i}>
            <StructuredDocument value={item} depth={depth + 1} />
          </section>
        ))}
      </div>
    );
  return (
    <div className="structured-document">
      {Object.entries(value).map(([key, item]) => (
        <details key={key} open={depth < 2}>
          <summary>{fieldNames[key] ?? key.replace(/_/g, " ")}</summary>
          <StructuredDocument value={item} depth={depth + 1} />
        </details>
      ))}
    </div>
  );
}
const emptyChunks: ContentChunk[] = [];
export function ContentReader({
  ticker,
  content,
  presentation = "document",
  collapsed = false,
  loadFully = false,
}: {
  ticker: string;
  content: ContentRef;
  presentation?: "document" | "message";
  collapsed?: boolean;
  loadFully?: boolean;
}) {
  const [cursor, setCursor] = useState<string>();
  const { query: cache, scope } = useRuntime();
  const key = useMemo(
    () => [scope, "content-parts", ticker, content.content_id, content.sha256],
    [scope, ticker, content.content_id, content.sha256],
  );
  const response = useRead(
    "Content",
    tickerPath(ticker) +
      `/contents/${id(content.content_id)}` +
      queryString({ cursor }),
  );
  const previous = cache.getQueryData<ContentChunk[]>(key) ?? emptyChunks;
  const chunk = response.data?.data.data;
  let chunks = previous,
    error = "";
  if (chunk && !previous.some((c) => c.chunk_index === chunk.chunk_index)) {
    try {
      chunks = appendContent(previous, chunk, content);
    } catch (e) {
      error = (e as Error).message;
    }
  }
  useEffect(() => {
    if (chunks !== previous) cache.setQueryData(key, chunks);
  }, [cache, key, chunks, previous]);
  const text = chunks.map((c) => c.text).join("");
  const next = chunks.at(-1);
  useEffect(() => {
    if (
      loadFully &&
      !error &&
      !response.error &&
      !response.isFetching &&
      next &&
      !next.complete &&
      next.next_cursor &&
      next.next_cursor !== cursor
    ) {
      setCursor(next.next_cursor);
    }
  }, [loadFully, error, response.error, response.isFetching, next, cursor]);
  let json: Json | undefined;
  if (content.content_type === "application/json" && chunks.at(-1)?.complete) {
    try {
      json = JSON.parse(text);
    } catch {
      error = "结构化正文格式无法解析。";
    }
  }
  return (
    <div className="content-reader">
      {error ? (
        <Notice danger>{error}</Notice>
      ) : chunks.length > 0 ? (
        <>
          {response.error && (
            <Notice danger>
              {response.error.message}
              <Button variant="link" onClick={() => void response.refetch()}>
                重试
              </Button>
            </Notice>
          )}
          {content.content_type === "application/json" ? (
            json !== undefined ? (
              <StructuredDocument value={json} />
            ) : (
              <p>正文尚未加载完整</p>
            )
          ) : presentation === "message" ? (
            <Document markdown={text} />
          ) : (
            <SectionedMarkdown text={text} />
          )}
        </>
      ) : (
        <Module query={response} label="正文">
          {() => null}
        </Module>
      )}
      {!loadFully &&
        !collapsed &&
        chunks.length > 0 &&
        !chunks.at(-1)!.complete && (
          <Button
            variant="outline"
            disabled={response.isFetching}
            onClick={() => setCursor(chunks.at(-1)!.next_cursor!)}
          >
            继续加载正文
          </Button>
        )}
      {presentation === "document" && (
        <Citations
          key={content.content_id}
          ticker={ticker}
          content={content}
          aliases={[
            ...new Set(
              [...text.matchAll(/【cite:([^】]+)】/g)].map((m) => m[1]),
            ),
          ]}
        />
      )}
    </div>
  );
}
function SectionedMarkdown({ text }: { text: string }) {
  const blocks = text.split(/(?=^#{1,3}\s)/m);
  return (
    <div className="report-sections">
      {blocks.map((block, i) => {
        const heading = /^(#{1,3})\s+(.+)\n?/.exec(block);
        return heading && !block.slice(heading[0].length).trim() ? (
          <h2 className="report-title" key={i}>
            {heading[2]}
          </h2>
        ) : heading ? (
          <details open key={i}>
            <summary>
              <ChevronDown aria-hidden="true" />
              <span>{heading[2]}</span>
            </summary>
            <Document markdown={block.slice(heading[0].length)} />
          </details>
        ) : (
          <Document key={i} markdown={block} />
        );
      })}
    </div>
  );
}

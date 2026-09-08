import { useState } from "react";
import type { Citation, ContentRef } from "@contract";
import { usePages } from "@/core/paged-query";
import { tickerPath, id } from "@/core/page-query";
import { Button } from "./ui/button";
import { Module } from "./state";
export function Citations({
  ticker,
  content,
  aliases,
}: {
  ticker: string;
  content?: ContentRef | null;
  aliases: string[];
}) {
  const [open, setOpen] = useState(false);
  const query = usePages<Citation, "Citations">(
    "Citations",
    open && content
      ? tickerPath(ticker) +
          `/contents/${id(content.content_id)}/citations?limit=20`
      : null,
    (r) => r.data,
  );
  if (!aliases.length) return null;
  return (
    <div className="citations">
      <Button variant="link" size="sm" onClick={() => setOpen(!open)}>
        引用 {aliases.length}
      </Button>
      {open &&
        (content ? (
          <Module query={query} label="引用">
            {(page) => (
              <div>
                {aliases.map((alias) => {
                  const ref = page.items.find(
                    (c) => c.alias === alias || c.citation_key === alias,
                  );
                  return (
                    <div key={alias}>
                      {ref?.status === "RESOLVED" &&
                      /^https?:\/\//.test(ref.url ?? "") ? (
                        <a href={ref.url!} target="_blank" rel="noreferrer">
                          {ref.title ?? alias}
                        </a>
                      ) : (
                        <span>
                          {ref?.title ?? alias} ·{" "}
                          {ref?.warning ?? "引用尚未解析"}
                        </span>
                      )}
                    </div>
                  );
                })}
                {query.hasNextPage && (
                  <Button
                    variant="outline"
                    onClick={() => void query.fetchNextPage()}
                  >
                    加载更多引用
                  </Button>
                )}
              </div>
            )}
          </Module>
        ) : (
          <span>引用信息未记录</span>
        ))}
    </div>
  );
}

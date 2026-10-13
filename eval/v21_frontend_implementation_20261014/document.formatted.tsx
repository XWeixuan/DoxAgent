import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { remarkCitationMarkers } from "@/core/citation-markers";
import { useCitationNumbers } from "./citation-text";
/** Raw HTML is intentionally not enabled. Domain pages will compose their own structured readers. */
export function Document({ markdown }: { markdown: string }) {
  const numbers = useCitationNumbers(markdown);
  return (
    <article className="document-reader">
      <Markdown
        remarkPlugins={[remarkGfm, remarkCitationMarkers(numbers)]}
        components={{
          table: ({ children }) => (
            <div className="report-table">
              <table>{children}</table>
            </div>
          ),
          a: ({ href, children }) =>
            /^(https?:\/\/|#)/.test(href ?? "") ? (
              <a
                href={href}
                target={href?.startsWith("#") ? undefined : "_blank"}
                rel="noreferrer noopener"
              >
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
          img: ({ alt }) => <span>{alt || "图片"}</span>,
        }}
      >
        {markdown}
      </Markdown>
    </article>
  );
}

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  CitationNumbers,
  citationAliases,
  citationParts,
  referenceAlias,
} from "../src/core/citation-markers";
import {
  CitationProvider,
  CitationText,
  ReferenceMarks,
} from "../src/components/citation-text";
import { Document } from "../src/components/document";

describe("display-only citations", () => {
  it("keeps canonical numbers and reserves later sections before custom aliases", () => {
    const numbers = new CitationNumbers().reserve({
      ref: ["D1-O1"],
      later: "【cite:O1】【cite:O229】",
      policy_id: "P2",
    });
    expect(numbers.number("O229")).toBe(229);
    expect(numbers.number("D1-O1")).toBe(2);
    expect(citationAliases({ condition_id: "C1", ref: ["O2"] })).toEqual([
      "O2",
    ]);
    expect(referenceAlias("context/d1/c1.md")).toBeNull();
    expect(referenceAlias("https://example.com/O1")).toBeNull();
    expect(citationParts("A【cite:O2】 B【cite:O3】")).toEqual([
      { text: "A" },
      { alias: "O2" },
      { text: " B" },
      { alias: "O3" },
    ]);
  });
  it("shares a document namespace, has no interaction, and preserves raw refs", () => {
    const html = renderToStaticMarkup(
      <CitationProvider source={{ ref: ["O1", "D1-O1"] }}>
        <CitationText text="Value【cite:D1-O1】" />
        <ReferenceMarks refs={["O1", "O1", "https://example.com/report"]} />
      </CitationProvider>,
    );
    expect(html).toContain('aria-label="引用 D1-O1"');
    expect(html).toContain(">2</span>");
    expect(html.match(/aria-label="引用 O1"/g)).toHaveLength(1);
    expect(html).not.toContain("tabindex");
    expect(html).not.toContain("<button");
    expect(html).toContain("https://example.com/report");
    expect(html).not.toContain("【cite:");
  });
  it("transforms Markdown text in bold and tables, leaving code and URLs intact", () => {
    const html = renderToStaticMarkup(
      <Document
        markdown={
          "**Proof【cite:O12】**\n\n| Value |\n| --- |\n| 2【cite:O229】 |\n\n`【cite:O3】`\n\n```txt\n【cite:O4】\n```\n\n[Source](https://example.com/cite:O5)"
        }
      />,
    );
    expect(html).toContain('aria-label="引用 O12"');
    expect(html).toContain('aria-label="引用 O229"');
    expect(html).toContain("<code>【cite:O3】</code>");
    expect(html).toContain("【cite:O4】");
    expect(html).toContain('href="https://example.com/cite:O5"');
    const unsafe = renderToStaticMarkup(
      <Document markdown={"【cite:<img src=x onerror=alert(1)>】"} />,
    );
    expect(unsafe).not.toContain("<img");
  });
});

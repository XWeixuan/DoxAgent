/** Render-only citation identities. Never rewrite business content or downloads. */
export type CitationPart = { text: string } | { alias: string };
export function citationParts(text: string): CitationPart[] {
  const parts: CitationPart[] = [];
  let offset = 0;
  for (const match of text.matchAll(/【cite:([^】\r\n]+)】/g)) {
    if (match.index! > offset)
      parts.push({ text: text.slice(offset, match.index) });
    parts.push({ alias: match[1].trim() });
    offset = match.index! + match[0].length;
  }
  if (offset < text.length) parts.push({ text: text.slice(offset) });
  return parts;
}
export function referenceAlias(ref: string): string | null {
  const marked = /^【cite:([^】\r\n]+)】$/.exec(ref.trim());
  if (marked) return marked[1].trim();
  return /^[A-Z][A-Z0-9_-]*\d+$/.test(ref.trim()) ? ref.trim() : null;
}
export function citationAliases(source: unknown): string[] {
  const aliases = new Set<string>();
  const visit = (value: unknown, reference = false) => {
    if (typeof value === "string") {
      const alias = reference ? referenceAlias(value) : null;
      if (alias) aliases.add(alias);
      for (const part of citationParts(value))
        if ("alias" in part) aliases.add(part.alias);
    } else if (Array.isArray(value))
      value.forEach((item) => visit(item, reference));
    else if (value && typeof value === "object")
      Object.entries(value).forEach(([key, item]) =>
        visit(item, ["ref", "citation", "citations", "aliases"].includes(key)),
      );
  };
  visit(source, Array.isArray(source));
  return [...aliases];
}
export class CitationNumbers {
  private numbers = new Map<string, number>();
  reserve(source: unknown) {
    const aliases = citationAliases(source);
    // Reserve canonical numbers first, including aliases in later sections.
    for (const alias of aliases) {
      const canonical = /^O(\d+)$/.exec(alias);
      if (canonical) {
        const n = Number(canonical[1]);
        if (Number.isSafeInteger(n)) {
          for (const [key, value] of this.numbers)
            if (value === n && !/^O\d+$/.test(key)) this.numbers.delete(key);
          this.numbers.set(alias, n);
        }
      }
    }
    for (const alias of aliases) this.number(alias);
    return this;
  }
  number(alias: string) {
    const canonical = /^O(\d+)$/.exec(alias);
    if (canonical && Number.isSafeInteger(Number(canonical[1])))
      return Number(canonical[1]);
    if (!this.numbers.has(alias)) {
      const reserved = new Set(this.numbers.values());
      let n = 1;
      while (reserved.has(n)) n++;
      this.numbers.set(alias, n);
    }
    return this.numbers.get(alias)!;
  }
}

interface TextNode {
  type: string;
  value?: string;
  children?: TextNode[];
  data?: Record<string, unknown>;
}
/** Only replace text AST nodes, never URLs, code or raw HTML. */
export function remarkCitationMarkers(numbers: CitationNumbers) {
  return () => (tree: TextNode) => {
    const visit = (node: TextNode) => {
      if (["code", "inlineCode", "html"].includes(node.type) || !node.children)
        return;
      node.children = node.children.flatMap((child) => {
        if (child.type !== "text") {
          visit(child);
          return [child];
        }
        return citationParts(child.value ?? "").map((part): TextNode =>
          "text" in part
            ? { type: "text", value: part.text }
            : {
                type: "citationBadge",
                data: {
                  hName: "span",
                  hProperties: {
                    className: "citation-badge",
                    "data-digits": String(numbers.number(part.alias)).length,
                    "aria-label": `引用 ${part.alias}`,
                    style: `width:${citationDiameter(numbers.number(part.alias))}px;height:${citationDiameter(numbers.number(part.alias))}px`,
                  },
                },
                children: [
                  { type: "text", value: String(numbers.number(part.alias)) },
                ],
              },
        );
      });
    };
    visit(tree);
  };
}
export function citationDiameter(number: number) {
  return Math.max(20, String(number).length * 8 + 2);
}

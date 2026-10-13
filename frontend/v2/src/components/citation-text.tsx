import { createContext, useContext, useState, type ReactNode } from "react";
import {
  CitationNumbers,
  citationParts,
  referenceAlias,
  citationDiameter,
} from "@/core/citation-markers";

const NumberContext = createContext<CitationNumbers | null>(null);
export function CitationProvider({
  source,
  children,
}: {
  source: unknown;
  children: ReactNode;
}) {
  const [numbers] = useState(() => new CitationNumbers());
  numbers.reserve(source);
  return (
    <NumberContext.Provider value={numbers}>{children}</NumberContext.Provider>
  );
}
export function useCitationNumbers(source: unknown) {
  const parent = useContext(NumberContext);
  const [local] = useState(() => new CitationNumbers());
  return (parent ?? local).reserve(source);
}
export function CitationBadge({
  alias,
  number,
}: {
  alias: string;
  number: number;
}) {
  return (
    <span
      className="citation-badge"
      style={{
        width: citationDiameter(number),
        height: citationDiameter(number),
      }}
      data-digits={String(number).length}
      aria-label={`引用 ${alias}`}
    >
      {number}
    </span>
  );
}
export function CitationText({ text }: { text: string }) {
  const numbers = useCitationNumbers(text);
  return (
    <>
      {citationParts(text).map((part, i) =>
        "text" in part ? (
          part.text
        ) : (
          <CitationBadge
            key={i}
            alias={part.alias}
            number={numbers.number(part.alias)}
          />
        ),
      )}
    </>
  );
}
export function ReferenceMarks({ refs }: { refs: string[] }) {
  const numbers = useCitationNumbers(refs);
  if (!refs.length) return null;
  const unique = [...new Set(refs)];
  const raw = unique.filter((ref) => !referenceAlias(ref));
  return (
    <div className="reference-marks">
      {unique.map((ref) => {
        const alias = referenceAlias(ref);
        return alias ? (
          <CitationBadge
            key={ref}
            alias={alias}
            number={numbers.number(alias)}
          />
        ) : null;
      })}
      {raw.length > 0 && (
        <details className="reference-raw">
          <summary>资料引用（{raw.length}）</summary>
          <ul>
            {raw.map((ref) => (
              <li key={ref}>
                {/^https?:\/\//.test(ref) ? (
                  <a href={ref} target="_blank" rel="noreferrer noopener">
                    {ref}
                  </a>
                ) : (
                  <CitationText text={ref} />
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

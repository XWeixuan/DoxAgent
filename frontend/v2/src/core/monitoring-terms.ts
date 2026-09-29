import type { MonitoringTermsValue, MonitoringL2Term } from "@contract";

export function blankTerms(languages: string[]): MonitoringTermsValue {
  return {
    l1_concepts: [{ concept_id: crypto.randomUUID(), expressions: Object.fromEntries(languages.map((lang) => [lang, ""])) }],
    l2: Object.fromEntries(languages.map((lang) => [lang, { groups: [] }])),
    definition: { relevant: "", irrelevant: "" },
  };
}

export function newTerm(): MonitoringL2Term {
  return { literal: "", regex: null, field: "all", case_sensitive: false, whole_word: false };
}

export function sameTerms(a: MonitoringTermsValue | null, b: MonitoringTermsValue | null) {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function ruleSummary(group: MonitoringTermsValue["l2"][string]["groups"][number]) {
  const part = (items: MonitoringL2Term[]) => items.map((item) => `${item.regex === null ? "文本" : "正则"} ${item.literal ?? item.regex ?? ""}`).join("、");
  return [
    group.any.length ? `任一：${part(group.any)}` : "",
    group.all.length ? `全部：${part(group.all)}` : "",
    group.none.length ? `本组排除：${part(group.none)}` : "",
  ].filter(Boolean).join("；");
}

export function parseRulesJson(text: string): MonitoringTermsValue["l2"] {
  const parsed: unknown = JSON.parse(text);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("分发规则必须为语言对象");
  for (const [language, rules] of Object.entries(parsed)) {
    if (!language || !rules || typeof rules !== "object" || !("groups" in rules) || !Array.isArray(rules.groups))
      throw new Error(`${language || "语言"} 缺少规则组数组`);
    for (const group of rules.groups) {
      if (!group || typeof group.id !== "string" || !["any", "all", "none"].every((key) => Array.isArray(group[key])))
        throw new Error(`${language} 的规则组格式错误`);
      for (const bucket of ["any", "all", "none"] as const) for (const term of group[bucket] as unknown[]) {
        if (!term || typeof term !== "object" || Array.isArray(term))
          throw new Error(`${language} 的${bucket}条件格式错误`);
        const item = term as Record<string, unknown>;
        if (!(item.literal === null || typeof item.literal === "string") ||
            !(item.regex === null || typeof item.regex === "string") ||
            !["all", "title", "summary", "body"].includes(String(item.field)) ||
            typeof item.case_sensitive !== "boolean" || typeof item.whole_word !== "boolean")
          throw new Error(`${language} 的${bucket}条件字段不完整`);
      }
    }
  }
  return parsed as MonitoringTermsValue["l2"];
}

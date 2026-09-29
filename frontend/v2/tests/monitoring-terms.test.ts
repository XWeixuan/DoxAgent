import { describe, expect, it } from "vitest";
import { parseRulesJson, ruleSummary } from "../src/core/monitoring-terms";

describe("monitoring rule views", () => {
  it("keeps regex syntax, flags and extra languages through JSON editing", () => {
    const rule = {
      id: "zh-hbm",
      any: [{ literal: null, regex: "(?i:HBM\\d+)|美光", field: "title", case_sensitive: true, whole_word: true }],
      all: [], none: [],
    };
    const parsed = parseRulesJson(JSON.stringify({ "zh-Hant": { groups: [rule] } }));
    expect(parsed["zh-Hant"].groups[0]).toEqual(rule);
    expect(ruleSummary(parsed["zh-Hant"].groups[0])).toContain("正则 (?i:HBM\\d+)|美光");
    expect(() => parseRulesJson('{"en":{"groups":[{"id":"bad","any":["text"],"all":[],"none":[]}]}}')).toThrow();
  });
});

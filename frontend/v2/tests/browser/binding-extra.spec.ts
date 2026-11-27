import { expect, test } from "@playwright/test";

for (const width of [1262, 1559]) test(`binding filter save, icon and disable at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 });
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto("/tests/browser/binding-extra.html");
  await page.getByText("额外相关性过滤", { exact: true }).click();
  const editor = page.getByRole("textbox", { name: "额外相关性过滤 JSON" });
  await expect(editor).toBeVisible();
  await expect(page.locator('[aria-label="已配置额外相关性过滤"]')).toHaveCount(0);
  await editor.fill("{");
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(page.locator("#saved")).toBeEmpty();
  const term = (pattern: string) => ({ literal: null, regex: pattern, field: "title", case_sensitive: false, whole_word: false });
  await editor.fill(JSON.stringify({ groups: [{ id: "exclude", any: [], all: [term(".")], none: [term("gaming chair")] }] }));
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(page.locator("#saved")).toContainText('"writes":1');
  await expect(page.locator('[aria-label="已配置额外相关性过滤"]')).toHaveCount(1);
  await page.getByText("额外相关性过滤", { exact: true }).click();
  await expect(editor).toContainText("gaming chair");
  await page.screenshot({ path: `../../eval/binding_extra_regex_20261127/ui-${width}.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  await editor.fill("");
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(page.locator("#saved")).toContainText('"extra":null');
  await expect(page.locator('[aria-label="已配置额外相关性过滤"]')).toHaveCount(0);
  expect(errors).toEqual([]);
});

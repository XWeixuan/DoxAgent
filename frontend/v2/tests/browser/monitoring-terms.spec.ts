import { expect, test } from "@playwright/test";

for (const width of [1262, 1559]) {
  test(`monitoring terms editor fits ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("http://127.0.0.1:5174/tests/browser/monitoring-terms.html");
    await expect(page.getByRole("heading", { name: "MU 统一监测词" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.getByRole("textbox", { name: "概念 1 en" }).fill("Micron HBM");
    await page.getByText("memory ·").click();
    await page.getByRole("textbox", { name: "至少命中一项 条件 1" }).fill("HBM");
    await page.getByRole("button", { name: "原始 JSON" }).click();
    const json = page.getByRole("textbox", { name: "分发规则 JSON" });
    await expect(json).toContainText("HBM");
    const validRules = await json.inputValue();
    await json.fill("{ invalid");
    await expect(page.getByRole("button", { name: "应用更新" })).toBeDisabled();
    await json.fill(validRules);
    await page.getByRole("button", { name: "规则视图" }).click();
    await page.getByRole("textbox", { name: "相关条件", exact: true }).fill("HBM supply relevance");
    await page.getByRole("button", { name: "应用更新" }).click();
    await expect(page.locator("#saved-revision")).toHaveText("2");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}

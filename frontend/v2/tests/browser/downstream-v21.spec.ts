import { test, expect } from "@playwright/test";
for (const width of [1262, 1559]) {
  test(`D2 native Unit reading and index ${width}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("http://127.0.0.1:5174/tests/browser/downstream-v21.html");
    await expect(
      page.getByRole("heading", { name: "客户订单", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "预期基线", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "共识基线", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "共识基线", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "当前状态", exact: true }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "客户结构", exact: true }).click();
    await expect(page.getByText("市场扩展", { exact: true })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/d2-v21-${width}.png`,
      fullPage: true,
    });
    expect(errors).toEqual([]);
  });
  test(`Policy native directions and raw refs ${width}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(
      "http://127.0.0.1:5174/tests/browser/downstream-v21.html?mode=policy",
    );
    await expect(page.getByText("LONG", { exact: true })).toBeVisible();
    await expect(page.getByText("SHORT", { exact: true })).toBeVisible();
    await expect(
      page.getByText("订单影响收入与盈利", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("context/document2.json#客户订单", { exact: true }),
    ).toBeVisible();
    await expect(page.locator(".source-links a")).toHaveCount(0);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/policy-v3-${width}.png`,
      fullPage: true,
    });
    expect(errors).toEqual([]);
  });
}

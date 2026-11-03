import { expect, test } from "@playwright/test";

for (const width of [1262, 1559]) {
  test(`failed Case KPI uses overview period at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/tests/browser/overview-monitor.html");
    const card = page
      .locator(".metric-card")
      .filter({ hasText: "下游Case判定失败数" });
    await expect(card).toHaveCount(1);
    await expect(card.locator(".metric-value")).toHaveText("6个");
    await expect(page.getByText("非例行维护触发数")).toHaveCount(0);
    await page.getByRole("radio", { name: "7 天", exact: true }).click();
    await expect(card.locator(".metric-value")).toHaveText("15个");
    const title = await card.locator("h3").boundingBox();
    const value = await card.locator(".metric-value").boundingBox();
    expect(title!.y + title!.height).toBeLessThanOrEqual(value!.y);
    const neighbor = await page
      .locator(".metric-card")
      .nth(3)
      .locator(".metric-value")
      .boundingBox();
    expect(Math.abs(value!.y - neighbor!.y)).toBeLessThanOrEqual(1);
    expect(value!.height).toBeGreaterThan(25);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/overview-failed-cases-${width}.png`,
      fullPage: false,
    });
    await card.screenshot({
      path: `test-results/overview-failed-card-${width}.png`,
    });
  });
}

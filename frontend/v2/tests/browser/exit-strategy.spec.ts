import { expect, test } from "@playwright/test";

for (const width of [1262, 1559]) {
  test(`exit state distinguishes request from flat at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("http://127.0.0.1:5189/tests/browser/exit-strategy.html");
    await expect(page.getByText("跟踪中", { exact: true })).toBeVisible();
    await expect(
      page.getByText("退出已触发，等待平仓", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("已平仓", { exact: true })).toHaveCount(1);
    await expect(page.getByText("✓ ✓ · ✓ ✓ ✓", { exact: true })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/exit-strategy-${width}.png`,
      fullPage: true,
    });
  });
}

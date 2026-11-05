import { expect, test } from "@playwright/test";
for (const width of [1262, 1559]) {
  test(`allocation diagnostics use the existing execution details at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/tests/browser/runtime-case.html?mode=allocation");
    await expect(page.getByText("配仓诊断", { exact: true })).toHaveCount(2);
    await expect(page.getByText(/实际 38.50%/)).toBeVisible();
    await expect(page.getByText(/主仓规划价 \$800.00/)).toBeVisible();
    await expect(
      page.getByText("预算与整数股约束下无合格组合", { exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/hedge-allocation-${width}.png`,
      fullPage: true,
    });
  });
}

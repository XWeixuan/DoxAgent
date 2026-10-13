import { expect, test } from "@playwright/test";

for (const width of [1262, 1559]) {
  test(`trade decision survives operations failure at ${width}px`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(
      "http://127.0.0.1:5174/tests/browser/runtime-case.html?mode=v4",
    );
    await expect(
      page.getByText("EXPERT_TRADE", { exact: false }),
    ).toBeVisible();
    await expect(page.getByText("FAILED · 后置操作重试耗尽")).toBeVisible();
    await expect(page.getByText("后置判定未完成")).toBeVisible();
    await expect(page.getByText("Policy 未命中", { exact: true })).toHaveCount(
      0,
    );
    await expect(page.getByText("W3 · 交易决策")).toBeVisible();
    await expect(page.getByText("W3 · 后置操作")).toBeVisible();
    await page.getByText("量产基线 · 观察后续").click();
    await expect(page.getByText("完整业务正文")).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    expect(errors).toEqual([]);
    await page.screenshot({
      path: `test-results/w3-v4-${width}.png`,
      fullPage: true,
    });
  });
}

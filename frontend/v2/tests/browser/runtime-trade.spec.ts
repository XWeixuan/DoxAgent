import { expect, test } from "@playwright/test";

for (const width of [1262, 1559]) {
  test(`trade flow and export selection fit ${width}px desktop`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("http://127.0.0.1:5174/tests/browser/runtime-trade.html");
    await expect(page.getByText("运行链路")).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "研判结果" }),
    ).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "交易执行" }),
    ).toBeVisible();
    await expect(page.getByText("交易意图产生").first()).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.getByText("交易意图产生").first().click();
    await expect(page.getByText("节点详情：TRADE_INTENT")).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    const graph = page.locator(".runtime-graph-scroll");
    if (width === 1262)
      expect(
        await graph.evaluate((element) => element.scrollWidth),
      ).toBeGreaterThan(await graph.evaluate((element) => element.clientWidth));

    await page
      .getByRole("checkbox", { name: "选择Micron supply update 0" })
      .check();
    await expect(page.locator("#selected")).toHaveText("已选择 1");
    await expect(page.locator("#opened")).toHaveText("已打开 ");
    await page.getByRole("checkbox", { name: "全选已加载记录" }).check();
    await expect(page.locator("#selected")).toHaveText("已选择 3");
    await page
      .getByRole("checkbox", { name: "选择Micron supply update 1" })
      .uncheck();
    await expect(
      page.getByRole("checkbox", { name: "全选已加载记录" }),
    ).not.toBeChecked();
    await page.getByText("Micron supply update 1").click();
    await expect(page.locator("#opened")).toHaveText("已打开 case-1");
  });
}

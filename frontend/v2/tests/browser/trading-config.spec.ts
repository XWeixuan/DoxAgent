import { test, expect } from "@playwright/test";
for (const width of [1262, 1559]) {
  test(`trading settings apply receipt and layout ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/tests/browser/trading-config.html");
    await expect(page.getByText("IB Gateway–Live")).toBeVisible();
    await expect(page.getByText("IB Gateway–Paper")).toBeVisible();
    await expect(page.getByText("Ticker 状态", { exact: true })).toHaveCount(0);
    const before = page.url();
    await page
      .getByRole("button", { name: "交易配置 MU", exact: true })
      .click();
    expect(page.url()).toBe(before);
    const dialog = page.getByRole("dialog", { name: "MU 交易配置" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("交易账户", { exact: true })).toHaveValue(
      "PAPER",
    );
    await expect(dialog.getByLabel("开启对冲")).not.toBeChecked();
    await expect(
      dialog.getByLabel("滑动止盈", { exact: true }),
    ).not.toBeChecked();
    // Native mouse clicks must not bubble through the portal into the row route.
    const account = dialog.getByLabel("交易账户", { exact: true });
    await account.click();
    expect(page.url()).toBe(before);
    await account.press("End");
    await account.press("Enter");
    await expect(account).toHaveValue("LIVE");
    await dialog.getByLabel("SHORT 替代", { exact: true }).check();
    expect(page.url()).toBe(before);
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("反向单股 ETF", { exact: true }).click();
    await dialog.getByLabel("反向单股 ETF", { exact: true }).fill("MUD");
    await dialog.getByText("开启对冲", { exact: true }).click();
    expect(page.url()).toBe(before);
    await expect(dialog.getByLabel("开启对冲")).toBeChecked();
    await dialog.getByLabel("滑动止盈", { exact: true }).check();
    expect(page.url()).toBe(before);
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("正向指数 ETF", { exact: true }).fill("SOXL");
    await dialog.getByLabel("反向指数 ETF", { exact: true }).fill("SOXS");
    await dialog.getByRole("button", { name: "添加禁交易日期" }).click();
    await dialog.getByLabel("禁交易日期 1", { exact: true }).fill("2026-11-04");
    expect(page.url()).toBe(before);
    await dialog.getByRole("button", { name: "保存配置", exact: true }).click();
    await expect(dialog.getByRole("status")).toContainText(
      "已保存，等待执行端应用",
    );
    await expect(dialog.getByRole("status")).toContainText("执行端已应用", {
      timeout: 7000,
    });
    expect(page.url()).toBe(before);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    const box = await dialog.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    await dialog.getByRole("button", { name: "关闭交易配置" }).click();
    await page.getByRole("button", { name: "策略资金", exact: true }).click();
    const funds = page.getByRole("dialog", { name: "账户策略资金" });
    await funds.getByLabel("分配本金（USD）").fill("4500");
    await funds.getByRole("button", { name: "保存额度" }).click();
    await expect(funds.getByRole("status")).toContainText("等待执行端应用");
    await expect(funds.getByRole("status")).toContainText("执行端已应用", {
      timeout: 7000,
    });
    await funds.getByRole("button", { name: "关闭策略资金" }).click();
    await page.getByRole("button", { name: "交易配置 MU", exact: true }).click();
    await page.locator(".detail-overlay").click({ position: { x: 5, y: 5 } });
    await expect(dialog).not.toBeVisible();
    expect(page.url()).toBe(before);
    // The normal non-control row click must still navigate.
    await page.locator("tr.monitor-row").filter({
      has: page.getByRole("link", { name: "MU", exact: true }),
    }).locator("td").nth(1).click();
    await expect(page).toHaveURL(/\/ticker\/MU\/research$/);
    await expect(page.getByRole("heading", { name: "基础投研测试页" })).toBeVisible();
  });
}

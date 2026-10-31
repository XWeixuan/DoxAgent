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
    await expect(
      page.getByRole("button", { name: "策略资金", exact: true }),
    ).toHaveCount(0);
    const title = await page
      .getByRole("heading", { name: "标的监控", exact: true })
      .boundingBox();
    const panel = await page.locator(".monitor-panel").boundingBox();
    expect(title!.x - panel!.x).toBeLessThan(40);
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
    await expect(dialog.getByLabel(/^指数 ETF/)).toHaveCount(0);
    await dialog.getByLabel("滑动止盈", { exact: true }).check();
    expect(page.url()).toBe(before);
    await expect(dialog).toBeVisible();
    await dialog.getByLabel(/^正向（LONG）指数 ETF/).fill("SOXL");
    await dialog.getByLabel(/^反向（SHORT）指数 ETF/).fill("SOXS");
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
    await page
      .getByRole("button", { name: "交易配置 MU", exact: true })
      .click();
    await page.locator(".detail-overlay").click({ position: { x: 5, y: 5 } });
    await expect(dialog).not.toBeVisible();
    expect(page.url()).toBe(before);
    // The normal non-control row click must still navigate.
    await page
      .locator("tr.monitor-row")
      .filter({
        has: page.getByRole("link", { name: "MU", exact: true }),
      })
      .locator("td")
      .nth(1)
      .click();
    await expect(page).toHaveURL(/\/ticker\/MU\/research$/);
    await expect(
      page.getByRole("heading", { name: "基础投研测试页" }),
    ).toBeVisible();
  });

  test(`account type controls hedge fields independently of environment ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/tests/browser/trading-config.html?account=paper-cash");
    const before = page.url();
    await page
      .getByRole("button", { name: "交易配置 MU", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "MU 交易配置" });
    await dialog.getByLabel("开启对冲").check();
    await expect(dialog.getByText("CASH", { exact: true })).toBeVisible();
    await expect(dialog.getByLabel(/^正向（LONG）指数 ETF/)).toBeVisible();
    await expect(dialog.getByLabel(/^反向（SHORT）指数 ETF/)).toBeVisible();
    await expect(dialog.getByLabel(/^指数 ETF/)).toHaveCount(0);
    await page.screenshot({
      path: `D:/hedge-form-tests/cash-${width}.png`,
      fullPage: true,
    });
    await dialog.getByLabel("交易账户", { exact: true }).selectOption("LIVE");
    await expect(dialog.getByText("MARGIN", { exact: true })).toBeVisible();
    await expect(
      dialog.getByLabel("指数 ETF · LONG 信号时卖空，SHORT 信号时买入", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(dialog.getByLabel(/^正向（LONG）指数 ETF/)).toHaveCount(0);
    await expect(dialog.getByLabel(/^反向（SHORT）指数 ETF/)).toHaveCount(0);
    expect(page.url()).toBe(before);
  });

  test(`unconfigured account never falls through to MARGIN hedge form ${width}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/tests/browser/trading-config.html?account=missing");
    await page
      .getByRole("button", { name: "交易配置 MU", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "MU 交易配置" });
    await dialog.getByLabel("交易账户", { exact: true }).selectOption("LIVE");
    await dialog.getByLabel("开启对冲").check();
    await expect(dialog.getByText("账户未配置", { exact: true })).toBeVisible();
    await expect(
      dialog.getByText("账户接入后，按 CASH / MARGIN 类型配置对冲 ETF。"),
    ).toBeVisible();
    await expect(dialog.getByLabel(/^指数 ETF/)).toHaveCount(0);
    await expect(dialog.getByLabel(/^正向（LONG）指数 ETF/)).toHaveCount(0);
    await expect(dialog.getByLabel(/^反向（SHORT）指数 ETF/)).toHaveCount(0);
    await dialog.getByRole("button", { name: "保存配置", exact: true }).click();
    await expect(dialog.getByRole("status")).toContainText(
      "ACCOUNT_NOT_CONFIGURED",
      { timeout: 7000 },
    );
    await expect(dialog.getByRole("status")).not.toContainText("执行端已应用");
  });
}

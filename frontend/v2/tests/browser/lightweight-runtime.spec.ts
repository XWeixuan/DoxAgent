import { test, expect } from "@playwright/test";

for (const width of [1262, 1559]) {
  test(`lightweight controls and Case at ${width}px`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/tests/browser/overview-monitor.html?lightweight=1");
    await expect(
      page.getByRole("radiogroup", { name: "运行模式", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("radiogroup", { name: "交易模式", exact: true }),
    ).toBeVisible();
    for (const name of ["交易模式", "运行模式"]) {
      const columns = await page.getByRole("radiogroup", { name, exact: true })
        .evaluate(e => getComputedStyle(e).gridTemplateColumns.split(" ").length);
      expect(columns).toBe(2);
    }
    await expect(
      page.getByRole("radio", { name: "普通运行", exact: true }),
    ).toBeChecked();
    const lightRow = page
      .getByRole("row")
      .filter({ has: page.getByRole("link", { name: "MU", exact: true }) });
    await expect(lightRow.locator(".status-stack")).toContainText("轻量运行");
    await expect(lightRow.locator(".status-stack .status-badge")).toHaveCount(1);
    await expect(lightRow.locator(".mode-pending")).toHaveCount(0);
    const fullRow = page
      .getByRole("row")
      .filter({ has: page.getByRole("link", { name: "NVDA", exact: true }) });
    await expect(fullRow.locator(".status-stack")).toContainText("普通运行");
    await expect(fullRow.locator(".status-stack .status-badge")).toHaveCount(1);
    await expect(page.locator(".status-stack").getByText("正常", { exact: true })).toHaveCount(0);
    await expect(page.locator(".status-stack").getByText("阻塞", { exact: true })).toBeVisible();
    await page.getByRole("radio", { name: "轻量运行", exact: true }).click();
    await expect(
      page.getByRole("radio", { name: "轻量运行", exact: true }),
    ).toBeChecked();
    await page
      .getByRole("radio", { name: "复用当前文档", exact: true })
      .click();
    await expect(
      page.getByRole("radio", { name: "轻量运行", exact: true }),
    ).toBeDisabled();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/lightweight-overview-${width}.png`,
      fullPage: true,
    });
    await page.goto("/tests/browser/runtime-case.html?mode=lightweight");
    await expect(
      page.getByRole("heading", { name: "W1 · 新旧判定", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "W2 · Policy 判定", exact: true }),
    ).toHaveCount(0);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    expect(errors).toEqual([]);
    await page.screenshot({
      path: `test-results/lightweight-case-${width}.png`,
      fullPage: true,
    });
  });
  test(`lightweight graph hides policy paths at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/tests/browser/runtime-trade.html?lightweight=1");
    for (const name of ["W2 · Policy 匹配", "一轮判定交易", "policy相关事件"])
      await expect(page.getByRole("button", { name, exact: true })).toHaveCount(
        0,
      );
    await expect(
      page.getByRole("button", { name: "W1 · 新旧判定", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "W3 · 二轮研判", exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/lightweight-graph-${width}.png`,
      fullPage: true,
    });
  });
  test(`lightweight disables D2 and D3 pages at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    for (const target of ["expectations", "strategy"]) {
      await page.goto(
        `/tests/browser/lightweight-navigation.html?page=${target}`,
      );
      await expect(
        page.getByRole("heading", { name: "MU 基础投研", exact: true }),
      ).toBeVisible();
      for (const name of ["预期研究", "交易策略"]) {
        const tab = page.getByRole("link", { name, exact: true });
        await expect(tab).toHaveAttribute("aria-disabled", "true");
        await expect(tab).not.toHaveAttribute("href");
      }
      const calls = await page.evaluate(
        () => (window as unknown as { fixtureCalls: string[] }).fixtureCalls,
      );
      expect(calls).not.toContain("Expectations");
      expect(calls).not.toContain("PolicyContext");
      expect(calls.filter((x) => x === "ReadContext")).toHaveLength(2);
    }
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/lightweight-navigation-${width}.png`,
      fullPage: true,
    });
  });
}

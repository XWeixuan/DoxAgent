import { expect, test } from "@playwright/test";

for (const width of [1262, 1559]) {
  test(`global source directory filters and manual refresh at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.clock.install();
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("/tests/browser/source-catalog.html");
    await expect(
      page.getByRole("heading", { name: "全部消息源", exact: true }),
    ).toBeVisible();
    await expect(page.locator(".catalog-card")).toHaveCount(9);
    await expect(page.locator(".app-header")).toHaveCount(1);
    expect(
      await page
        .locator(".catalog-grid")
        .first()
        .evaluate(
          (el) => getComputedStyle(el).gridTemplateColumns.split(" ").length,
        ),
    ).toBe(3);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await expect(
      page.locator(".catalog-card").first().locator("dt"),
    ).toHaveCount(7);
    await expect(page.locator('[data-source-id="newswire_rss"]')).toContainText(
      "0.0%",
    );
    await expect(
      page.locator('[data-source-id="barrons_ticker_news"] .catalog-health'),
    ).toContainText("成功");
    await expect(
      page.locator('[data-source-id="reuters_site_search"] .catalog-health'),
    ).toContainText("失败");
    await expect(
      page.locator('[data-source-id="yahoo_finance_news"] .catalog-health'),
    ).toContainText("部分成功");
    await expect(
      page.locator('[data-source-id="yahoo_finance_news"] .catalog-health'),
    ).toContainText("目标 20–30s");
    await expect(
      page.locator(
        '[data-source-id="barrons_ticker_news"] [data-slot="card-title"]',
      ),
    ).toHaveAttribute("title", /Other Dow Jones/);
    await expect(page.locator("#section-UNUSED")).toBeVisible();
    await expect(page.locator("#section-TEST")).toBeVisible();
    expect(
      await page.locator(".catalog-section > h2").allTextContents(),
    ).toEqual([
      "全局通用消息源4",
      "AI、半导体与计算基础设施1",
      "软件、云、网络安全与前沿计算1",
      "已停用或未使用2",
      "测试消息源1",
    ]);
    const normal = page.locator('[data-source-id="barrons_ticker_news"]');
    await expect(normal.locator(".catalog-errors")).toHaveCount(0);
    expect(
      await normal
        .locator(".catalog-attributes")
        .evaluate(
          (el) => getComputedStyle(el).gridTemplateColumns.split(" ").length,
        ),
    ).toBe(2);
    const abnormal = page.locator('[data-source-id="reuters_site_search"]');
    await abnormal.locator(".catalog-errors summary").click();
    await expect(abnormal.locator(".catalog-errors code")).toHaveText(
      "HTTP_403",
    );
    await abnormal.locator(".catalog-errors summary").click();
    await page.screenshot({ path: `test-results/source-catalog-${width}.png` });
    await page.getByRole("combobox", { name: "抓取阶段" }).click();
    await page.getByRole("option", { name: "列表抓取" }).click();
    await page.getByRole("button", { name: "抓取方式", exact: true }).click();
    await page
      .getByRole("checkbox", { name: "Chrome/CDP", exact: true })
      .click();
    await page.keyboard.press("Escape");
    await expect(
      page.locator('[data-source-id="barrons_ticker_news"]'),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-source-id="reuters_site_search"]'),
    ).toBeVisible();
    await page.getByRole("checkbox", { name: "含备用方式" }).click();
    await expect(
      page.locator('[data-source-id="barrons_ticker_news"]'),
    ).toBeVisible();
    await page.getByRole("button", { name: "清除筛选", exact: true }).click();
    await page.getByRole("button", { name: "适用行业", exact: true }).click();
    await page
      .getByRole("checkbox", { name: "软件、云、网络安全与前沿计算" })
      .click();
    await page.keyboard.press("Escape");
    await expect(
      page.locator('[data-source-id="data_center_frontier"]'),
    ).toHaveCount(1);
    await expect(page.locator("#section-L1-02")).toBeVisible();
    await page.getByRole("button", { name: "清除筛选", exact: true }).click();
    const entries = page.locator(
      '[data-source-id="barrons_ticker_news"] .catalog-entries details',
    );
    await entries.locator("summary").click();
    await expect(entries).toHaveAttribute("open", "");
    await page.clock.fastForward(61_000);
    await page.evaluate(() => {
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("online"));
    });
    expect(
      await page.evaluate(() => (window as any).catalogControl.calls.length),
    ).toBe(1);
    await page.evaluate(() => {
      (window as any).catalogControl.fail = true;
    });
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(page.getByText(/catalog-fixture-error/)).toBeVisible();
    await expect(page.locator(".catalog-card")).toHaveCount(9);
    await expect(entries).toHaveAttribute("open", "");
    await page.getByRole("link", { name: "收益 / 成本审计" }).click();
    await expect(
      page.getByRole("heading", { name: "收益 / 成本审计" }),
    ).toBeVisible();
    await page.evaluate(() => {
      (window as any).catalogControl.fail = false;
    });
    await page.getByRole("link", { name: "全部消息源", exact: true }).click();
    await expect(page.locator(".catalog-card")).toHaveCount(9);
    expect(
      await page.evaluate(() => (window as any).catalogControl.calls.length),
    ).toBe(3);
    expect(errors).toEqual([]);
  });
}

test("first failure is not presented as an empty global directory", async ({
  page,
}) => {
  await page.goto("/tests/browser/source-catalog.html?fail=1");
  await expect(page.getByText(/catalog-fixture-error/)).toBeVisible();
  await expect(page.locator(".catalog-count")).toHaveCount(0);
  await expect(page.getByText("没有符合条件的消息源")).toHaveCount(0);
});

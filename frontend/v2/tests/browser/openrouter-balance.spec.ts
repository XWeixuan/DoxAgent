import { expect, test } from "@playwright/test";

for (const width of [1262, 1559]) {
  test(`OpenRouter balance below both gateways at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/tests/browser/overview-monitor.html");
    const rows = page.locator(".ticker-gateway-content .gateway-status");
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText("IB Gateway–Live");
    await expect(rows.nth(1)).toContainText("IB Gateway–Paper");
    await expect(rows.nth(2)).toHaveText("OpenRouter 余额$13.95");
    const box = await rows.nth(2).boundingBox();
    const above = await rows.nth(1).boundingBox();
    expect(box!.y).toBeGreaterThanOrEqual(above!.y + above!.height);
    const label = await rows.nth(2).locator("span").boundingBox();
    const amount = await rows.nth(2).locator("strong").boundingBox();
    expect(label!.x + label!.width).toBeLessThanOrEqual(amount!.x);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/openrouter-balance-${width}.png`,
      fullPage: false,
    });
  });
}

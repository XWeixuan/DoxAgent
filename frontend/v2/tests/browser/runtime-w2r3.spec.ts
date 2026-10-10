import { expect, test } from "@playwright/test";

for (const width of [1262, 1559]) {
  test(`R3 detail and W1 extraction fit ${width}px`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(
      "http://127.0.0.1:5174/tests/browser/runtime-case.html?mode=extraction",
    );
    await expect(page.getByText("W2 · R3 新事实对齐")).toBeVisible();
    await expect(page.getByText("P8/C2 · TRUE")).toBeVisible();
    await expect(page.getByText("Policy 依据新旧：NEW")).toBeVisible();
    await page.getByText("W1 · R3 事实抽取 · 1 条").click();
    await expect(
      page.getByText("Synthetic qualification newly completed."),
    ).toBeVisible();
    await expect(page.getByText("W1_R3", { exact: true })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/w2r3-detail-${width}.png`,
      fullPage: true,
    });
    expect(errors).toEqual([]);
  });
}

for (const mode of ["empty", "skipped"]) {
  test(`${mode} does not present missing R3 as OLD`, async ({ page }) => {
    await page.goto(
      `http://127.0.0.1:5174/tests/browser/runtime-case.html?mode=${mode}`,
    );
    await expect(page.getByText("W2 · R3 新事实对齐")).toBeVisible();
    await expect(page.getByText("Policy 依据新旧：OLD")).toHaveCount(0);
    await expect(page.getByText("未执行：无 Policy 命中或相关")).toBeVisible();
  });
}

for (const width of [1262, 1559]) {
  test(`WATCH receipt fits ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(
      "http://127.0.0.1:5174/tests/browser/runtime-case.html?mode=watch",
    );
    await expect(
      page.getByRole("heading", { name: "Policy WATCH" }),
    ).toBeVisible();
    await expect(page.getByText(/P8 · 等待客户确认/)).toBeVisible();
    await expect(page.getByText(/P8 · CANCEL · 证据已完整/)).toBeVisible();
    await page.getByText("动作接纳依据").click();
    await expect(page.getByText(/APPLIED/)).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/watch-detail-${width}.png`,
      fullPage: true,
    });
  });
}

for (const width of [1262, 1559]) {
  test(`explicit Policy selection fits ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(
      "http://127.0.0.1:5174/tests/browser/runtime-case.html?mode=selection",
    );
    await expect(page.getByText("Policy 执行判定")).toBeVisible();
    await expect(
      page.getByText("交易 · P8/C2 · 采用明确命中边界"),
    ).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/policy-selection-${width}.png`,
      fullPage: true,
    });
  });
}

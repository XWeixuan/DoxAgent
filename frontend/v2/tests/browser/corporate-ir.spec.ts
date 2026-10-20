import { expect, test } from "@playwright/test";

for (const width of [1262, 1559]) {
  test(`IR endpoint states remain separate at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("http://127.0.0.1:5174/tests/browser/corporate-ir.html");
    await expect(
      page.getByRole("heading", { name: "企业 IR / Press Releases" }),
    ).toBeVisible();
    const table = page.getByRole("table");
    await expect(table.getByRole("row")).toHaveCount(3);
    await expect(table.getByRole("row").nth(1)).toContainText("失败");
    await expect(table.getByRole("row").nth(2)).toContainText("正常");
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    await page.getByText("内部入口（2）").click();
    await expect(table).not.toBeVisible();
    await page.getByText("内部入口（2）").click();
    await expect(table).toBeVisible();
    await expect(table.getByRole("link").first()).toHaveAttribute(
      "target",
      "_blank",
    );
  });
}

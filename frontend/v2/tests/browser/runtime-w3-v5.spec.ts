import { expect, test } from "@playwright/test";

for (const width of [1262, 1559]) {
  for (const failed of [false, true]) {
    test(`unified direction and inherited HIT: ${width}px failed=${failed}`, async ({
      page,
    }) => {
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(
        `http://127.0.0.1:5174/tests/browser/runtime-case.html?mode=${failed ? "v5-failed" : "v5"}`,
      );
      await expect(
        page.getByText("NO_TRADE · 原W2命中仍保留，当前无交易机会"),
      ).toBeVisible();
      await expect(page.getByText("展开W2 Policy 判断依据")).toBeVisible();
      await expect(page.getByText("接纳至决策 / 结束（秒）")).toBeVisible();
      await expect(page.getByText("1726.85", { exact: true })).toBeVisible();
      await expect(page.getByText("调用并集 / 未覆盖（秒）")).toBeVisible();
      await expect(page.getByText("专家交易判定", { exact: true })).toHaveCount(
        0,
      );
      if (failed) {
        await expect(page.getByText("FAILED · 后置操作重试耗尽")).toBeVisible();
        await expect(page.getByText("后置判定未完成")).toBeVisible();
      } else {
        await expect(page.getByText("后置判定未完成")).toHaveCount(0);
        await expect(page.getByText("OLD · Policy 命中")).toBeVisible();
      }
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth),
      ).toBeLessThanOrEqual(width);
      expect(errors).toEqual([]);
      await page.screenshot({
        path: `test-results/w3-v5-${width}-${failed}.png`,
        fullPage: true,
      });
    });
  }
}

import { expect, test } from "@playwright/test";
for (const width of [1262, 1559]) {
  test(`global cost audit common filters and columns ${width}`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("http://127.0.0.1:5174/tests/browser/cost-audit.html");
    await expect(
      page.getByRole("heading", { name: "逐轮模型用量" }),
    ).toBeVisible();
    await expect(page.locator("tbody tr")).toHaveCount(3);
    await expect(page.getByText("有效请求 3 / 已观测 3")).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    const boundaries = await page
      .locator(".cost-report-table")
      .evaluate((t) =>
        Array.from(t.querySelectorAll("thead th")).map((h, i) =>
          Math.abs(
            h.getBoundingClientRect().left -
              t
                .querySelectorAll("tbody tr")[0]
                .children[i].getBoundingClientRect().left,
          ),
        ),
      );
    expect(Math.max(...boundaries)).toBeLessThanOrEqual(1);
    await page.getByRole("combobox", { name: "审计标的" }).selectOption("MU");
    await expect(page.locator("tbody tr")).toHaveCount(2);
    await expect(page.getByText("有效请求 2 / 已观测 2")).toBeVisible();
    await page.getByRole("combobox", { name: "审计节点" }).selectOption("W1R2");
    await expect(page.locator("tbody tr")).toHaveCount(1);
    await expect(page.getByText("有效请求 1 / 已观测 1")).toBeVisible();
    await expect(page.locator(".audit-metrics article").first()).toContainText(
      "0.044M",
    );
    const calls = await page.evaluate(
      () => (window as unknown as { costAuditCalls: string[] }).costAuditCalls,
    );
    expect(calls.filter((x) => x.startsWith("/read-context"))).toHaveLength(1);
    expect(
      calls.filter((x) => x.startsWith("/audit/cost/report")),
    ).toHaveLength(3);
    expect(
      calls.some((x) => /\/(summary|nodes|trend|breakdown|filters)\?/.test(x)),
    ).toBe(false);
    await page.getByText("分项", { exact: true }).click();
    await page.screenshot({
      path: `test-results/cost-audit-${width}.png`,
      fullPage: true,
    });
    expect(errors).toEqual([]);
  });
}

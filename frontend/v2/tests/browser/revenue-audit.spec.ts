import { expect, test } from "@playwright/test";
for (const width of [1262, 1559]) {
  test(`revenue audit frozen filters, shared drawer and lazy calculations ${width}`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.setViewportSize({ width, height: 1040 });
    await page.goto(
      "http://127.0.0.1:5174/tests/browser/revenue-audit.html?audit=REVENUE",
    );
    await expect(page.locator(".revenue-table tbody tr")).toHaveCount(3);
    await expect(page.locator(".revenue-holding")).toHaveCount(2);
    await expect(page.locator(".revenue-kpis article")).toHaveCount(7);
    const metricRows = await page
      .locator(".revenue-kpis article")
      .evaluateAll((cards) =>
        cards.map((card) => Math.round(card.getBoundingClientRect().top)),
      );
    expect(new Set(metricRows).size).toBe(width === 1262 ? 2 : 1);
    await expect(page.locator(".revenue-kpis article").first()).toContainText(
      "当前策略总资产",
    );
    const assets = await page
      .locator(".revenue-kpis article")
      .first()
      .locator("strong")
      .innerText();
    const calls = () =>
      page.evaluate(
        () =>
          (window as unknown as { revenueAuditCalls: string[] })
            .revenueAuditCalls,
      );
    expect((await calls()).filter((x) => x.includes("/refresh"))).toHaveLength(
      1,
    );
    expect((await calls()).some((x) => x.includes("/hypotheticals"))).toBe(
      false,
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    const boundaries = await page
      .locator(".revenue-table")
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
    await page.screenshot({
      path: `D:/revenue-ui-${width}.png`,
      fullPage: true,
    });
    await page.locator(".revenue-holding").first().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "执行过程", exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: `D:/revenue-drawer-${width}.png`,
      fullPage: true,
    });
    await expect(
      page.getByRole("link", { name: "查看 W3 Case" }),
    ).toHaveAttribute("href", /\/ticker\/MU\/runtime\?case=/);
    await page.getByRole("button", { name: "关闭详情" }).click();
    await page.getByRole("radio", { name: "全部记录", exact: true }).click();
    await expect(page.locator(".revenue-table tbody tr")).toHaveCount(6);
    await expect
      .poll(
        async () =>
          (await calls()).filter((x) => x.includes("/hypotheticals")).length,
      )
      .toBe(1);
    await expect(page.locator(".revenue-table")).toContainText("假设");
    await page.getByRole("radio", { name: "未执行意图", exact: true }).click();
    await expect(page.locator(".revenue-table tbody tr")).toHaveCount(3);
    await page.getByRole("radio", { name: "7天", exact: true }).click();
    await expect(
      page.locator(".revenue-kpis article").first().locator("strong"),
    ).toHaveText(assets);
    await page.getByRole("radio", { name: "LIVE", exact: true }).click();
    await expect(page.locator(".revenue-holding")).toHaveCount(0);
    expect((await calls()).filter((x) => x.includes("/refresh"))).toHaveLength(
      1,
    );
    await page.getByRole("radio", { name: "PAPER", exact: true }).click();
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await expect
      .poll(
        async () =>
          (await calls()).filter((x) => x.includes("/refresh")).length,
      )
      .toBe(2);
    await page.getByRole("radio", { name: "成本审计", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "逐轮模型用量", exact: true }),
    ).toBeVisible();
    await page.getByRole("radio", { name: "收益审计", exact: true }).click();
    await expect(page.locator(".revenue-table tbody tr")).toHaveCount(3);
    expect((await calls()).filter((x) => x.includes("/refresh"))).toHaveLength(
      3,
    );
    expect(errors).toEqual([]);
  });
}

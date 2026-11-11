import { expect, test } from "@playwright/test";
test("invalid HTTP envelope reports its request id without inventing empty bindings", async ({
  page,
}) => {
  await page.goto("/tests/browser/news-timeliness.html?invalid_coverage=1");
  await expect(page.getByText(/消息统计读取失败/)).toBeVisible();
  await expect(page.getByText(/请求编号：fixture-request/)).toBeVisible();
  await expect(
    page.getByText(/请求编号：undefined|已保留上次结果|没有已绑定消息源/),
  ).toHaveCount(0);
  await expect(page.locator("[data-message-id]")).toHaveCount(20);
  await page.evaluate(() => {
    (
      window as unknown as { busControl: { invalidCoverage: boolean } }
    ).busControl.invalidCoverage = false;
  });
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByText("已绑定 32")).toBeVisible();
  await expect(page.getByText(/消息统计读取失败/)).toHaveCount(0);
  await expect(page.locator("[data-message-id]")).toHaveCount(20);
});
for (const width of [1262, 1559]) {
  test(`bus reading survives minute errors and view rollover at ${width}px`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.clock.install();
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/tests/browser/news-timeliness.html");
    await expect(page.getByText("已绑定 32")).toBeVisible();
    await expect(page.getByText("84.0%")).toBeVisible();
    await expect(page.getByText("85.0%")).toBeVisible();
    await expect(page.locator(".business-metrics article")).toHaveCount(6);
    await expect(page.locator(".source-status dl dt")).toHaveCount(7);
    await expect(page.getByText(/旧口径/)).toHaveCount(0);
    await page.getByRole("button", { name: "展开更多消息" }).click();
    await expect(page.locator("[data-message-id]")).toHaveCount(40);
    const item = page.locator('[data-message-id="m25"]');
    await item.getByRole("button").click();
    await item.scrollIntoViewIfNeeded();
    await expect(item.getByRole("button")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    const position = await item.boundingBox();
    await page.evaluate(() => {
      const w = window as unknown as {
        busControl: { fail: boolean };
        oldCard: Element | null;
        oldSource: Element | null;
      };
      w.oldCard = document.querySelector('[data-message-id="m25"]');
      w.oldSource = document.querySelector(".source-status");
      w.busControl.fail = true;
    });
    await page.clock.runFor(60_100);
    await expect(page.getByText(/消息统计读取失败/)).toBeVisible();
    await expect(page.getByText("84.0%")).toBeVisible();
    await expect(page.locator("[data-message-id]")).toHaveCount(40);
    expect(
      await page.evaluate(
        () =>
          (window as unknown as { oldSource: Element }).oldSource ===
          document.querySelector(".source-status"),
      ),
    ).toBe(true);
    expect(Math.abs((await item.boundingBox())!.y - position!.y)).toBeLessThan(
      4,
    );
    await page.evaluate(() => {
      (window as unknown as { busControl: { fail: boolean } }).busControl.fail =
        false;
    });
    await page.clock.runFor(60_100);
    await expect(page.getByText(/消息统计读取失败/)).toHaveCount(0);
    await page.clock.runFor(12 * 60_000 + 200);
    const renewals = () =>
      page.evaluate(
        () =>
          (
            window as unknown as { busFixtureCalls: string[] }
          ).busFixtureCalls.filter(
            (x) => x.startsWith("Messages:") && x.includes("reading_after="),
          ).length,
      );
    await expect.poll(renewals).toBe(1);
    await expect(page.locator("[data-message-id]")).toHaveCount(40);
    await expect(item.getByRole("button")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(
      await page.evaluate(
        () =>
          (window as unknown as { oldCard: Element }).oldCard ===
          document.querySelector('[data-message-id="m25"]'),
      ),
    ).toBe(true);
    expect(Math.abs((await item.boundingBox())!.y - position!.y)).toBeLessThan(
      4,
    );
    await page.getByRole("button", { name: "展开更多消息" }).click();
    await expect(page.locator("[data-message-id]")).toHaveCount(60);
    await page.evaluate(() => {
      (
        window as unknown as { busControl: { reset: () => void } }
      ).busControl.reset();
    });
    await expect.poll(renewals).toBe(2);
    await expect(page.locator("[data-message-id]")).toHaveCount(60);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(width);
    expect(
      await page.evaluate(() =>
        (
          window as unknown as { busFixtureCalls: string[] }
        ).busFixtureCalls.some((x) =>
          /^BusMetrics:|^Sources:|^BusStatus:/.test(x),
        ),
      ),
    ).toBe(false);
    expect(errors).toEqual([]);
    for (const label of await page.locator(".source-status dt").all()) {
      const left = await label.boundingBox(),
        right = await label
          .locator("xpath=following-sibling::dd[1]")
          .boundingBox();
      expect(left!.x + left!.width).toBeLessThanOrEqual(right!.x);
    }
    await page.screenshot({
      path: `test-results/news-timeliness-${width}.png`,
      fullPage: true,
    });
  });
}

import {expect,test} from "@playwright/test";

for (const width of [1262,1559]) {
  test(`news cohort and latency metrics at ${width}px`,async ({page}) => {
    const errors: string[] = [];
    page.on("pageerror",error=>errors.push(error.message));
    await page.setViewportSize({width,height:1000});
    await page.goto("http://127.0.0.1:5174/tests/browser/news-timeliness.html");
    await expect(page.getByText("新文章正文成功率")).toBeVisible();
    await expect(page.getByText("84.0%")).toBeVisible();
    await expect(page.getByText("近30分钟实际刷新间隔")).toBeVisible();
    await expect(page.getByText("30.28s").first()).toBeVisible();
    await expect(page.getByText(/兑现 96.4%/)).toBeVisible();
    await expect(page.getByText(/候选待接纳 2/)).toBeVisible();
    await expect(page.getByText("实际间隔",{exact:true})).toBeVisible();
    await expect(page.getByText("周期兑现",{exact:true})).toBeVisible();
    for (const label of await page.locator(".source-status-body dt").all()) {
      expect((await label.boundingBox())?.height).toBeLessThan(30);
    }
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    expect(errors).toEqual([]);
    await page.screenshot({path:`test-results/news-timeliness-${width}.png`,fullPage:true});
  });
}

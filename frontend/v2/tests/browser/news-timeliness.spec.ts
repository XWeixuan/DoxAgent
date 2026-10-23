import {expect,test} from "@playwright/test";

for (const width of [1262,1559]) {
  test(`news cohort and latency metrics at ${width}px`,async ({page}) => {
    const errors: string[] = [];
    page.on("pageerror",error=>errors.push(error.message));
    await page.setViewportSize({width,height:1000});
    await page.goto("http://127.0.0.1:5174/tests/browser/news-timeliness.html");
    await expect(page.getByText("新文章正文成功率")).toBeVisible();
    await expect(page.getByText("84.0%")).toBeVisible();
    await expect(page.getByText("近30分钟轮询耗时")).toBeVisible();
    await expect(page.getByText("3.28s")).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    expect(errors).toEqual([]);
    await page.screenshot({path:`test-results/news-timeliness-${width}.png`,fullPage:true});
  });
}

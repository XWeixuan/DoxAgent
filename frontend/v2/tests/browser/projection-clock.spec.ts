import { expect, test } from "@playwright/test";
test("projection delay remains visible until caught up, history stays separate",async({page})=>{
 await page.goto("http://127.0.0.1:5174/tests/browser/projection-clock.html");
 await expect(page.getByText(/最早待同步事件已等待 59 秒/)).toBeVisible();
 await page.getByText("数据同步状态",{exact:true}).click();
 await expect(page.getByText(/17204 个历史缺口待核验/)).toBeVisible();
 await page.getByRole("button",{name:"刷新",exact:true}).click();
 await expect(page.getByText(/最早待同步事件已等待/)).toHaveCount(0);
 await expect(page.getByText(/已追平已观察源头/)).toBeVisible();
 await expect(page.getByText(/17204 个历史缺口待核验/)).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(1440);
 await page.screenshot({path:"test-results/projection-clock.png",fullPage:true});
});

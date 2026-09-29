import { expect, test } from "@playwright/test";

test("monitoring terms UI applies to native Bus database and reads it back", async ({ page }) => {
  test.skip(!process.env.DOXAGENT_TERMS_BACKEND, "Start the disposable native-Bus backend fixture first");
  await page.goto("http://127.0.0.1:5174/tests/browser/monitoring-terms.html?real=1");
  await expect(page.getByRole("heading", { name: "MU 统一监测词" })).toBeVisible();
  await page.getByRole("textbox", { name: "概念 1 en" }).fill("Micron HBM supply");
  await page.getByRole("button", { name: "检查并预览" }).click();
  await expect(page.getByText("实际搜索语句预览")).toBeVisible();
  await page.getByRole("button", { name: "应用更新" }).click();
  await expect(page.getByText("版本 2")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("textbox", { name: "概念 1 en" })).toHaveValue("Micron HBM supply");
});

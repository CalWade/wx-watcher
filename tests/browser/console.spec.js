const { test, expect } = require("@playwright/test");
test.beforeEach(async ({ page }) => {
  await page.goto("/#token=browser-fixture-token&page=setup");
  await expect(page.locator("#title")).toHaveText("连接与配置");
});
async function closeJob(page) {
  await expect(page.locator("#job-close")).toBeEnabled({ timeout: 10000 });
  await expect(page.locator("#job-progress")).toHaveText("已完成");
  await page.locator("#job-close").click();
}
test("onboarding chooses conversation and persists it", async ({ page }) => {
  await page.locator("#chat-query").fill("文件传输助手");
  await page.locator("#find-chats").click();
  await closeJob(page);
  await page.locator("#chats button").first().click();
  await closeJob(page);
  await expect(page.locator("#chat-query")).toHaveValue("文件传输助手");
  await expect(page).not.toHaveURL(/token=/);
});
test("customer text renders literally without HTML execution", async ({
  page,
}) => {
  await page.locator('nav [data-page="records"]').click();
  await expect(page.locator(".original")).toContainText("<img src=x");
  await expect(page.locator(".record img")).toHaveCount(0);
  expect(await page.evaluate(() => window.injectionExecuted)).toBeUndefined();
});
test("knowledge and call limits can be configured", async ({ page }) => {
  await page.locator('nav [data-page="knowledge"]').click();
  await page.locator("#business-context").fill("培训时间待确认。");
  await page.locator("#reply-rules").fill("不承诺未知日期。");
  await page.locator("#save-knowledge").click();
  await closeJob(page);
  await page.reload();
  await expect(page.locator("#business-context")).toHaveValue(
    "培训时间待确认。",
  );
});
test("credential page never displays secret material", async ({ page }) => {
  await page.locator('nav [data-page="keytool"]').click();
  await expect(
    page.getByText("Keychain 已保存", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("#key-file")).toHaveValue("");
  await expect(page.locator("#backup-password")).toHaveAttribute(
    "type",
    "password",
  );
});
test("automatic connection exposes choices and completes setup in one flow", async ({
  page,
}) => {
  await page.locator("#auto-connect").click();
  await closeJob(page);
  await expect(page.locator("#onboarding-checks")).toContainText(
    "合成连接检查",
  );
  await page.locator("#chats button").first().click();
  await closeJob(page);
  await page.locator("#feishu-chats button").first().click();
  await closeJob(page);
  await page.locator("#finish-setup").click();
  await closeJob(page);
  await expect(page.locator("#health")).toContainText("正在监控");
  await page.locator('[data-op="stop"]').click();
  await closeJob(page);
});

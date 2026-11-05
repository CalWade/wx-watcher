const { test, expect } = require("@playwright/test");

async function step(page, number) {
  await page.locator(`[data-step="${number}"]`).click();
  await expect(page.locator(`[data-step="${number}"]`)).toHaveAttribute(
    "aria-current",
    "step",
  );
}
async function more(page, target) {
  if (!(await page.locator("#more-menu").evaluate((el) => el.open)))
    await page.locator("#more-menu > summary").click();
  await page.locator(`nav [data-page="${target}"]`).click();
}
async function saved(page) {
  await expect(page.locator("#job-dialog")).not.toBeVisible();
  await expect(page.locator("#notice")).toContainText("已");
}

test.beforeEach(async ({ page }) => {
  await page.goto("/#token=browser-fixture-token&page=setup");
  await expect(page.locator("#title")).toHaveText("连接与配置");
});
test("onboarding chooses conversation and persists it", async ({ page }) => {
  await step(page, 2);
  await page.locator("#chat-query").fill("文件传输助手");
  await page.locator("#find-chats").click();
  await page.locator("#chats button").first().click();
  await saved(page);
  await expect(page.locator(".selected-value")).toContainText("文件传输助手");
  await expect(page.locator("#step-next")).toBeEnabled();
  await expect(page).not.toHaveURL(/token=/);
});
test("customer text renders literally without HTML execution", async ({
  page,
}) => {
  await page.locator('nav [data-page="overview"]').click();
  await page.locator("#open-records").click();
  await expect(page.locator(".original")).toContainText("<img src=x");
  await expect(page.locator(".record img")).toHaveCount(0);
  expect(await page.evaluate(() => window.injectionExecuted)).toBeUndefined();
});
test("knowledge and call limits can be configured", async ({ page }) => {
  await more(page, "knowledge");
  await page.locator("#business-context").fill("培训时间待确认。");
  await page.locator("#reply-rules").fill("不承诺未知日期。");
  await page.locator("#save-knowledge").click();
  await saved(page);
  await page.reload();
  await expect(page.locator("#business-context")).toHaveValue(
    "培训时间待确认。",
  );
});
test("credential page never displays secret material", async ({ page }) => {
  await more(page, "keytool");
  await expect(
    page.getByText("Keychain 已保存", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("#key-file")).toHaveValue("");
  await expect(page.locator("#backup-password")).toHaveAttribute(
    "type",
    "password",
  );
  await expect(page.locator("#key-file")).toBeHidden();
});
test("capture requires a separate login-page confirmation before login readiness", async ({
  page,
}) => {
  await more(page, "keytool");
  await expect(page.locator("#capture-task")).toBeVisible();
  await expect(page.locator("#capture-login-ready")).toBeHidden();
  if (await page.locator("#recapture").isVisible())
    await page.locator("#recapture").click();
  await page.locator("#capture-check").click();
  await page.locator("#capture-start").click();
  await page.locator("#confirm-yes").click();
  await expect(page.locator("#capture-login-ready")).toBeVisible();
  await expect(page.locator("#capture-progress")).toContainText("先停在登录页");
  await expect(page.locator("#notice")).not.toContainText(
    "现在可以点击微信登录",
  );
  await page.locator("#capture-login-ready").click();
  await expect(page.locator("#capture-login-ready")).toBeHidden();
  await expect(page.locator("#capture-progress")).toContainText("监听已就绪");
  await page.locator("#capture-cancel").click();
  await expect(page.locator("#capture-check")).toBeVisible();
  await expect(page.locator("#capture-check")).toBeEnabled();
});
test("guided steps connect model and destination then start the workbench", async ({
  page,
}) => {
  await step(page, 2);
  await page.locator("#find-chats").click();
  await page.locator("#chats button").first().click();
  await saved(page);
  await page.locator("#step-next").click();
  await expect(page.locator("#chat-query")).toHaveCount(0);
  await page.locator("#auto-connect").click();
  await expect(page.locator("#onboarding-checks")).toContainText(
    "合成模型与费用连接",
  );
  await page.locator("#step-next").click();
  await page.locator("#find-feishu").click();
  await page.locator("#feishu-chats button").first().click();
  await saved(page);
  await page.locator("#step-next").click();
  await page.locator("#finish-setup").click();
  await expect(page.locator("#title")).toHaveText("工作台");
  await expect(page.locator("#health")).toContainText("正在监控");
  await page.locator('[data-op="stop"]').click();
  await expect(page.locator("#health")).toContainText("未运行");
});

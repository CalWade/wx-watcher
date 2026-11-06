const { test, expect } = require("@playwright/test");

const headers = { "X-NoteBridge-UI": "1", Origin: "http://127.0.0.1:19768" };
async function sandbox(page, route, body) {
  const response =
    body === undefined
      ? await page.request.get("/_sandbox/" + route)
      : await page.request.post("/_sandbox/" + route, { headers, data: body });
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function choose(page, step) {
  await page.locator(`[data-step="${step}"]`).click();
  await expect(page.locator(`[data-step="${step}"]`)).toHaveAttribute(
    "aria-current",
    "step",
  );
}

test("real application pipeline supports offline setup, delivery, restart and failed-agent review", async ({
  page,
}) => {
  await page.goto("/#token=sandbox-fixture-token&page=setup");
  await expect(page.locator("#chat-query")).toBeVisible();
  await sandbox(page, "login", { logged_in: false });
  await page.reload();
  await expect(page.locator("#find-chats")).toBeEnabled();
  await expect(page.locator("#setup-login-status")).toBeVisible();
  await page.locator("#chat-query").fill("客户服务测试群");
  await page.locator("#find-chats").click();
  await page
    .getByRole("button", { name: "客户服务测试群", exact: true })
    .click();
  await expect(page.locator(".selected-value")).toContainText("客户服务测试群");
  await sandbox(page, "login", { logged_in: true });
  await page.locator("#refresh-setup-account").click();
  await choose(page, 3);
  await page.locator("#auto-connect").click();
  await expect(page.locator("#onboarding-checks")).toContainText(
    "本地 Agent 接口",
  );
  await choose(page, 4);
  await page.locator("#find-feishu").click();
  await page
    .getByRole("button", { name: "自动验收接收群", exact: true })
    .click();
  await choose(page, 5);
  await page.locator("#send-enabled").check();
  await page.locator("#finish-setup").click();
  await expect(page.locator("#title")).toHaveText("工作台", { timeout: 30000 });
  let snapshot = await sandbox(page, "status");
  expect(snapshot.messages).toHaveLength(1);
  expect(snapshot.tasks).toHaveLength(0);
  await sandbox(page, "emit", {
    text: "培训资料什么时候发送？浏览器完整流程验收",
  });
  await expect
    .poll(
      async () =>
        (await sandbox(page, "status")).tasks.filter((t) => t.status === "sent")
          .length,
      { timeout: 20000 },
    )
    .toBe(1);
  snapshot = await sandbox(page, "status");
  expect(snapshot.messages).toHaveLength(2);
  expect(snapshot.messages[1].body.content).toContain("浏览器完整流程验收");
  expect(snapshot.tasks[0].agent.provider_id).toBe("http");
  expect(snapshot.submissions).toBe(2);
  await page.locator('[data-op="stop"]').click();
  await expect(page.locator('[data-op="start"]')).toBeVisible();
  await page.locator('[data-op="start"]').click();
  await expect(page.locator('[data-op="stop"]')).toBeVisible();
  await sandbox(page, "emit", { text: "123456" });
  await expect
    .poll(
      async () =>
        (await sandbox(page, "status")).tasks.filter(
          (t) => t.status === "skipped",
        ).length,
      { timeout: 10000 },
    )
    .toBe(1);
  snapshot = await sandbox(page, "status");
  expect(snapshot.submissions).toBe(2);
  expect(snapshot.messages).toHaveLength(2);
  await sandbox(page, "agent-mode", { mode: "invalid" });
  await sandbox(page, "emit", { text: "这是一个触发模型格式错误的测试问题" });
  await expect
    .poll(
      async () =>
        (await sandbox(page, "status")).tasks.filter(
          (t) => t.status === "needs_review",
        ).length,
      { timeout: 10000 },
    )
    .toBe(1);
  expect((await sandbox(page, "status")).messages).toHaveLength(2);
  await page.locator('[data-op="stop"]').click();
  await expect(page.locator('[data-op="start"]')).toBeVisible();
  const queued = (await sandbox(page, "status")).tasks.filter(
    (t) => t.status === "queued" || t.status === "inference",
  );
  expect(queued).toHaveLength(0);
});

const { test, expect } = require("@playwright/test");
const path = require("node:path");

async function preview(page, name) {
  if (!process.env.NOTE_BRIDGE_SCREENSHOTS) return;
  await page.locator("#notice").evaluate((el) => {
    el.className = "note";
    el.textContent = "合成界面示例 · 不连接真实微信、模型或飞书";
  });
  await page.screenshot({
    path: path.join("docs/images", name + ".png"),
    fullPage: true,
  });
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 980 });
  await page.route("**/api/settings", async (route) => {
    const response = await route.fetch();
    const settings = await response.json();
    await route.fulfill({
      json: { ...settings, profiles: [], active_profile: "" },
    });
  });
  await page.goto("/#token=browser-fixture-token&page=setup");
});

test("fresh installation starts with key capture and hides advanced imports", async ({
  page,
}) => {
  await expect(page.locator("#first-connect")).toBeVisible();
  await expect(page.locator("#chat-query")).toHaveCount(0);
  await preview(page, "first-run-welcome");
  await page.locator("#first-connect").click();
  await expect(
    page.getByRole("heading", { name: "首次连接微信 · 取钥向导" }),
  ).toBeVisible();
  await expect(page.locator("#capture-start")).toBeDisabled();
  await expect(page.locator("#db-root")).toBeHidden();
  await page.locator("#capture-check").click();
  await expect(page.locator("#capture-start")).toBeEnabled();
  await expect(page.locator("#selected-account")).toContainText("合成当前账号");
  await expect(page.locator("#capture-checks .passed")).toHaveCount(6);
  await preview(page, "first-run-checks");
});

test("missing prerequisites are actionable and never enable capture", async ({
  page,
}) => {
  await page.route("**/api/action/connection-check", (route) =>
    route.fulfill({
      status: 202,
      json: { job_id: "missing-prerequisites" },
    }),
  );
  await page.route("**/api/job?id=missing-prerequisites", (route) =>
    route.fulfill({
      json: {
        status: "completed",
        result: {
          ready: false,
          db_root: "",
          checks: [
            {
              id: "debugger",
              title: "调试工具",
              ok: false,
              action: "xcode",
              message: "请安装适用的 Apple Xcode，完成首次打开后重新检查。",
            },
            {
              id: "database",
              title: "本地消息资料",
              ok: false,
              action: "wechat",
              message: "请先正常登录官方 Mac 微信并发送连接测试消息。",
            },
          ],
        },
      },
    }),
  );
  await page.locator("#first-connect").click();
  await page.locator("#capture-check").click();
  await expect(page.locator("#capture-checks")).toContainText("先正常登录");
  await expect(
    page.getByRole("link", { name: "获取 Apple 开发工具" }),
  ).toBeVisible();
  await expect(page.locator("#capture-start")).toBeDisabled();
});

test("first-run login gate leads to verified completion and session setup", async ({
  page,
}) => {
  await page.locator("#first-connect").click();
  await page.locator("#capture-check").click();
  await expect(page.locator("#capture-start")).toBeEnabled();
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#capture-start").click();
  await expect(page.locator("#capture-login-ready")).toBeVisible();
  await expect(page.locator("#capture-timeline .current")).toContainText(
    "停在登录页",
  );
  await preview(page, "first-run-login-gate");
  await page.locator("#capture-login-ready").click();
  await expect(page.locator("#capture-timeline .current")).toContainText(
    "现在登录",
  );
  await expect(page.locator("#capture-progress")).toContainText("监听已就绪");
  await page.unroute("**/api/settings");
  await page.route("**/api/action/capture-status", (route) =>
    route.fulfill({
      status: 202,
      json: { job_id: "completed-capture" },
    }),
  );
  await page.route("**/api/job?id=completed-capture", (route) =>
    route.fulfill({
      json: {
        status: "completed",
        result: {
          phase: "completed",
          verified: 2,
          total: 2,
          official_restored: true,
          message: "合成凭据已验证并保存，官方客户端已恢复",
        },
      },
    }),
  );
  await expect(page.locator("#continue-setup")).toBeVisible();
  await expect(page.locator("#notice")).toContainText("2 / 2");
  await preview(page, "first-run-complete");
  await page.locator("#continue-setup").click();
  await expect(page.locator("#chat-query")).toBeVisible();
});

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
      json: {
        ...settings,
        profiles: [],
        active_profile: "",
        chat_id: "",
        group_name: "",
        feishu_chat_id: "",
        feishu_chat_name: "",
      },
    });
  });
  await page.route("**/api/action/account-status", (route) =>
    route.fulfill({ status: 202, json: { job_id: "fresh-account" } }),
  );
  await page.route("**/api/job?id=fresh-account", (route) =>
    route.fulfill({
      json: {
        status: "completed",
        result: {
          code: "needs_credentials",
          message: "当前账号尚未保存密钥",
          matches_current: false,
          selected: null,
          current: {
            label: "合成当前账号",
            db_root: "/example/db_storage",
            active: true,
            credential_saved: false,
          },
          accounts: [
            {
              label: "合成当前账号",
              db_root: "/example/db_storage",
              active: true,
              credential_saved: false,
            },
          ],
        },
      },
    }),
  );
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
              action: "privacy",
              message: "读取权限尚未确认，请允许完整磁盘访问；无需先登录。",
            },
          ],
        },
      },
    }),
  );
  await page.locator("#first-connect").click();
  await page.locator("#capture-check").click();
  await expect(page.locator("#capture-checks")).toContainText(
    "读取权限尚未确认",
  );
  await expect(
    page.getByRole("link", { name: "获取 Apple 开发工具" }),
  ).toBeVisible();
  await expect(page.locator("#capture-start")).toBeDisabled();
});

for (const loggedInAfterRestore of [true, false]) {
  test(`first-run completion opens sessions when restored login is ${loggedInAfterRestore}`, async ({
    page,
  }) => {
    await page.locator("#first-connect").click();
    await page.locator("#capture-check").click();
    await expect(page.locator("#capture-start")).toBeEnabled();
    await page.locator("#capture-start").click();
    await page.locator("#confirm-yes").click();
    await expect(page.locator("#capture-login-ready")).toBeHidden();
    await expect(page.locator("#capture-progress")).toContainText(
      "正在准备监听",
    );
    await expect(page.locator("#capture-timeline .current")).toContainText(
      "自动准备监听",
    );
    await preview(page, "first-run-listener-preparation");
    // Simulate the native listener becoming ready, with no user confirmation.
    await page.route("**/api/action/capture-status", (route) =>
      route.fulfill({
        status: 202,
        json: { job_id: "listener-ready" },
      }),
    );
    await page.route("**/api/job?id=listener-ready", (route) =>
      route.fulfill({
        json: {
          status: "completed",
          result: { phase: "ready_for_login", message: "监听已就绪，可以登录" },
        },
      }),
    );
    await expect(page.locator("#capture-timeline .current")).toContainText(
      "现在登录",
    );
    await expect(page.locator("#capture-progress")).toContainText("监听已就绪");
    await page.route("**/api/job?id=listener-ready", (route) =>
      route.fulfill({
        json: {
          status: "completed",
          result: {
            phase: "waiting_database",
            message: "正在等待本地资料，无需发送消息",
          },
        },
      }),
    );
    await expect(page.locator("#capture-task h2")).toHaveText(
      "正在等待资料并确认账号",
    );
    await expect(page.locator("#capture-timeline .current")).toContainText(
      "验证",
    );
    await page.unroute("**/api/settings");
    await page.unroute("**/api/action/account-status");
    await page.unroute("**/api/job?id=fresh-account");
    if (!loggedInAfterRestore) {
      await page.route("**/api/settings", async (route) => {
        const response = await route.fetch();
        const settings = await response.json();
        await route.fulfill({
          json: { ...settings, feishu_chat_id: "oc_synthetic_destination" },
        });
      });
      const selected = {
        label: "刚取钥的合成账号",
        db_root: "/example/db_storage",
        profile_id: "fixture",
        credential_saved: true,
        selected: true,
        active: false,
      };
      await page.route("**/api/action/account-status", (route) =>
        route.fulfill({ status: 202, json: { job_id: "restored-logged-out" } }),
      );
      await page.route("**/api/job?id=restored-logged-out", (route) =>
        route.fulfill({
          json: {
            status: "completed",
            result: {
              code: "local_ready",
              current: null,
              selected,
              matches_current: false,
              can_configure: true,
              accounts: [selected],
            },
          },
        }),
      );
    }
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
    await expect(page.locator("#find-chats")).toBeEnabled();
    await page.locator("#find-chats").click();
    await expect(page.locator("#chats")).toContainText("文件传输助手");
    await page.locator("#chats button").first().click();
    await expect(page.locator(".selected-value")).toContainText("文件传输助手");
    await expect(page.locator("#step-next")).toBeEnabled();
    if (!loggedInAfterRestore) {
      await expect(page.locator("#setup-login-status")).toBeVisible();
      await expect(page.locator(".account-strip")).toContainText(
        "刚取钥的合成账号",
      );
      await page.locator('[data-step="5"]').click();
      await expect(page.locator("#finish-setup")).toBeDisabled();
      await page.locator('[data-step="2"]').click();
      await expect(page.locator("#chat-query")).toBeVisible();
    }
  });
}

test("fresh account captures with no root and never reuses the old active account", async ({
  page,
}) => {
  await page.route("**/api/action/connection-check", (route) =>
    route.fulfill({ status: 202, json: { job_id: "new-login" } }),
  );
  await page.route("**/api/job?id=new-login", (route) =>
    route.fulfill({
      json: {
        status: "completed",
        result: {
          ready: true,
          db_root: "",
          account_mode: "login",
          account_label: "登录时自动识别",
          accounts: [],
          checks: [
            "官方微信",
            "取钥兼容范围",
            "调试工具",
            "微信资料读取权限",
            "本地消息资料",
            "本次连接账号",
          ].map((title, i) => ({
            id: String(i),
            title,
            ok: true,
            message:
              i === 4 ? "登录后自动等待资料生成并验证" : "合成检查已通过",
          })),
        },
      },
    }),
  );
  let payload;
  await page.route("**/api/action/capture-start", (route) => {
    payload = route.request().postDataJSON();
    return route.fulfill({ status: 202, json: { job_id: "synthetic-start" } });
  });
  await page.route("**/api/job?id=synthetic-start", (route) =>
    route.fulfill({
      json: {
        status: "completed",
        result: { capture_id: "synthetic", phase: "preparing_listener" },
      },
    }),
  );
  await page.locator("#first-connect").click();
  await expect(page.locator("#capture-preparation")).toContainText(
    "无需提前登录或发送消息",
  );
  await page.locator("#capture-check").click();
  await expect(page.locator("#selected-account")).toContainText(
    "登录时自动识别",
  );
  await expect(page.locator("#db-root")).toHaveValue("");
  await preview(page, "first-run-new-account");
  await page.locator("#capture-start").click();
  await page.locator("#confirm-yes").click();
  await expect
    .poll(() => payload)
    .toEqual({ db_root: "", discover_account: true });
});

test("multiple historical accounts offer a visible choice and login-other option", async ({
  page,
}) => {
  const accounts = [
    { label: "历史甲", db_root: "/example/a/db_storage" },
    { label: "历史乙", db_root: "/example/b/db_storage" },
  ];
  let choice = {};
  await page.route("**/api/action/connection-check", (route) => {
    choice = route.request().postDataJSON();
    return route.fulfill({ status: 202, json: { job_id: "choose-account" } });
  });
  await page.route("**/api/job?id=choose-account", (route) =>
    route.fulfill({
      json: {
        status: "completed",
        result: {
          ready:
            !!choice.discover_account || choice.db_root === accounts[1].db_root,
          db_root: choice.db_root === accounts[1].db_root ? choice.db_root : "",
          account_mode: choice.discover_account ? "login" : "existing",
          account_label: choice.discover_account
            ? "登录时自动识别"
            : "请选择账号",
          accounts,
          checks: [
            {
              id: "account",
              title: "本次连接账号",
              ok:
                !!choice.discover_account ||
                choice.db_root === accounts[1].db_root,
              message: "请选择要连接的历史账号，或选择登录其他账号。",
            },
          ],
        },
      },
    }),
  );
  await page.locator("#first-connect").click();
  await page.locator("#capture-check").click();
  await expect(page.locator("#capture-start")).toBeDisabled();
  await expect(page.locator("#capture-account-choice")).toBeVisible();
  await preview(page, "first-run-account-choice");
  await page
    .locator("#capture-account-choice")
    .selectOption(accounts[1].db_root);
  await expect(page.locator("#capture-start")).toBeEnabled();
  await expect(page.locator("#db-root")).toHaveValue(accounts[1].db_root);
  await page.locator("#capture-account-choice").selectOption("__login__");
  await expect(page.locator("#selected-account")).toContainText(
    "登录时自动识别",
  );
  await expect(page.locator("#db-root")).toHaveValue("");
  await page.locator("#capture-check").click();
  await expect
    .poll(() => choice)
    .toEqual({ db_root: "", discover_account: true });
});

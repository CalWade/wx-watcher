const { test, expect } = require("@playwright/test");

function sampleReport(hasKey = false) {
  const old = {
    label: "先前保存的账号",
    db_root: "/example/db_storage",
    profile_id: "fixture",
    credential_saved: true,
    selected: true,
    active: false,
  };
  const current = {
    label: "微信新登录账号",
    db_root: "/example/new/db_storage",
    profile_id: hasKey ? "current-fixture" : "",
    credential_saved: hasKey,
    selected: false,
    active: true,
  };
  return {
    code: hasKey ? "switch_required" : "needs_credentials",
    message: hasKey ? "账号不同，请验证并切换" : "当前登录账号尚未保存密钥",
    current,
    selected: old,
    matches_current: false,
    accounts: [current, old],
  };
}
async function mockReport(page, value) {
  await page.route("**/api/action/account-status", (route) =>
    route.fulfill({ status: 202, json: { job_id: "account-report" } }),
  );
  await page.route("**/api/job?id=account-report", (route) =>
    route.fulfill({ json: { status: "completed", result: value() } }),
  );
}

test("different current account is visible and connects its own credential target", async ({
  page,
}) => {
  const report = sampleReport();
  await mockReport(page, () => report);
  await page.goto("/#token=browser-fixture-token&page=setup");
  await expect(page.locator("#wechat-current-account")).toHaveText(
    "微信新登录账号",
  );
  await expect(page.locator("#project-selected-account")).toHaveText(
    "先前保存的账号",
  );
  await expect(page.locator("#account-message")).toContainText("尚未保存密钥");
  await expect(page.locator('[data-step="2"]')).toBeDisabled();
  await expect(page.locator("#find-chats")).toHaveCount(0);
  await page.locator("#account-list").evaluate((el) => (el.open = true));
  await expect(page.locator("#account-list")).toContainText("历史账号资料");
  await expect(page.locator('[data-account-index="1"]')).toBeDisabled();
  if (process.env.NOTE_BRIDGE_SCREENSHOTS) {
    await page.locator("#notice").evaluate((el) => {
      el.className = "note";
      el.textContent = "合成账号示例 · 不连接真实微信、模型或飞书";
    });
    await page.screenshot({
      path: "docs/images/account-switching.png",
      fullPage: true,
    });
  }
  await page.locator("#use-current-account").click();
  await expect(page.locator("#title")).toHaveText("连接微信");
  await expect(page.locator("#db-root")).toHaveValue("/example/new/db_storage");
  await expect(page.locator("#selected-account")).toContainText(
    "微信新登录账号",
  );
  await expect(page.locator("#continue-setup")).toHaveCount(0);
  await expect(page.locator("#capture-start")).toBeDisabled();
  await expect(page.locator('[data-op="verify-key"]')).toHaveCount(0);
  await expect(page.locator("#backup-key")).toBeDisabled();
});

test("saved current account can be verified and selected without reusing old conversation", async ({
  page,
}) => {
  const report = sampleReport(true);
  let switched = false;
  let submitted;
  await mockReport(page, () => report);
  await page.route("**/api/settings", async (route) => {
    const response = await route.fetch();
    const settings = await response.json();
    if (switched) {
      settings.profiles.push({
        id: "current-fixture",
        label: "微信新登录账号",
        db_root: "/example/new/db_storage",
        key_ref: "synthetic-reference",
      });
      settings.active_profile = "current-fixture";
      settings.chat_id = "";
      settings.group_name = "";
      settings.send_enabled = false;
    }
    await route.fulfill({ json: settings });
  });
  await page.route("**/api/action/settings", (route) => {
    submitted = route.request().postDataJSON();
    switched = true;
    report.selected = report.current;
    report.matches_current = true;
    report.code = "matched";
    report.message = "当前登录账号与本项目使用的账号一致";
    return route.fulfill({ status: 202, json: { job_id: "account-switched" } });
  });
  await page.route("**/api/job?id=account-switched", (route) =>
    route.fulfill({
      json: {
        status: "completed",
        result: { saved: true, message: "已验证并切换账号，请重新选择会话" },
      },
    }),
  );
  await page.goto("/#token=browser-fixture-token&page=setup");
  await page.locator("#use-current-account").click();
  await expect(page.locator(".account-strip")).toContainText("微信新登录账号");
  expect(submitted).toEqual({ active_profile: "current-fixture" });
  await expect(page.locator("#chat-query")).toHaveValue("");
  await page.locator('[data-step="5"]').click();
  await expect(page.locator("#send-enabled")).not.toBeChecked();
});

test("denied access offers permission help only on request and never reports old credentials as connected", async ({
  page,
}) => {
  const old = sampleReport().selected;
  await mockReport(page, () => ({
    code: "unavailable",
    message: "需要允许读取微信资料",
    current: null,
    selected: old,
    matches_current: false,
    accounts: [old],
  }));
  await page.addInitScript(() => {
    window.permissionRequests = [];
    window.webkit = {
      messageHandlers: {
        native: {
          postMessage: (value) => window.permissionRequests.push(value.kind),
        },
      },
    };
  });
  await page.goto("/#token=browser-fixture-token&page=setup");
  await expect(page.locator("#account-privacy")).toBeVisible();
  expect(await page.evaluate(() => window.permissionRequests)).toEqual([]);
  await page.locator("#account-privacy").click();
  expect(await page.evaluate(() => window.permissionRequests)).toEqual([
    "open-privacy",
  ]);
  await page.locator("#first-connect").click();
  await expect(page.locator("#capture-check")).toBeVisible();
  await expect(page.locator("#db-root")).toHaveValue("");
  await expect(page.locator("#continue-setup")).toHaveCount(0);
});

test("account names can be edited without switching account or touching credentials", async ({
  page,
}) => {
  const report = sampleReport();
  report.current.custom_label = "";
  report.current.account_id = "wxid_currentdemo_abcd";
  await mockReport(page, () => report);
  let submitted;
  await page.route("**/api/action/account-name", (route) => {
    submitted = route.request().postDataJSON();
    report.current.custom_label = submitted.label;
    report.current.label = submitted.label;
    return route.fulfill({ status: 202, json: { job_id: "rename-account" } });
  });
  await page.route("**/api/job?id=rename-account", (route) =>
    route.fulfill({
      json: {
        status: "completed",
        result: report.current,
      },
    }),
  );
  await page.goto("/#token=browser-fixture-token&page=setup");
  await page.locator("#account-list").evaluate((el) => (el.open = true));
  await page.locator('[data-name-index="0"]').click();
  await expect(page.locator("#account-name-dialog")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 780 });
  const bounds = await page.locator("#account-name-dialog").boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  await page.setViewportSize({ width: 1160, height: 900 });
  if (process.env.NOTE_BRIDGE_SCREENSHOTS)
    await page.screenshot({ path: "docs/images/account-name-edit.png" });
  await expect(page.locator("#account-name-refresh")).toBeDisabled();
  await page.locator("#account-name-input").fill("客户服务号 <img src=x>");
  await page.locator("#account-name-save").click();
  await expect(page.locator("#account-name-dialog")).not.toBeVisible();
  await expect(page.locator("#wechat-current-account")).toHaveText(
    "客户服务号 <img src=x>",
  );
  await expect(page.locator("#wechat-current-account img")).toHaveCount(0);
  await expect(page.locator("#project-selected-account")).toHaveText(
    "先前保存的账号",
  );
  expect(submitted).toEqual({
    db_root: report.current.db_root,
    label: "客户服务号 <img src=x>",
  });
  await page.locator("#use-current-account").click();
  await expect(page.locator("#selected-account")).toContainText(
    "客户服务号 <img src=x>",
  );
  await page.locator("#capture-edit-name").click();
  await expect(page.locator("#account-name-input")).toHaveValue(
    "客户服务号 <img src=x>",
  );
  await page.locator("#account-name-cancel").click();
});

test("temporary login detection loss preserves setup and known account switches still stop it", async ({
  page,
}) => {
  const selected = { ...sampleReport().selected, active: true };
  let report = {
    code: "matched",
    current: selected,
    selected,
    matches_current: true,
    can_configure: true,
    accounts: [selected],
  };
  let polls = 0;
  await mockReport(page, () => {
    polls++;
    return report;
  });
  await page.goto("/#token=browser-fixture-token&page=setup");
  await expect(page.locator("#chat-query")).toBeVisible();
  await page.locator("#chat-query").fill("尚未提交的会话搜索");
  report = {
    ...report,
    code: "local_ready",
    current: null,
    matches_current: false,
  };
  const before = polls;
  await expect.poll(() => polls, { timeout: 10000 }).toBeGreaterThan(before);
  await expect(page.locator("#chat-query")).toHaveValue("尚未提交的会话搜索");
  await expect(page.locator("#find-chats")).toBeEnabled();
  await expect(page.locator("#setup-login-status")).toBeVisible();
  await page.locator('[data-step="3"]').click();
  await page.locator('[data-step="2"]').click();
  await expect(page.locator("#chat-query")).toHaveValue("尚未提交的会话搜索");
  await page.reload();
  await expect(page.locator("#find-chats")).toBeEnabled();
  await expect(page.locator(".account-strip")).toContainText(selected.label);
  if (process.env.NOTE_BRIDGE_SCREENSHOTS)
    await page.screenshot({
      path: "docs/images/setup-after-capture.png",
      fullPage: true,
    });
  report = { ...sampleReport(), can_configure: false };
  await page.locator("#refresh-setup-account").click();
  await expect(page.locator('[data-step="2"]')).toBeDisabled();
  await expect(page.locator("#find-chats")).toHaveCount(0);
  await expect(page.locator("#wechat-current-account")).toHaveText(
    "微信新登录账号",
  );
});

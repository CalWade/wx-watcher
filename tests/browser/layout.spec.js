const { test, expect } = require("@playwright/test");

async function openMore(page, target) {
  await page.locator("#more-menu").evaluate((el) => (el.open = true));
  await page.locator(`[data-page="${target}"]`).click();
}
async function checkLayout(page) {
  const issues = await page.evaluate(() => {
    const issues = [];
    const visible = (el) => {
      if (
        !el.getClientRects().length ||
        getComputedStyle(el).visibility === "hidden"
      )
        return false;
      for (
        let parent = el.parentElement;
        parent;
        parent = parent.parentElement
      ) {
        if (
          parent.tagName === "DETAILS" &&
          !parent.open &&
          !parent.querySelector(":scope > summary")?.contains(el)
        )
          return false;
      }
      return true;
    };
    if (document.documentElement.scrollWidth > innerWidth + 1)
      issues.push("horizontal overflow");
    const controls = [
      ...document.querySelectorAll(
        "main button, main input, main select, main textarea, main summary",
      ),
    ].filter(visible);
    for (let i = 0; i < controls.length; i++) {
      const a = controls[i].getBoundingClientRect();
      if (a.left < -1 || a.right > innerWidth + 1)
        issues.push(`offscreen: ${controls[i].id || controls[i].textContent}`);
      for (let j = i + 1; j < controls.length; j++) {
        const b = controls[j].getBoundingClientRect();
        if (
          Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
          Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1
        )
          issues.push(`overlap: ${controls[i].id} / ${controls[j].id}`);
      }
    }
    for (const group of document.querySelectorAll(".actions, .field-action")) {
      if (visible(group) && parseFloat(getComputedStyle(group).gap) < 12)
        issues.push("crowded action group");
    }
    return issues;
  });
  expect(issues).toEqual([]);
}

test("only core navigation is visible; secondary features stay in More", async ({
  page,
}) => {
  await page.goto("/#token=browser-fixture-token&page=setup");
  await expect(
    page.locator('nav[aria-label="主导航"] button:visible'),
  ).toHaveCount(2);
  await expect(
    page.locator('nav[aria-label="更多功能"] button:visible'),
  ).toHaveCount(0);
  await page.locator("#more-menu > summary").click();
  await page.locator('[data-page="knowledge"]').click();
  await expect(page.locator("#business-context")).toBeVisible();
  await page.locator(".brand").click();
  await expect(page.locator("#title")).toHaveText("工作台");
});

test("periodic status polling preserves account details, focus and typed input", async ({
  page,
}) => {
  await page.goto("/#token=browser-fixture-token&page=setup");
  await page.locator('[data-step="1"]').click();
  await page.locator("#account-list > summary").click();
  await page.locator("#account-list > summary").focus();
  await page.waitForTimeout(5500);
  await expect(page.locator("#account-list")).toHaveAttribute("open", "");
  await expect(page.locator("#account-list > summary")).toBeFocused();
  await page.locator('[data-step="2"]').click();
  await page.locator("#chat-query").fill("尚未提交的搜索");
  await page.waitForTimeout(5500);
  await expect(page.locator("#chat-query")).toHaveValue("尚未提交的搜索");
  await expect(page.locator("#chat-query")).toBeFocused();
});

for (const width of [1160, 900, 760, 390]) {
  test(`forms and controls have spacing without overlaps at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 780 });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("/#token=browser-fixture-token&page=setup");
    await expect(page.locator(".steps")).toBeVisible();
    for (const number of [1, 2, 3, 4, 5]) {
      await page.locator(`[data-step="${number}"]`).click();
      if (number === 4) {
        await page.locator("#feishu-transport").selectOption("openapi");
        await expect(page.locator("#app-secret")).toBeVisible();
      }
      await checkLayout(page);
    }
    await openMore(page, "keytool");
    await page.locator("#existing-options > summary").click();
    await checkLayout(page);
    await openMore(page, "knowledge");
    await checkLayout(page);
    expect(errors).toEqual([]);
  });
}

test("cancelled capture can be retried and returning to the wizard keeps live progress", async ({
  page,
}) => {
  await page.goto("/#token=browser-fixture-token&page=keytool");
  await expect(page.locator("#capture-task")).toBeVisible();
  if (await page.locator("#recapture").isVisible())
    await page.locator("#recapture").click();
  await page.locator("#capture-check").click();
  await page.locator("#capture-start").click();
  await page.locator("#confirm-no").click();
  await expect(page.locator("#capture-start")).toBeEnabled();
  await expect(page.locator("#capture-login-ready")).toBeHidden();
  await page.locator("#capture-start").click();
  await page.locator("#confirm-yes").click();
  await expect(page.locator("#capture-login-ready")).toBeHidden();
  await expect(page.locator("#capture-progress")).toContainText("正在准备监听");
  await page.locator('[data-page="overview"]').click();
  await openMore(page, "keytool");
  await expect(page.locator("#capture-login-ready")).toBeHidden();
  await expect(page.locator("#capture-progress")).toContainText("正在准备监听");
  await expect(page.locator("#capture-check")).toBeHidden();
  await expect(page.locator("#import-key")).toBeDisabled();
  await page.locator("#capture-cancel").click();
  await expect(page.locator("#capture-check")).toBeVisible();
  await expect(page.locator("#capture-check")).toBeEnabled();
  if (await page.locator("#recapture").isVisible())
    await page.locator("#recapture").click();
  await page.locator("#capture-check").click();
  await expect(page.locator("#capture-start")).toBeEnabled();
});

test("an empty search result is explained in the current step", async ({
  page,
}) => {
  await page.route("**/api/action/conversations", (r) =>
    r.fulfill({ status: 202, json: { job_id: "empty-conversations" } }),
  );
  await page.route("**/api/job?id=empty-conversations", (r) =>
    r.fulfill({ json: { status: "completed", result: [] } }),
  );
  await page.goto("/#token=browser-fixture-token&page=setup");
  await page.locator('[data-step="2"]').click();
  await page.locator("#chat-query").fill("不存在的群");
  await page.locator("#chat-query").press("Enter");
  await expect(page.locator("#chats")).toContainText("没有匹配的会话");
  await expect(page.locator("#job-dialog")).not.toBeVisible();
});

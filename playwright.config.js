const { defineConfig } = require("@playwright/test");
module.exports = defineConfig({
  testDir: "tests/browser",
  use: {
    baseURL: "http://127.0.0.1:19767",
    browserName: "chromium",
    channel: process.env.CI ? undefined : "chrome",
  },
  webServer: {
    command: "uv run python scripts/test_console_server.py",
    url: "http://127.0.0.1:19767",
    reuseExistingServer: false,
  },
  reporter: "list",
  workers: 1,
});

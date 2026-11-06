const { defineConfig } = require("@playwright/test");
module.exports = defineConfig({
  testDir: "tests/e2e",
  timeout: 60000,
  use: {
    baseURL: "http://127.0.0.1:19768",
    browserName: "chromium",
  },
  webServer: {
    command: "uv run python scripts/dev.py serve --port 19768",
    url: "http://127.0.0.1:19768/",
    reuseExistingServer: false,
    timeout: 30000,
  },
  reporter: "list",
  workers: 1,
});

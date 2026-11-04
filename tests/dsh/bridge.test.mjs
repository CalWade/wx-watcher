import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import http from "node:http";

// A deliberately small host SDK fixture exercises the real HTTP/durable-job boundary.
// Actual DSH inference, model routing and cost recording are a separate live check.
test("bridge authenticates, denies tools, deduplicates and recovers interrupted jobs", async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "wx-bridge-"));
  let cleanup;
  const previousHome = process.env.DSH_HOME;
  const previousHost = process.env.DSH_HOST_PACKAGE_JSON;
  try {
    const sdk = path.join(directory, "node_modules/@deepseek-ai/dsh-llm");
    await fs.mkdir(sdk, { recursive: true });
    await fs.writeFile(
      path.join(sdk, "package.json"),
      JSON.stringify({ type: "module", exports: "./index.mjs" }),
    );
    await fs.writeFile(
      path.join(sdk, "index.mjs"),
      "export const createUserMessage = value => value;",
    );
    process.env.DSH_HOME = directory;
    process.env.DSH_HOST_PACKAGE_JSON = path.join(directory, "package.json");
    const { apply } = await import("../../integrations/dsh/index.mjs");
    const socket = http.createServer();
    await new Promise((resolve) => socket.listen(0, "127.0.0.1", resolve));
    const port = socket.address().port;
    await new Promise((resolve) => socket.close(resolve));
    let calls = 0;
    let checkpoints = 0;
    const context = {
      agentDefaultModel: {
        currentSelection: () => ({ provider: "fixture", model: "fixture" }),
      },
      agents: {
        create: async (options) => {
          calls++;
          const listeners = new Map();
          const session = {};
          let restriction, guard;
          const agent = {
            session,
            followup: () => {},
            cancel: () => {},
            whenIdle: async () => {
              assert.deepEqual(restriction, { allow: [] });
              assert.match(guard(), /only drafts replies/);
              const assembled = await listeners.get("system-prompt/assemble")(
                {},
                {},
                async () => ({ tools: [{ name: "must-be-removed" }] }),
              );
              assert.deepEqual(assembled.tools, []);
              const emit = (event) =>
                listeners.get("session/event")(session, event);
              emit({ type: "request/header", data: { header: { tools: [] } } });
              emit({
                type: "assistant/message",
                data: {
                  message: {
                    content: [
                      {
                        type: "text",
                        text: '{"should_reply":false,"summary":"fixture","suggested_reply":"","reason":"fixture","needs_confirmation":[]}',
                      },
                    ],
                  },
                },
              });
              emit({
                type: "turn/end",
                data: { reason: { kind: "completed" } },
              });
            },
          };
          await options.setup(
            {
              tools: {
                restrict: (v) => (restriction = v),
                presentAs: () => {},
                guard: (v) => (guard = v),
                schemas: () => [],
              },
              systemPrompt: {
                suppressRuntimeContext: () => {},
                section: () => {},
              },
              on: (name, fn) => listeners.set(name, fn),
            },
            agent,
          );
          return { agent, dispose: async () => {} };
        },
      },
      sessionProjectionCache: {
        write: async () => {
          checkpoints++;
        },
      },
      sessionProjections: { stateOf: () => ({ totalTokens: 12 }) },
      costMeter: {
        getSessionCost: async () => ({ found: true, own: { cost: 0.01 } }),
      },
      effect: (factory) => {
        cleanup = factory();
      },
    };
    await apply(context, { port });
    const token = (
      await fs.readFile(
        path.join(directory, "storages/wx-reply-agent/token"),
        "utf8",
      )
    ).trim();
    const request = async (route, body, auth = true) => {
      const response = await fetch(`http://127.0.0.1:${port}${route}`, {
        method: body === undefined ? "GET" : "POST",
        headers: auth ? { Authorization: "Bearer " + token } : {},
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: response.status, body: await response.json() };
    };
    assert.equal((await request("/v1/status", undefined, false)).status, 401);
    assert.equal(
      (await request("/v1/jobs", { id: "invalid", prompt: "text" })).status,
      400,
    );
    const input = { id: "a".repeat(64), prompt: "synthetic question" };
    assert.equal((await request("/v1/jobs", input)).status, 202);
    let job;
    for (let i = 0; i < 100; i++) {
      job = (await request("/v1/jobs/" + input.id)).body;
      if (job.status === "completed") break;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.equal(job.status, "completed");
    assert.equal(job.request_tool_count, 0);
    assert.equal(job.cost_meter.found, true);
    assert.equal(checkpoints, 1);
    assert.equal((await request("/v1/jobs", input)).status, 200);
    assert.equal(
      (await request("/v1/jobs", { ...input, prompt: "different" })).status,
      409,
    );
    assert.equal(calls, 1);
    await cleanup();
    cleanup = null;
    const jobs = path.join(directory, "storages/wx-reply-agent/jobs");
    const interrupted = {
      id: "b".repeat(64),
      status: "running",
      session_id: "fixture",
    };
    await fs.writeFile(
      path.join(jobs, interrupted.id + ".json"),
      JSON.stringify(interrupted),
    );
    await fs.writeFile(path.join(jobs, "c".repeat(64) + ".json"), "{broken");
    await apply(context, { port });
    assert.equal(
      (await request("/v1/jobs/" + interrupted.id)).body.status,
      "interrupted",
    );
    assert.equal((await request("/v1/status")).body.quarantined_jobs, 1);
    assert.equal(calls, 1);
  } finally {
    if (cleanup) await cleanup();
    if (previousHome === undefined) delete process.env.DSH_HOME;
    else process.env.DSH_HOME = previousHome;
    if (previousHost === undefined) delete process.env.DSH_HOST_PACKAGE_JSON;
    else process.env.DSH_HOST_PACKAGE_JSON = previousHost;
    await fs.rm(directory, { recursive: true, force: true });
  }
});

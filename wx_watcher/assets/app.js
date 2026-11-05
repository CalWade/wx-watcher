"use strict";
const $ = (s) => document.querySelector(s);
const labels = {
  overview: "工作台",
  setup: "连接与配置",
  keytool: "连接微信",
  records: "回复建议",
  knowledge: "业务资料",
  diagnostics: "运行诊断",
};
let settings = {},
  state = {},
  page = "setup",
  timer;
let connectionReport = null;
let captureReadiness = null;
let captureProgress = null;
let accountReport = null;
let pendingAccount = null;
let setupStep = null;
let setupDraft = {};
let activeCaptureId = null;
let activeOperation = false;
let refreshPending = null;
let lastPassiveView = "";
let noticeTimer;
let initialized = false;
let requestRecapture = false;
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const field = (label, id, value = "", type = "text") =>
  `<label>${escape(label)}<input id="${id}" type="${type}" value="${escape(value)}"></label>`;
const button = (text, op, primary = false) =>
  `<button data-op="${op}" class="${primary ? "primary" : ""}">${escape(text)}</button>`;
const card = (title, body) =>
  `<section class="card"><h2>${escape(title)}</h2>${body}</section>`;
function notice(message, error = false) {
  $("#notice").className = error ? "error" : "success";
  $("#notice").textContent = message;
  clearTimeout(noticeTimer);
  if (!error)
    noticeTimer = setTimeout(() => {
      if ($("#notice").textContent === message) {
        $("#notice").textContent = "";
        $("#notice").className = "";
      }
    }, 6000);
}
async function api(route, data) {
  const response = await fetch("/api/" + route, {
    method: data === undefined ? "GET" : "POST",
    headers:
      data === undefined
        ? {}
        : { "Content-Type": "application/json", "X-NoteBridge-UI": "1" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "请求失败");
  return value;
}
function confirmAction(message) {
  const dialog = $("#confirm-dialog");
  if (dialog.open) return Promise.resolve(false);
  $("#confirm-message").textContent = message;
  return new Promise((resolve) => {
    let finished = false;
    const finish = (value) => {
      if (finished) return;
      finished = true;
      dialog.close();
      dialog.removeEventListener("cancel", cancel);
      resolve(value);
    };
    const cancel = (e) => {
      e.preventDefault();
      finish(false);
    };
    $("#confirm-no").onclick = () => finish(false);
    $("#confirm-yes").onclick = () => finish(true);
    dialog.addEventListener("cancel", cancel);
    dialog.showModal();
    $("#confirm-no").focus();
  });
}
async function action(op, data = {}, title = "正在处理") {
  if (activeOperation) throw new Error("上一步还在处理，请稍候。");
  activeOperation = true;
  const dialog = $("#job-dialog");
  $("#job-title").textContent = title;
  $("#job-result").textContent = "";
  $("#job-details").open = false;
  $("#job-summary").textContent = "";
  $("#job-progress").textContent = "正在处理，请稍候…";
  $("#job-close").disabled = true;
  if (!dialog.open) dialog.showModal();
  try {
    const result = await quietAction(op, data);
    await refresh();
    activeOperation = false;
    $("#job-progress").textContent = "已完成";
    $("#job-summary").textContent = describeResult(op, result);
    $("#job-result").textContent = JSON.stringify(result, null, 2);
    // Ordinary saves/searches return to the task without an extra acknowledgement.
    if (!["doctor", "test-dsh", "test-feishu"].includes(op)) dialog.close();
    if (
      !["connection-check", "capture-start", "capture-login-ready"].includes(op)
    )
      notice(describeResult(op, result));
    return result;
  } catch (error) {
    dialog.close();
    notice(error.message, true);
    throw error;
  } finally {
    $("#job-close").disabled = false;
    activeOperation = false;
  }
}
function describeResult(op, value) {
  if (op === "doctor" && value.wechat_data_access?.readable === false)
    return value.wechat_data_access.hint;
  if (value.message || value.hint) return value.message || value.hint;
  if (op === "connect-dsh")
    return value.ready
      ? "DSH 与费用插件已连接，可以继续下一步。"
      : value.message;
  if (op === "auto-connect")
    return value.checks.every((c) => c.ok)
      ? "本机连接已就绪，可以继续下一步。"
      : "已完成可用的连接，请处理页面中标出的事项。";
  if (op === "connection-check")
    return value.ready
      ? "检查已通过，可以开始连接。"
      : "还有条件未满足，请处理下方提示后重新检查。";
  if (op === "capture-preflight")
    return value.ready
      ? "环境检查通过。开始后工具会自动准备监听，微信自动打开后登录即可。"
      : "暂时不能开始取钥。" +
          (value.permission_hint ||
            value.account_hint ||
            "请展开详细结果，检查微信版本、调试器与数据读取权限。");
  if (op === "test-dsh")
    return `模型已返回建议；请求工具数：${value.tools}。费用统计：${value.cost_meter?.found ? "已记账" : "尚未找到，请检查 dsh-cost-meter"}。`;
  if (op === "test-feishu") return "测试消息已发送到选定飞书群，回读验证通过。";
  if (op === "install-dsh")
    return value.ready
      ? "桥接插件已就绪，继续使用 DSH 当前默认模型。"
      : "配置已安装，请检查 DSH 插件状态。";
  if (op === "verify-key" || value.connected)
    return `已验证 ${value.verified} / ${value.total} 份数据库。`;
  if (op === "backup-key")
    return "加密备份已生成，请保存下载文件并单独保管口令。";
  if (Array.isArray(value))
    return `已找到 ${value.length} 项，请在列表中选择。`;
  if (op === "feishu-chats")
    return `已找到 ${value.data?.chats?.length || 0} 个机器人可访问的群。`;
  if (op === "login-service")
    return value.installed
      ? "已设置登录 Mac 后启动本地服务，下次登录生效。"
      : "已关闭登录启动。";
  if (value.running === true) return "读取与回复服务已启动。";
  if (value.running === false) return "本应用启动的服务已停止。";
  if (value.saved) return "设置已保存。";
  return "操作已完成。";
}
async function quietAction(op, data = {}) {
  const { job_id } = await api("action/" + op, data);
  let job;
  do {
    await new Promise((r) => setTimeout(r, 250));
    job = await api("job?id=" + job_id);
  } while (job.status === "running");
  if (job.status !== "completed") throw new Error(job.error || "操作未完成");
  return job.result;
}
function bind(id, fn) {
  const element = $("#" + id);
  if (!element) return;
  element.addEventListener("click", async () => {
    if (element.dataset.busy) return;
    element.dataset.busy = "true";
    element.setAttribute("aria-busy", "true");
    try {
      await fn();
    } catch (e) {
      notice(e.message, true);
    } finally {
      delete element.dataset.busy;
      element.removeAttribute("aria-busy");
    }
  });
}
function openPrivacy() {
  if (window.webkit?.messageHandlers?.native)
    window.webkit.messageHandlers.native.postMessage({
      kind: "open-privacy",
      id: crypto.randomUUID(),
    });
  else
    notice(
      "请打开系统设置 → 隐私与安全性 → 完整磁盘访问权限，允许实际运行本项目的应用。",
    );
}
function nativePick(kind) {
  return new Promise((resolve) => {
    if (!window.webkit?.messageHandlers?.native) {
      const value = window.prompt(
        kind === "choose-file" ? "填写文件绝对路径" : "填写目录绝对路径",
      );
      resolve(value || "");
      return;
    }
    const id = crypto.randomUUID();
    const listener = (e) => {
      if (e.detail.id === id) {
        window.removeEventListener("native-result", listener);
        resolve(e.detail.value || "");
      }
    };
    window.addEventListener("native-result", listener);
    window.webkit.messageHandlers.native.postMessage({ kind, id });
  });
}
async function refresh() {
  if (refreshPending) return refreshPending;
  refreshPending = refreshState();
  try {
    await refreshPending;
  } finally {
    refreshPending = null;
  }
}
async function refreshState() {
  const fresh = await Promise.all([api("settings"), api("status")]);
  [settings, state] = fresh;
  try {
    const previous = accountReport?.current?.db_root;
    accountReport = await quietAction("account-status");
    if (previous !== accountReport.current?.db_root) {
      captureReadiness = null;
      if (previous && !activeCaptureId) {
        pendingAccount = null;
        captureProgress = null;
        setupDraft = {};
        connectionReport = null;
        setupStep = 1;
        if (page === "setup" || page === "keytool") render();
      }
    }
  } catch (error) {
    captureReadiness = null;
    accountReport = {
      code: "unavailable",
      message: error.message,
      accounts: [],
    };
  }
  paintAccountPanel();
  const running = state.services.running;
  const problem =
    state.services.last_error ||
    state.services.worker?.last_error ||
    (running && state.services.reader?.state !== "ready"
      ? state.services.reader?.error
      : "");
  $("#health").textContent = problem
    ? "● 需要处理"
    : running
      ? "● 正在监控"
      : "○ 未运行";
  $("#health").className = "pill " + (problem ? "warn" : running ? "ok" : "");
}
function accountPanelHTML(framed = true) {
  const r = accountReport;
  const rows = r?.accounts || [];
  const hasSaved = settings.profiles?.length > 0;
  return `<section id="account-panel" ${framed ? 'class="card"' : ""}><h2>微信账号</h2><div class="account-summary"><div><span class="label">微信当前登录</span><strong id="wechat-current-account">${escape(r?.current?.label || "尚未识别，请登录微信后刷新")}</strong></div><div><span class="label">NoteBridge 已连接</span><strong id="project-selected-account">${escape(r?.selected?.label || "尚未连接")}</strong></div></div><p id="account-message" class="${r?.matches_current ? "success" : "note"}">${escape(r?.message || "正在识别账号…")}</p><div class="actions">${r?.code === "unavailable" ? '<button id="account-privacy" class="primary">允许读取微信资料</button>' : ""}${(!hasSaved || !r?.current) && page === "setup" ? `<button id="first-connect" class="${r?.code === "unavailable" ? "" : "primary"}">${hasSaved ? "检查微信连接" : "首次连接微信"}</button>` : ""}${r?.current && (hasSaved || page !== "setup") ? `<button id="use-current-account" class="${r.matches_current || page === "keytool" ? "" : "primary"}">${r.current.credential_saved ? (r.matches_current ? "验证当前账号" : "验证并切换到当前账号") : "连接当前账号"}</button>` : ""}<button id="refresh-accounts" class="text-button">刷新账号</button></div><details id="account-list"><summary id="account-toggle">已发现的账号 · ${rows.length}</summary>${rows.map((a, i) => `<div class="row"><div class="grow"><strong>${escape(a.label)}</strong><p class="muted">${a.active ? "微信当前登录 · " : "历史账号资料 · "}${a.credential_saved ? "密钥已保存" : "尚无密钥"}${a.selected ? " · 本项目正在使用" : ""}</p></div><button data-account-index="${i}" ${a.active ? "" : "disabled"}>${a.credential_saved ? "验证并使用" : "连接此账号"}</button></div>`).join("")}<p class="muted">更换账号请先在微信登录，再回来刷新。每个账号分别保存凭据。</p></details></section>`;
}
function updateAccountActions() {
  const matches = accountReport?.matches_current === true;
  if ($("#find-chats")) $("#find-chats").disabled = !matches;
  if ($("#finish-setup"))
    $("#finish-setup").disabled =
      !matches || !settings.chat_id || !settings.feishu_chat_id;
  document
    .querySelectorAll('[data-op="start"]')
    .forEach((b) => (b.disabled = !matches || !settings.chat_id));
  document
    .querySelectorAll("[data-step]")
    .forEach((b) => (b.disabled = Number(b.dataset.step) > 1 && !matches));
  if ($("#capture-start") && !activeCaptureId)
    $("#capture-start").disabled = !captureReadiness?.ready;
}
function paintAccountPanel() {
  const panel = $("#account-panel");
  if (!panel) {
    updateAccountActions();
    return;
  }
  const html = accountPanelHTML(panel.classList.contains("card"));
  // Replacing unchanged markup every five seconds collapsed details and stole focus.
  if (panel.dataset.report === html) {
    updateAccountActions();
    return;
  }
  const expanded = $("#account-list")?.open;
  const focusedId = panel.contains(document.activeElement)
    ? document.activeElement.id
    : null;
  panel.outerHTML = html;
  $("#account-panel").dataset.report = html;
  if ($("#account-list")) $("#account-list").open = !!expanded;
  bindAccountPanel();
  if (focusedId) $("#" + focusedId)?.focus({ preventScroll: true });
  updateAccountActions();
}
function bindAccountPanel() {
  const panel = $("#account-panel");
  if (panel)
    panel.dataset.report = accountPanelHTML(panel.classList.contains("card"));
  bind("first-connect", () => {
    pendingAccount = accountReport?.current || null;
    captureReadiness = null;
    if (!activeCaptureId) captureProgress = null;
    navigate("keytool");
  });
  bind("account-privacy", openPrivacy);
  bind("refresh-accounts", async () => {
    await refresh();
    notice(accountReport?.message || "账号状态已刷新");
  });
  bind("use-current-account", () => useAccount(accountReport.current));
  document.querySelectorAll("[data-account-index]").forEach((b) => {
    b.onclick = () =>
      useAccount(accountReport.accounts[Number(b.dataset.accountIndex)]).catch(
        (e) => notice(e.message, true),
      );
  });
}
async function useAccount(account) {
  if (!account?.active)
    throw new Error("请先在微信中登录此账号，再刷新账号状态。");
  if (state.services.running) {
    if (
      !(await confirmAction(
        "切换或连接账号前需要暂停当前监控。是否暂停？已有凭据与任务记录会保留。",
      ))
    )
      return;
    await action("stop", {}, "暂停当前监控");
    $("#job-dialog").close();
  }
  if (account.credential_saved) {
    const selected = await action(
      "settings",
      { active_profile: account.profile_id },
      "验证并使用当前微信账号",
    );
    $("#job-dialog").close();
    pendingAccount = null;
    setupStep = 2;
    setupDraft = {};
    connectionReport = null;
    captureReadiness = null;
    navigate("setup");
    notice(selected.message || "当前账号凭据验证通过，可以继续使用当前配置。");
    return;
  }
  pendingAccount = account;
  requestRecapture = false;
  captureReadiness = null;
  captureProgress = null;
  navigate("keytool");
  notice(
    "已选择当前登录账号作为取钥目标。请先检查，再按提示授权；旧账号凭据会保留。",
  );
}
function render() {
  $("#title").textContent = labels[page];
  $("#subtitle").textContent = {
    overview: "关注新消息，确认回复建议。",
    setup: "一步一步接入，完成后开始处理新消息。",
    keytool: "连接当前微信账号，凭据仅保存在本机。",
    records: "原问题、回复草稿和需要你确认的事项。",
    knowledge: "补充业务事实，让回复更贴合你的工作。",
    diagnostics: "检查连接状态，处理运行异常。",
  }[page];
  document
    .querySelectorAll("nav button")
    .forEach((b) =>
      b.classList.toggle(
        "active",
        b.dataset.page === page ||
          (page === "records" && b.dataset.page === "overview") ||
          (page === "keytool" && b.dataset.page === "setup"),
      ),
    );
  if (page === "overview") overview();
  if (page === "setup") setup();
  if (page === "keytool") keytool();
  if (page === "records") records();
  if (page === "knowledge") knowledge();
  if (page === "diagnostics") diagnostics();
  updateAccountActions();
  if (page === "keytool" && activeCaptureId) {
    document
      .querySelectorAll("#existing-options button, #existing-options input")
      .forEach((el) => (el.disabled = true));
    watchCapture(activeCaptureId);
  }
  document.querySelectorAll("[data-op]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        await action(b.dataset.op, {}, b.textContent);
        render();
      } catch (e) {
        notice(e.message, true);
      }
    }),
  );
}
function overview() {
  const records = state.records || [];
  const sent = records.filter((r) => r.status === "sent").length;
  const review = records.filter((r) =>
    ["needs_review", "delivery_review", "budget_review"].includes(r.status),
  ).length;
  const cost = records.reduce(
    (a, r) => a + (r.dsh?.cost_meter?.own?.cost || 0),
    0,
  );
  const ready =
    accountReport?.matches_current &&
    settings.chat_id &&
    settings.feishu_chat_id;
  const problem =
    state.services.last_error || state.services.worker?.last_error;
  $("#content").innerHTML =
    `${problem ? `<p class="error">${escape(problem)}</p>` : ""}<section class="hero"><div><h2>${state.services.running ? "正在关注新消息" : ready ? "一切就绪，随时开始" : "连接微信，开始你的工作流"}</h2><p>${!accountReport?.matches_current ? escape(accountReport?.message || "先完成微信账号连接，再选择会话与接收群。") : "微信新消息会生成回复建议，按设置送到飞书内部群。"}</p></div><div class="actions">${state.services.running ? button("暂停监控", "stop", true) : ready ? button("开始监控", "start", true) : '<button id="open-setup" class="primary">继续接入设置 →</button>'}</div></section><div class="grid">${card("已投递建议", `<div class="metric">${sent}</div><small>最近 100 条任务</small>`)}${card("需要你确认", `<div class="metric">${review}</div><small>模型、预算或投递异常</small>`)}${card("模型费用估算", `<div class="metric">$${cost.toFixed(4)}</div><small>dsh-cost-meter 用量记录</small>`)}</div>${card("当前连接", `<div class="row"><span class="muted">微信账号</span><strong>${escape(accountReport?.current?.label || "尚未识别")}</strong></div><div class="row"><span class="muted">监听会话</span><strong>${escape(accountReport?.matches_current ? settings.group_name || "尚未选择" : "请先连接当前账号")}</strong></div><div class="row"><span class="muted">飞书接收群</span><strong>${escape(settings.feishu_chat_name || "尚未选择")}</strong></div><div class="row"><span class="muted">运行模式</span><strong>${settings.send_enabled ? "自动投递飞书内部群" : "仅保存本地草稿"}</strong></div><div class="actions"><button id="edit-setup" class="text-button">调整连接配置 →</button></div>`)}<div class="section-title"><h2>最近回复建议</h2><button id="open-records" class="text-button">查看全部 →</button></div>${
      records.length
        ? records
            .slice(0, 3)
            .map(
              (r) =>
                `<article class="record"><div class="row"><strong>${escape(r.group)}</strong><span class="pill">${escape(recordStatus(r.status))}</span></div><p class="original">${escape(r.event?.content || r.event?.text || "")}</p><p>${escape(r.proposal?.suggested_reply || "等待生成回复建议")}</p></article>`,
            )
            .join("")
        : '<div class="empty">开始运行后，新消息的回复建议会出现在这里。</div>'
    }`;
  bind("open-setup", () => {
    setupStep = defaultSetupStep();
    navigate("setup");
  });
  bind("edit-setup", () => {
    setupStep = 1;
    navigate("setup");
  });
  bind("open-records", () => navigate("records"));
}
function recordStatus(status) {
  return (
    {
      sent: "已投递",
      draft: "本地草稿",
      queued: "等待生成",
      inference: "生成中",
      needs_review: "模型结果需检查",
      delivery_review: "投递结果待核对",
      budget_review: "达到调用上限",
      skipped: "已跳过",
      dismissed: "已忽略",
      resolved: "已重新安排",
      sending: "正在投递",
    }[status] || status
  );
}
function renderConversationChoices(rows, searched = false) {
  const target = $("#chats");
  if (!target) return;
  target.replaceChildren(
    ...rows.map((r) => {
      const b = document.createElement("button");
      b.textContent = r.name;
      b.classList.toggle("selected", settings.chat_id === r.username);
      b.setAttribute("aria-pressed", String(settings.chat_id === r.username));
      b.onclick = async () => {
        try {
          await action(
            "settings",
            { chat_id: r.username, group_name: r.name },
            "选择监听会话",
          );
          render();
        } catch (e) {
          notice(e.message, true);
        }
      };
      return b;
    }),
  );
  if (searched && !rows.length)
    target.innerHTML = '<p class="empty">没有匹配的会话，换个名称试试。</p>';
}
function renderFeishuChoices(rows, searched = false) {
  const target = $("#feishu-chats");
  if (!target) return;
  target.replaceChildren(
    ...rows.map((r) => {
      const b = document.createElement("button");
      b.textContent = r.name;
      b.classList.toggle("selected", settings.feishu_chat_id === r.chat_id);
      b.setAttribute(
        "aria-pressed",
        String(settings.feishu_chat_id === r.chat_id),
      );
      b.onclick = async () => {
        try {
          await action(
            "settings",
            { feishu_chat_id: r.chat_id, feishu_chat_name: r.name },
            "选择飞书接收群",
          );
          render();
        } catch (e) {
          notice(e.message, true);
        }
      };
      return b;
    }),
  );
  if (searched && !rows.length)
    target.innerHTML =
      '<p class="empty">没有找到群。请先在飞书群中添加当前应用的机器人，再重新获取。</p>';
}
function runOptions() {
  return {
    send_enabled: $("#send-enabled").checked,
    autostart_monitor: $("#autostart-monitor").checked,
    login_enabled: $("#login-start").checked,
  };
}
const setupStages = [
  "连接微信",
  "选择会话",
  "接入模型",
  "接入飞书",
  "开始运行",
];
function defaultSetupStep() {
  if (!accountReport?.matches_current) return 1;
  if (!settings.chat_id) return 2;
  if (!settings.feishu_chat_id) return 3;
  return 5;
}
function setSetupStep(step) {
  if (step > 1 && !accountReport?.matches_current) return;
  rememberSetupDraft();
  setupStep = step;
  render();
  window.scrollTo(0, 0);
}
function rememberSetupDraft() {
  for (const id of [
    "chat-query",
    "feishu-transport",
    "app-id",
    "send-enabled",
    "autostart-monitor",
    "login-start",
  ]) {
    const el = $("#" + id);
    if (el) setupDraft[id] = el.type === "checkbox" ? el.checked : el.value;
  }
}
function stepFooter(previous, next, nextTitle = "下一步", disabled = false) {
  return `<div class="step-footer"><button id="step-back" class="text-button" ${previous ? "" : "hidden"}>← 上一步</button><div class="actions">${next ? `<button id="step-next" class="primary" ${disabled ? "disabled" : ""}>${escape(nextTitle)} →</button>` : ""}</div></div>`;
}
function transportFields() {
  const transport = setupDraft["feishu-transport"] || settings.feishu_transport;
  return transport === "openapi"
    ? `<p class="muted">在飞书开放平台创建应用，并将机器人添加到接收群。</p><div class="form">${field("App ID", "app-id", setupDraft["app-id"] || settings.feishu_app_id)}${field("App Secret · 保存后不再显示", "app-secret", "", "password")}</div><div class="actions"><button id="save-secret">安全保存应用凭据</button><button id="feishu-permissions" class="text-button">下载权限配置</button><a href="https://open.feishu.cn/app" target="_blank" rel="noreferrer">应用后台 ↗</a></div>`
    : `<p class="note">使用本机 Lark CLI 已连接的应用，无需重新填写密钥。先将这个应用的机器人添加到飞书接收群。</p>`;
}
function bindTransportFields() {
  bind("feishu-permissions", () =>
    download("feishu-permissions.json", {
      scopes: {
        tenant: [
          "im:message:send_as_bot",
          "im:chat:readonly",
          "im:message:readonly",
        ],
        user: [],
      },
    }),
  );
  bind("save-secret", async () => {
    await action(
      "feishu-secret",
      { app_id: $("#app-id").value, secret: $("#app-secret").value },
      "安全保存飞书凭据",
    );
    $("#app-secret").value = "";
    if (connectionReport) connectionReport.feishu_chats = [];
    delete setupDraft["send-enabled"];
    render();
  });
}
function setup() {
  if (!setupStep) setupStep = defaultSetupStep();
  const connected = accountReport?.matches_current === true;
  if (!connected) setupStep = 1;
  const done = [
    connected,
    connected && !!settings.chat_id,
    connectionReport?.dsh?.ready === true,
    connected && !!settings.feishu_chat_id,
    state.services.running,
  ];
  const heading = (title, description) =>
    `<div class="step-heading"><span class="step-label">第 ${setupStep} 步 / 共 5 步</span><h2>${title}</h2><p>${description}</p></div>`;
  let content;
  if (setupStep === 1) {
    content =
      heading(
        "先连接你的微信",
        "识别当前登录账号，为这个账号准备本地读取凭据。",
      ) + accountPanelHTML(false);
    if (!connected) {
      content += `<p class="muted">首次连接会引导你检查环境、授权取钥并恢复官方微信。完成后凭据保存在本机，日常读取无需重复取钥。</p><div class="actions"><button id="existing-credential" class="text-button">已有密钥或备份</button></div>`;
    } else {
      content +=
        `<p class="muted">当前账号已连接。接下来选择一个会话，用新消息验证完整流程。</p>` +
        stepFooter(null, 2, "选择微信会话");
    }
  } else if (setupStep === 2) {
    content =
      heading(
        "选择要关注的会话",
        "一次选择一个微信会话。首次使用建议先用文件传输助手测试。",
      ) +
      `<label>搜索群名或会话<div class="field-action"><input id="chat-query" placeholder="例如：文件传输助手" value="${escape(setupDraft["chat-query"] ?? settings.group_name)}"><button id="find-chats">搜索会话</button></div></label><div id="chats" class="choice-list" role="group" aria-label="微信会话搜索结果"></div><p class="selected-value">已选会话：<strong>${escape(settings.group_name || "尚未选择")}</strong></p>` +
      stepFooter(1, 3, "接入模型", !settings.chat_id);
  } else if (setupStep === 3) {
    content =
      heading(
        "连接你的 DSH 模型",
        "复用 DSH 中已有的模型渠道，并接入 dsh-cost-meter 记录用量。",
      ) +
      `<p class="muted">先打开 DSH 并配置好模型，再点击下方连接。缺少费用插件时会使用 DSH 自带安装器补齐。</p><div class="actions"><button id="auto-connect" class="primary">连接 DSH 与费用插件</button>${button("测试模型与记账", "test-dsh")}</div><div id="onboarding-checks" class="connection-checks" aria-live="polite"></div><p class="note">连接不会发起模型对话。「测试模型」会产生一次真实调用；最后一步也会统一验证。</p><details class="subsection"><summary>手动维护桥接插件</summary><div class="actions">${button("安装 / 更新桥接插件", "install-dsh")}</div></details>` +
      stepFooter(2, 4, "接入飞书");
  } else if (setupStep === 4) {
    const transport =
      setupDraft["feishu-transport"] || settings.feishu_transport;
    content =
      heading(
        "把建议送到飞书",
        "选择你们的内部接收群，客户群中的回复仍由你手动确认。",
      ) +
      `<div class="form"><label>连接方式<select id="feishu-transport"><option value="cli" ${transport === "cli" ? "selected" : ""}>复用本机 Lark CLI</option><option value="openapi" ${transport === "openapi" ? "selected" : ""}>使用自建飞书应用</option></select></label><div id="transport-fields">${transportFields()}</div></div><div class="actions"><button id="find-feishu" class="primary">获取机器人所在群</button></div><div id="feishu-chats" class="choice-list" role="group" aria-label="飞书接收群"></div><p class="selected-value">接收群：<strong>${escape(settings.feishu_chat_name || "尚未选择")}</strong></p><details class="subsection"><summary>单独测试飞书连接</summary><p class="muted">向已选群发送一条连通性测试消息，并核对是否收到。</p><div class="actions">${button("发送测试并回读", "test-feishu")}</div></details>` +
      stepFooter(
        3,
        5,
        "确认并运行",
        !settings.feishu_chat_id || transport !== settings.feishu_transport,
      );
  } else {
    content =
      heading(
        "确认配置，开始运行",
        "最后验证模型、费用记录和飞书投递，然后监听新消息。",
      ) +
      `<div class="row"><span class="muted">微信会话</span><strong>${escape(settings.group_name || "尚未选择")}</strong></div><div class="row"><span class="muted">飞书接收群</span><strong>${escape(settings.feishu_chat_name || "尚未选择")}</strong></div><label class="check"><input id="send-enabled" type="checkbox" ${(setupDraft["send-enabled"] ?? settings.send_enabled) ? "checked" : ""}><span><strong>将回复建议自动发送到飞书</strong><small>关闭后仅保存本地草稿。建议内容请人工确认后回复客户。</small></span></label><details class="subsection"><summary>启动方式</summary><label class="check"><input id="autostart-monitor" type="checkbox" ${(setupDraft["autostart-monitor"] ?? settings.autostart_monitor) ? "checked" : ""}><span>打开 NoteBridge 后恢复监控</span></label><label class="check"><input id="login-start" type="checkbox" ${(setupDraft["login-start"] ?? state.login_installed) ? "checked" : ""}><span>登录 Mac 后在菜单栏启动</span></label></details><p class="note">测试会调用一次模型，并向已选飞书群发送一条测试消息。保持 Mac 微信登录、DSH 开启；开启自动投递前，请在工作台检查已有草稿。</p><div class="step-footer"><button id="step-back" class="text-button">← 上一步</button><button id="finish-setup" class="primary" ${settings.chat_id && settings.feishu_chat_id ? "" : "disabled"}>测试并开始运行</button></div><div class="actions"><button id="save-run" class="text-button">仅保存运行选项</button></div>`;
  }
  $("#content").innerHTML =
    `<div class="setup-layout"><ol class="steps" aria-label="接入进度">${setupStages.map((name, i) => `<li><button class="step ${setupStep === i + 1 ? "current" : done[i] ? "done" : ""}" data-step="${i + 1}" ${setupStep === i + 1 ? 'aria-current="step"' : ""} ${!connected && i > 0 ? "disabled" : ""}><span class="step-number">${done[i] && setupStep !== i + 1 ? "✓" : i + 1}</span><span>${name}</span></button></li>`).join("")}</ol>${setupStep > 1 ? `<div class="account-strip"><span>当前账号 · ${escape(accountReport.current?.label)}</span><button id="change-account" class="text-button">管理账号</button></div>` : ""}<section class="card setup-task">${content}</section><p class="setup-help">遇到问题？<a href="https://github.com/CalWade/wx-watcher/blob/main/docs/FIRST_RUN.md" target="_blank" rel="noreferrer">查看接入指南 ↗</a></p></div>`;
  bindAccountPanel();
  document
    .querySelectorAll("[data-step]")
    .forEach((b) => (b.onclick = () => setSetupStep(Number(b.dataset.step))));
  bind("step-back", () => setSetupStep(setupStep - 1));
  bind("step-next", () => setSetupStep(setupStep + 1));
  bind("change-account", () => setSetupStep(1));
  bind("existing-credential", () => {
    pendingAccount = accountReport?.current || null;
    navigate("keytool");
    $("#existing-options").open = true;
    $("#existing-options").scrollIntoView({ block: "start" });
  });
  bind("auto-connect", async () => {
    const result = await action("connect-dsh", {}, "连接 DSH 与费用插件");
    connectionReport = {
      ...connectionReport,
      dsh: result,
      checks: result.checks || [],
    };
    render();
  });
  if (connectionReport) {
    if ($("#onboarding-checks"))
      $("#onboarding-checks").innerHTML = (connectionReport.checks || [])
        .map(
          (c) =>
            `<p class="${c.ok ? "success" : "error"}">${c.ok ? "✓" : "○"} ${escape(c.stage)}${c.message ? "：" + escape(c.message) : ""}</p>`,
        )
        .join("");
    renderConversationChoices(connectionReport.conversations || []);
    renderFeishuChoices(connectionReport.feishu_chats || []);
  }
  bind("find-chats", async () => {
    setupDraft["chat-query"] = $("#chat-query").value;
    const rows = await action(
      "conversations",
      { query: $("#chat-query").value },
      "搜索本地会话",
    );
    connectionReport = { ...connectionReport, conversations: rows };
    renderConversationChoices(rows, true);
  });
  $("#chat-query")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#find-chats").click();
  });
  $("#feishu-transport")?.addEventListener("change", () => {
    setupDraft["feishu-transport"] = $("#feishu-transport").value;
    $("#transport-fields").innerHTML = transportFields();
    bindTransportFields();
    // Results belong to the previously saved transport, not this unsaved choice.
    if (connectionReport) connectionReport.feishu_chats = [];
    $("#feishu-chats").replaceChildren();
    $("#step-next").disabled =
      setupDraft["feishu-transport"] !== settings.feishu_transport ||
      !settings.feishu_chat_id;
    $(".selected-value").textContent =
      setupDraft["feishu-transport"] !== settings.feishu_transport
        ? "连接方式已改变，请重新获取并选择接收群。"
        : "接收群：" + (settings.feishu_chat_name || "尚未选择");
  });
  bindTransportFields();
  bind("find-feishu", async () => {
    const transport = $("#feishu-transport").value;
    if (transport !== settings.feishu_transport) {
      await action(
        "settings",
        { feishu_transport: transport },
        "保存飞书连接方式",
      );
    }
    const result = await action("feishu-chats", {}, "读取机器人所在群");
    const rows = result.data?.chats || [];
    connectionReport = { ...connectionReport, feishu_chats: rows };
    delete setupDraft["send-enabled"];
    render();
    renderFeishuChoices(rows, true);
  });
  bind("save-run", () => action("run-settings", runOptions(), "保存运行选项"));
  bind("finish-setup", async () => {
    await action("finish-setup", runOptions(), "验证模型、费用与飞书并启动");
    setupDraft = {};
    navigate("overview");
  });
}
function captureTimeline(phase) {
  const stages = [
    ["环境检查", ["idle"], "先确认版本、权限、调试工具和账号资料"],
    [
      "系统授权与备份",
      [
        "authorization",
        "permissions",
        "backup",
        "preparing",
        "signing",
        "opening_client",
      ],
      "系统窗口授权；其余由工具完成",
    ],
    [
      "自动准备监听",
      [
        "preparing_listener",
        "awaiting_login_page",
        "attaching",
        "attached",
        "armed",
        "reattaching",
      ],
      "无需操作；监听装好后微信会自动打开",
    ],
    [
      "现在登录同一个账号",
      ["ready_for_login", "waiting"],
      "微信打开后，扫码或点击登录并完成手机确认",
    ],
    [
      "验证、恢复与保存",
      ["verifying", "account_changed", "restoring", "captured", "completed"],
      "成功必须通过数据库验证、恢复官方客户端并保存 Keychain",
    ],
  ];
  const current = stages.findIndex(([, phases]) =>
    phases.includes(phase || "idle"),
  );
  return stages
    .map(
      ([title, phases, hint], index) =>
        `<li class="${index === current ? "current" : index < current ? "done" : ""}"><strong>${escape(title)}</strong><span>${escape(hint)}</span></li>`,
    )
    .join("");
}
function keytool() {
  const profile = settings.profiles.find(
    (p) => p.id === settings.active_profile,
  );
  const report = captureReadiness;
  const target =
    pendingAccount ||
    (!accountReport?.matches_current ? accountReport?.current : null);
  const root =
    report?.db_root ||
    target?.db_root ||
    (accountReport?.matches_current || captureProgress?.phase === "completed"
      ? profile?.db_root
      : "") ||
    "";
  const targetIsSaved = profile && root === profile.db_root;
  const active = !!activeCaptureId;
  const completed = targetIsSaved && captureProgress?.phase === "completed";
  const phase = captureProgress?.phase || "idle";
  const alreadyConnected = targetIsSaved && !requestRecapture;
  const checks = report?.checks || [];
  const checksHTML = checks
    .map(
      (c) =>
        `<div class="readiness ${c.ok ? "passed" : "blocked"}"><span class="check-mark" aria-hidden="true">${c.ok ? "✓" : "!"}</span><div><strong>${escape(c.title)}</strong><p>${escape(c.message)}</p>${!c.ok && c.action === "xcode" ? '<a href="https://developer.apple.com/xcode/resources/" target="_blank" rel="noreferrer">获取 Apple 开发工具 ↗</a>' : ""}${!c.ok && c.action === "wechat" ? '<a href="https://mac.weixin.qq.com/" target="_blank" rel="noreferrer">官方微信网站 ↗</a>' : ""}${!c.ok && c.action === "privacy" ? '<button id="check-open-privacy" class="text-button">打开权限设置</button>' : ""}</div></div>`,
    )
    .join("");
  $("#content").innerHTML =
    `<div class="account-strip"><button id="back-to-setup" class="text-button">← 返回接入设置</button><span id="selected-account">本次取钥目标：${escape(report?.account_label || target?.label || profile?.label || "检查时自动识别")}</span></div><div class="capture-layout"><ol id="capture-timeline" class="journey" aria-label="微信连接进度">${captureTimeline(phase)}</ol><div class="capture-main">
    <section class="card" id="capture-task">
      <div class="step-heading"><h2>${active ? "按提示完成微信登录" : completed || alreadyConnected ? "微信连接已完成" : report?.ready ? "检查通过，可以连接" : "首次连接微信 · 取钥向导"}</h2><p>${active ? "请留在这个向导，下一步操作会在这里明确提示。" : completed || alreadyConnected ? "凭据已安全保存，可以继续选择会话。" : report?.ready ? "准备好暂时中断微信使用后，再开始取钥。" : "先检查这台 Mac，确认账号、读取权限与兼容条件。"}</p></div>
      <div id="capture-preparation" ${active || completed || alreadyConnected ? "hidden" : ""}>
        ${report ? "" : '<p class="muted">请先正常登录 Mac 微信，并向文件传输助手发送一条消息，让资料保存到本机。检查只读取环境信息。</p>'}
        <div id="capture-checks" aria-live="polite">${report?.ready ? `<details><summary>✓ ${checks.length} 项检查全部通过 · 查看详情</summary>${checksHTML}</details>` : checksHTML}</div>
        <div class="actions"><button id="capture-check" class="${report?.ready ? "" : "primary"}">${report ? "重新检查" : "检查并识别微信账号"}</button><button id="capture-start" class="primary" ${report?.ready ? "" : "disabled"} ${!report?.ready ? "hidden" : ""}>授权并开始取钥</button></div>
        ${report?.ready ? '<p class="note">这一步会备份微信、临时重签并连接调试器，结束后恢复官方客户端。需要系统授权；首次取钥无法保证零风控风险。</p>' : ""}
      </div>
      <div id="capture-live" ${active || (captureProgress && !completed) ? "" : "hidden"}><p id="capture-progress" class="note" role="status">${escape(captureProgress?.message || "正在准备，请稍候…")}</p><div class="actions"><button id="capture-login-ready" class="primary" hidden>我已停在登录页，准备监听</button><button id="capture-cancel" class="text-button" disabled>取消并恢复</button></div></div>
      ${targetIsSaved ? `<div id="credential-result" class="${completed ? "" : "subsection"}"><div class="row"><strong>${escape(profile.label)}</strong><span class="pill ok">Keychain 已保存</span></div><p class="muted">${escape(profile.verification?.verified || 0)} / ${escape(profile.verification?.total || 0)} 份数据库已验证。请在恢复后的官方微信中正常登录，再继续。</p><div class="actions"><button id="continue-setup" class="primary">下一步：选择会话</button>${button("验证当前凭据", "verify-key")}</div>${!active && !completed && !requestRecapture ? '<div class="subsection"><button id="recapture" class="text-button">需要重新获取凭据？</button></div>' : ""}</div>` : ""}
    </section>
    <details class="card" id="permission-options"><summary>权限帮助</summary><p class="muted">在系统设置中允许 NoteBridge 读取微信资料。列表中没有应用时，将权限帮助窗口中的图标拖入列表，开启后点击「重新连接」。</p><div class="actions"><button id="open-privacy">完整磁盘访问设置</button><button id="open-app-management">App 管理设置</button></div></details>
  </div></div>
  <details id="existing-options" class="card"><summary>已有密钥或备份 · 手动导入</summary><p class="muted">首次连接无需填写路径。已有凭据可在这里导入；不要将密钥粘贴到聊天或日志。</p><div class="form"><label>数据库目录<div class="field-action"><input id="db-root" value="${escape(root)}" placeholder="自动识别或选择 db_storage 目录"><button id="pick-root">选择目录</button></div></label><div><button id="find-accounts">识别本机账号</button><div id="account-choices" class="choice-list"></div></div><label>密钥文件<div class="field-action"><input id="key-file" placeholder="选择现有密钥文件"><button id="pick-key">选择文件</button></div></label>${field("账号备注", "profile-label", report?.account_label || target?.label || profile?.label || "")}</div><div class="actions"><button id="import-key" class="primary">验证并保存凭据</button></div><div class="subsection"><h3>加密备份与恢复</h3><p class="muted">请单独保管备份口令，工具不会保存它。</p>${field("备份 / 恢复口令 · 至少 12 个字符", "backup-password", "", "password")}<div class="actions"><button id="backup-key" ${targetIsSaved ? "" : "disabled"}>下载当前凭据备份</button><button id="restore-key">导入加密备份</button><input id="backup-file" type="file" accept=".json" hidden></div></div></details>
  <details class="card" id="capture-history"><summary>历史取钥任务与恢复</summary><p class="muted">中断的任务可在这里继续。如恢复未完成，请先恢复官方客户端。</p><button id="refresh-captures">刷新任务</button><div id="capture-sessions"></div></details>`;
  bind("recapture", () => {
    requestRecapture = true;
    captureReadiness = null;
    captureProgress = null;
    render();
  });
  bind("back-to-setup", () => {
    setupStep = 1;
    navigate("setup");
  });
  bind("check-open-privacy", openPrivacy);
  bind("open-privacy", openPrivacy);
  bind("open-app-management", () => {
    if (window.webkit?.messageHandlers?.native)
      window.webkit.messageHandlers.native.postMessage({
        kind: "open-app-management",
        id: crypto.randomUUID(),
      });
    else
      notice(
        "请打开系统设置 → 隐私与安全性 → App 管理，允许实际运行取钥工具的应用。",
      );
  });
  bind("find-accounts", async () => {
    const rows = await action("accounts", {}, "识别本机账号");
    $("#account-choices").replaceChildren(
      ...rows.map((r) => {
        const b = document.createElement("button");
        b.textContent = r.label + (r.active ? " · 当前登录" : " · 历史账号");
        b.onclick = () => {
          $("#db-root").value = r.db_root;
          $("#profile-label").value = r.label;
          captureReadiness = null;
          $("#capture-start").disabled = true;
        };
        return b;
      }),
    );
    if (!rows.length)
      notice(
        "未找到可读取的微信数据，请先正常登录 Mac 微信并检查数据访问权限。",
      );
  });
  bind("refresh-captures", loadCaptureSessions);
  loadCaptureSessions().catch((e) => notice(e.message, true));
  $("#db-root").addEventListener("input", () => {
    captureReadiness = null;
    $("#capture-start").disabled = true;
  });
  bind("pick-root", async () => {
    $("#db-root").value = await nativePick("choose-directory");
    captureReadiness = null;
    $("#capture-start").disabled = true;
  });
  bind("pick-key", async () => {
    $("#key-file").value = await nativePick("choose-file");
  });
  bind("import-key", async () => {
    await action(
      "import-key",
      {
        db_root: $("#db-root").value,
        key_file: $("#key-file").value,
        label: $("#profile-label").value,
      },
      "验证并保存微信凭据",
    );
    pendingAccount = null;
    captureReadiness = null;
    render();
  });
  bind("continue-setup", () => {
    setupStep = 2;
    navigate("setup");
  });
  bind("capture-check", async () => {
    captureReadiness = await action(
      "connection-check",
      { db_root: $("#db-root").value },
      "检查首次连接条件",
    );
    $("#job-dialog").close();
    render();
  });
  bind("capture-start", async () => {
    if (
      !(await confirmAction(
        "本次取钥会退出并重新打开微信、临时重签和连接调试器；工具会备份并在结束时恢复官方客户端。系统将要求管理员授权。开始后请按向导提示操作登录。是否开始？",
      ))
    )
      return;
    const result = await action(
      "capture-start",
      { db_root: $("#db-root").value },
      "准备取钥会话",
    );
    $("#job-dialog").close();
    captureProgress = result;
    activeCaptureId = result.capture_id;
    render();
  });
  bind("backup-key", async () => {
    const result = await action(
      "backup-key",
      { password: $("#backup-password").value },
      "创建加密备份",
    );
    $("#backup-password").value = "";
    download("notebridge-access-backup.json", result.backup);
  });
  bind("restore-key", () => $("#backup-file").click());
  $("#backup-file").addEventListener("change", async (e) => {
    try {
      const file = e.target.files[0];
      if (!file || file.size > 10000) throw new Error("备份文件无效");
      const backup = JSON.parse(await file.text());
      await action(
        "restore-key",
        {
          backup,
          password: $("#backup-password").value,
          db_root: $("#db-root").value,
          label: $("#profile-label").value,
        },
        "恢复并验证凭据",
      );
      pendingAccount = null;
      captureReadiness = null;
      render();
    } catch (error) {
      notice(error.message, true);
    }
  });
}
async function loadCaptureSessions() {
  const rows = await quietAction("capture-sessions");
  const box = $("#capture-sessions");
  if (!box) return;
  box.replaceChildren(
    ...rows.reverse().map((r) => {
      const row = document.createElement("div");
      row.className = "record";
      const text = document.createElement("p");
      text.textContent = r.message || r.phase;
      row.append(text);
      if (r.error === "account_changed" && r.observed_db_roots?.length === 1) {
        const useAccount = document.createElement("button");
        useAccount.textContent = "使用本次识别到的账号目录";
        useAccount.onclick = () => {
          $("#db-root").value = r.observed_db_roots[0];
          captureReadiness = null;
          $("#capture-start").disabled = true;
          notice("已选用本次微信实际打开的账号目录，可以重新检查取钥条件。");
        };
        row.append(useAccount);
      }
      const terminal = [
        "completed",
        "failed",
        "cancelled",
        "recovered",
      ].includes(r.phase);
      if (!terminal) {
        const watch = document.createElement("button");
        watch.textContent = "继续查看进度 / 保存凭据";
        watch.onclick = () => watchCapture(r.capture_id);
        row.append(watch);
        if (r.phase === "recovery_required") {
          const recover = document.createElement("button");
          recover.textContent = "恢复官方微信";
          recover.onclick = async () => {
            try {
              await action(
                "capture-recover",
                { capture_id: r.capture_id },
                "请求系统授权恢复客户端",
              );
              $("#job-dialog").close();
              watchCapture(r.capture_id);
            } catch (e) {
              notice(e.message, true);
            }
          };
          row.append(recover);
        }
      }
      return row;
    }),
  );
  if (
    rows.some(
      (r) =>
        !["completed", "failed", "cancelled", "recovered"].includes(r.phase),
    )
  ) {
    $("#capture-history").open = true;
    if ($("#capture-start")) $("#capture-start").disabled = true;
  }
  if (!rows.length)
    box.textContent = "没有首次取钥任务。已有密钥导入无需启动取钥。";
}
function watchCapture(id) {
  clearInterval(timer);
  activeCaptureId = id;
  const loginReady = $("#capture-login-ready");
  if (!loginReady) return;
  $("#capture-preparation").hidden = true;
  $("#capture-live").hidden = false;
  loginReady.hidden = true;
  loginReady.onclick = async () => {
    if (activeOperation) return;
    loginReady.disabled = true;
    try {
      await action(
        "capture-login-ready",
        { capture_id: id },
        "确认已停在微信登录页",
      );
      loginReady.hidden = true;
    } catch (error) {
      notice(error.message, true);
    } finally {
      loginReady.disabled = false;
    }
  };
  $("#capture-start").disabled = true;
  $("#capture-check").disabled = true;
  $("#capture-cancel").disabled = false;
  $("#capture-cancel").onclick = () =>
    action("capture-cancel", { capture_id: id }, "请求取消并恢复").catch((e) =>
      notice(e.message, true),
    );
  let busy = false;
  const poll = async () => {
    if (busy || page !== "keytool" || activeCaptureId !== id) return;
    busy = true;
    try {
      const progress = await quietAction("capture-status", { capture_id: id });
      if (page !== "keytool" || activeCaptureId !== id) return;
      captureProgress = progress;
      $("#capture-task h2").textContent =
        {
          awaiting_login_page: "先停在微信登录页",
          ready_for_login: "现在可以登录微信",
          waiting: "等待登录与密钥验证",
          preparing_listener: "正在准备监听，无需操作",
          attaching: "正在准备监听，无需操作",
          attached: "正在准备监听，无需操作",
          armed: "监听已安装，正在打开微信",
          restoring: "正在恢复官方微信",
          verifying: "正在验证凭据",
          captured: "正在安全保存凭据",
        }[progress.phase] || "正在处理，请按提示操作";
      $("#capture-progress").textContent = progress.message || progress.phase;
      $("#capture-timeline").innerHTML = captureTimeline(progress.phase);
      loginReady.hidden = progress.phase !== "awaiting_login_page";
      if (progress.phase === "ready_for_login")
        notice("取钥监听已就绪，现在可以点击微信登录并完成手机确认。");
      if (
        [
          "completed",
          "failed",
          "cancelled",
          "recovered",
          "recovery_required",
        ].includes(progress.phase)
      ) {
        clearInterval(timer);
        activeCaptureId = null;
        captureReadiness = null;
        if (progress.phase === "completed") pendingAccount = null;
        await refresh();
        if (page !== "keytool") return;
        render();
        if (progress.phase === "completed") {
          notice(
            `连接完成：${progress.verified} / ${progress.total} 份数据库验证通过，官方微信已恢复，密钥已保存。请正常登录后继续选择会话。`,
          );
        } else if (progress.phase === "recovery_required") {
          $("#capture-history").open = true;
          notice("需要先恢复官方微信，请使用下方任务中的恢复按钮。", true);
        } else
          notice(
            progress.message || "本次取钥已结束。可重新检查后再试。",
            progress.phase === "failed",
          );
      }
    } catch (e) {
      notice(e.message, true);
    } finally {
      busy = false;
    }
  };
  poll();
  timer = setInterval(poll, 1200);
}
function records() {
  const names = {
    sent: "已投递",
    draft: "本地草稿",
    queued: "等待生成",
    inference: "生成中",
    needs_review: "模型结果需检查",
    delivery_review: "投递结果待核对",
    budget_review: "达到调用上限",
    skipped: "已跳过",
    dismissed: "已忽略",
    resolved: "已重新安排",
    sending: "正在投递",
  };
  $("#content").innerHTML =
    `<div class="account-strip"><button id="back-to-overview" class="text-button">← 返回工作台</button></div><p class="muted">查看原问题、建议和待确认事项。投递结果不明确的任务请先核对飞书，避免重复发送。</p>` +
    (state.records.length
      ? state.records
          .map(
            (r, i) =>
              `<article class="record"><div class="row"><strong>${escape(r.group)}</strong><span class="pill ${r.status === "sent" ? "ok" : "warn"}">${escape(names[r.status] || r.status)}</span></div><p class="original">${escape(r.event?.content || r.event?.text || "")}</p>${r.proposal ? `<blockquote>${escape(r.proposal.suggested_reply || "无需回复")}</blockquote><p>${escape(r.proposal.reason)}</p><ul>${r.proposal.needs_confirmation.map((v) => `<li>${escape(v)}</li>`).join("")}</ul>` : ""}${["draft", "needs_review", "delivery_review", "budget_review"].includes(r.status) ? `<div class="actions"><button data-task="${escape(r.id)}" data-decision="retry">${r.status === "delivery_review" ? "确认未投递，重新安排" : "重新生成建议"}</button>${r.status === "delivery_review" ? `<button data-task="${escape(r.id)}" data-decision="mark-delivered">确认已投递</button>` : ""}<button data-task="${escape(r.id)}" data-decision="dismiss">忽略此任务</button></div>` : ""}${r.error ? `<p class="error">${escape(r.error)}</p>` : ""}<small>${escape(r.created_at)} · ${r.knowledge_sources?.map((s) => escape(s.name)).join("、") || "未附加知识文件"}</small></article>`,
          )
          .join("")
      : '<div class="empty">暂无任务。完成配置并开启监控后，新消息会出现在这里。</div>');
  bind("back-to-overview", () => navigate("overview"));
  document.querySelectorAll("[data-task]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        if (state.services.running)
          throw new Error("请先在概览暂停监控，再处理任务。");
        const decision = b.dataset.decision;
        const task = state.records.find((r) => r.id === b.dataset.task);
        const delivery = task.status === "delivery_review";
        const warning =
          decision === "mark-delivered"
            ? "已在飞书确认这条建议投递成功？"
            : delivery && decision === "retry"
              ? "请先检查飞书群，确认这条建议尚未投递。重新开始监控后将再次尝试发送。"
              : decision === "retry"
                ? "重新开始监控后会发起一次新的模型调用，并按发送开关安排投递。继续？"
                : "忽略这条任务？";
        if (!(await confirmAction(warning))) return;
        await action(
          "task-review",
          {
            id: task.id,
            decision,
            confirmed: true,
            confirmed_not_delivered: delivery,
          },
          "处理回复任务",
        );
        render();
      } catch (e) {
        notice(e.message, true);
      }
    }),
  );
}
function knowledge() {
  $("#content").innerHTML =
    card(
      "业务事实与回复规则",
      `<p class="muted">只提供可以用于客户回复的资料。系统按问题在本地选取相关段落，必要上下文会发送到配置的模型渠道。</p><div class="form"><label>业务说明<textarea id="business-context">${escape(settings.business_context)}</textarea></label><label>回复风格与承诺边界<textarea id="reply-rules">${escape(settings.reply_rules)}</textarea></label><label>Markdown / TXT 资料（每行一个绝对路径）<textarea id="knowledge-files">${escape(settings.knowledge_files.join("\n"))}</textarea></label><div class="actions"><button id="add-knowledge">选择资料文件</button><button id="save-knowledge" class="primary">保存资料与规则</button></div></div>`,
    ) +
    card(
      "处理节奏与用量上限",
      `<div class="two">${field("连续消息合并等待（秒，0–30）", "debounce", settings.debounce_seconds, "number")}${field("每日最多模型调用（UTC 日期，0 表示不限制）", "max-calls", settings.max_daily_calls, "number")}${field("本地事件与已完成任务保留天数（0 表示不清理）", "retention-days", settings.retention_days, "number")}</div><p class="note">调用次数上限用于控制本系统任务数量，不等同于模型供应商的金额账单上限。</p><button id="save-budget">保存处理设置</button>`,
    );
  bind("add-knowledge", async () => {
    const p = await nativePick("choose-file");
    if (p)
      $("#knowledge-files").value +=
        ($("#knowledge-files").value ? "\n" : "") + p;
  });
  bind("save-knowledge", () =>
    action(
      "settings",
      {
        business_context: $("#business-context").value,
        reply_rules: $("#reply-rules").value,
        knowledge_files: $("#knowledge-files")
          .value.split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      },
      "保存业务资料",
    ),
  );
  bind("save-budget", () =>
    action(
      "settings",
      {
        debounce_seconds: Number($("#debounce").value),
        max_daily_calls: Number($("#max-calls").value),
        retention_days: Number($("#retention-days").value),
      },
      "保存用量设置",
    ),
  );
}
function diagnostics() {
  $("#content").innerHTML =
    card(
      "连接与运行检查",
      `<p class="muted">检查当前环境与连接状态。导出诊断不包含密钥和聊天正文；本地路径和渠道名称仍可能属于私有信息，分享前请检查。</p><div class="actions">${button("检查运行环境", "doctor")}<button id="export-diagnostics">导出诊断</button></div><pre>${escape(JSON.stringify(state.services, null, 2))}</pre>`,
    ) +
    card(
      "登录启动与历史恢复",
      `<p class="muted">登录启动在下次登录 Mac 时生效。事件游标超过保留期时，先暂停监控，核对历史缺口再修复；未完成任务会保留。</p><div class="actions"><button id="enable-login">开启登录启动</button><button id="disable-login">关闭登录启动</button><button id="reset-cursor">确认缺口并修复游标</button></div>`,
    ) +
    card(
      "使用文档",
      `<p><a href="https://github.com/CalWade/wx-watcher/tree/main/docs" target="_blank" rel="noreferrer">安装、取钥、DSH、飞书与故障恢复 ↗</a></p><p>关闭主窗口可继续后台运行；菜单栏可以重新打开。退出应用会停止本应用启动的后台服务。</p>`,
    );
  bind("enable-login", () =>
    action("login-service", { action: "install" }, "设置登录启动"),
  );
  bind("disable-login", () =>
    action("login-service", { action: "uninstall" }, "关闭登录启动"),
  );
  bind("reset-cursor", async () => {
    if (
      await confirmAction(
        "确认接受超过保留期的未消费事件无法自动补回？请先暂停监控。系统会记录这次缺口并从当前最新游标继续。",
      )
    )
      await action("reset-cursor", { confirmed: true }, "修复过期游标");
  });
  bind("export-diagnostics", async () =>
    download(
      "notebridge-diagnostics.json",
      await action("diagnostics", {}, "收集诊断"),
    ),
  );
}
function download(name, value) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function navigate(next) {
  if (!initialized) {
    page = labels[next] ? next : "setup";
    return;
  }
  clearInterval(timer);
  if (page === "setup") rememberSetupDraft();
  page = labels[next] ? next : "overview";
  history.replaceState(null, "", "#" + page);
  $("#notice").textContent = "";
  $("#notice").className = "";
  render();
  window.scrollTo(0, 0);
}
document.querySelectorAll("[data-page]").forEach((b) => (b.disabled = true));
$(".brand").addEventListener("click", (e) => {
  e.preventDefault();
  navigate("overview");
});
$("#job-dialog").addEventListener("cancel", (e) => {
  if (activeOperation) e.preventDefault();
});
$("#job-close").addEventListener("click", () => $("#job-dialog").close());
document
  .querySelectorAll("[data-page]")
  .forEach((b) => b.addEventListener("click", () => navigate(b.dataset.page)));
(async () => {
  try {
    const params = new URLSearchParams(location.hash.slice(1));
    const token = params.get("token");
    page = params.get("page") || location.hash.slice(1) || "setup";
    if (!labels[page]) page = "setup";
    if (token) await api("auth", { token });
    history.replaceState(null, "", "#" + page);
    await refresh();
    initialized = true;
    document
      .querySelectorAll("[data-page]")
      .forEach((b) => (b.disabled = false));
    navigate(page);
    setInterval(async () => {
      try {
        await refresh();
        const passive = JSON.stringify([page, state, accountReport, settings]);
        if (
          ["overview", "records"].includes(page) &&
          passive !== lastPassiveView &&
          !activeOperation &&
          !$("#confirm-dialog").open &&
          !$("#job-dialog").open
        ) {
          const focused = document.activeElement;
          if (!$("#content").contains(focused)) {
            render();
            lastPassiveView = passive;
          }
        }
      } catch (_) {
        $("#health").textContent = "○ 本地后台已断开";
        $("#health").className = "pill warn";
        notice("本地后台连接已断开，请重新打开应用。", true);
      }
    }, 5000);
  } catch (error) {
    $("#content").textContent = error.message;
    notice("请从 Mac 应用或本机受保护的启动入口打开。", true);
  }
})();

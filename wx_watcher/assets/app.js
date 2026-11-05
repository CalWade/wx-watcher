"use strict";
const $ = (s) => document.querySelector(s);
const labels = {
  overview: "运行概览",
  setup: "连接与配置",
  keytool: "微信凭据",
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
async function action(op, data = {}, title = "正在处理") {
  const dialog = $("#job-dialog");
  $("#job-title").textContent = title;
  $("#job-result").textContent = "";
  $("#job-details").open = false;
  $("#job-summary").textContent = "";
  $("#job-progress").textContent = "正在执行，请稍候。";
  $("#job-close").disabled = true;
  if (!dialog.open) dialog.showModal();
  try {
    const { job_id } = await api("action/" + op, data);
    let job;
    do {
      await new Promise((r) => setTimeout(r, 350));
      job = await api("job?id=" + job_id);
    } while (job.status === "running");
    if (job.status !== "completed") throw new Error(job.error || "操作未完成");
    $("#job-progress").textContent = "已完成";
    $("#job-summary").textContent = describeResult(op, job.result);
    $("#job-result").textContent = JSON.stringify(job.result, null, 2);
    return job.result;
  } catch (error) {
    $("#job-progress").textContent = "需要处理";
    $("#job-summary").textContent = error.message;
    throw error;
  } finally {
    $("#job-close").disabled = false;
    await refresh();
  }
}
function describeResult(op, value) {
  if (op === "doctor" && value.wechat_data_access?.readable === false)
    return value.wechat_data_access.hint;
  if (value.message || value.hint) return value.message || value.hint;
  if (op === "auto-connect")
    return value.checks.every((c) => c.ok)
      ? "本机连接已就绪。请选择微信会话和飞书接收群，再测试全链路。"
      : "已自动完成可用的连接，请关闭此窗口处理页面中标出的事项。";
  if (op === "capture-preflight")
    return value.ready
      ? "环境检查通过。开始后请停在微信登录页，等待监听明确就绪再点击登录。"
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
    return `已找到 ${value.length} 项，关闭此窗口后选择。`;
  if (op === "feishu-chats")
    return `已找到 ${value.data?.chats?.length || 0} 个机器人可访问的群。`;
  if (op === "login-service")
    return value.installed
      ? "已设置登录 Mac 后启动本地服务，下次登录生效。"
      : "已关闭登录启动。";
  if (value.running === true) return "读取与回复服务已启动。";
  if (value.running === false) return "本应用启动的服务已停止。";
  if (value.saved) return "设置已保存。";
  return "操作已完成，可以展开查看详细结果。";
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
  if (element)
    element.addEventListener("click", () =>
      Promise.resolve()
        .then(fn)
        .catch((e) => notice(e.message, true)),
    );
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
  settings = await api("settings");
  state = await api("status");
  try {
    const previous = accountReport?.current?.db_root;
    accountReport = await quietAction("account-status");
    if (previous !== accountReport.current?.db_root) captureReadiness = null;
  } catch (error) {
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
      : "○ 尚未监控";
  $("#health").className = "pill " + (problem ? "warn" : running ? "ok" : "");
}
function accountPanelHTML() {
  const r = accountReport;
  const rows = r?.accounts || [];
  return `<section id="account-panel" class="card"><h2>微信账号</h2><div class="row"><span>微信当前登录</span><strong id="wechat-current-account">${escape(r?.current?.label || "尚未确认，请登录微信后刷新")}</strong></div><div class="row"><span>本项目正在使用</span><strong id="project-selected-account">${escape(r?.selected?.label || "尚未连接账号")}</strong></div><p id="account-message" class="${r?.matches_current ? "success" : "note"}">${escape(r?.message || "正在识别账号…")}</p><div class="actions"><button id="refresh-accounts">刷新账号状态</button>${r?.current ? `<button id="use-current-account" class="primary">${r.current.credential_saved ? (r.matches_current ? "验证当前账号凭据" : "验证并切换到当前登录账号") : "连接当前登录账号（尚无密钥）"}</button>` : ""}</div><details id="account-list"><summary>查看本机账号与已保存凭据（${rows.length}）</summary>${rows.map((a, i) => `<div class="row"><div><strong>${escape(a.label)}</strong><p class="muted">${a.active ? "微信当前登录 · " : "历史账号资料 · "}${a.credential_saved ? "密钥已保存" : "尚未连接凭据"}${a.selected ? " · 本项目正在使用" : ""}</p></div><button data-account-index="${i}" ${a.active ? "" : "disabled"}>${a.credential_saved ? "验证并使用" : "连接此账号"}</button></div>`).join("")}<p class="muted">切换微信登录账号后请刷新。新账号需要自己的密钥；历史账号需先在微信正常登录，再切换本项目。不会自动更改微信的登录账号。</p></details></section>`;
}
function updateAccountActions() {
  const matches = accountReport?.matches_current === true;
  if ($("#find-chats")) $("#find-chats").disabled = !matches;
  if ($("#finish-setup"))
    $("#finish-setup").disabled =
      !matches || !settings.chat_id || !settings.feishu_chat_id;
  document
    .querySelectorAll('[data-op="start"]')
    .forEach((b) => (b.disabled = !matches));
}
function paintAccountPanel() {
  if (!$("#account-panel")) return;
  $("#account-panel").outerHTML = accountPanelHTML();
  bindAccountPanel();
  updateAccountActions();
}
function bindAccountPanel() {
  bind("refresh-accounts", async () => {
    accountReport = await action(
      "account-status",
      {},
      "识别当前微信与已保存账号",
    );
    $("#job-dialog").close();
    paintAccountPanel();
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
      !window.confirm(
        "切换或连接账号前需要暂停当前监控。是否暂停？已有凭据与任务记录会保留。",
      )
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
    connectionReport = null;
    captureReadiness = null;
    navigate("setup");
    notice(selected.message || "当前账号凭据验证通过，可以继续使用当前配置。");
    return;
  }
  pendingAccount = account;
  captureReadiness = null;
  navigate("keytool");
  notice(
    "已选择当前登录账号作为取钥目标。请先检查，再按提示授权；旧账号凭据会保留。",
  );
}
function render() {
  $("#title").textContent = labels[page];
  document
    .querySelectorAll("nav button")
    .forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  if (page === "overview") overview();
  if (page === "setup") setup();
  if (page === "keytool") keytool();
  if (page === "records") records();
  if (page === "knowledge") knowledge();
  if (page === "diagnostics") diagnostics();
  if (["setup", "keytool", "overview"].includes(page)) {
    $("#content").insertAdjacentHTML("afterbegin", accountPanelHTML());
    bindAccountPanel();
    updateAccountActions();
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
  $("#content").innerHTML =
    `${state.services.last_error || state.services.worker?.last_error ? `<p class="error">${escape(state.services.last_error || state.services.worker.last_error)}</p>` : ""}<section class="hero"><div><h2>${state.services.running ? "消息助手已就绪" : "把客户问题，变成可确认的回复建议"}</h2><p>读取选定微信会话，结合业务资料生成建议，送达内部飞书群。你负责最终判断与回复。</p></div><div>${button(state.services.running ? "暂停监控" : "开始监控", state.services.running ? "stop" : "start", true)}</div></section><div class="grid">${card("已投递建议", `<div class="metric">${sent}</div><small>最近 100 条任务中的成功投递</small>`)}${card("待处理事项", `<div class="metric">${review}</div><small>模型、预算或投递结果需核对</small>`)}${card("模型费用估算", `<div class="metric">$${cost.toFixed(4)}</div><small>来自 dsh-cost-meter，非供应商实际账单</small>`)}</div>${card("当前工作流", `<div class="row"><span>微信会话</span><strong>${escape(settings.group_name || "尚未选择")}</strong></div><div class="row"><span>飞书接收群</span><strong>${escape(settings.feishu_chat_name || "尚未连接")}</strong></div><div class="row"><span>投递模式</span><strong>${settings.send_enabled ? "自动投递内部飞书群" : "仅保存本地草稿"}</strong></div><div class="row"><span>微信读取</span><span>${escape(state.services.reader?.state || "stopped")}</span></div><div class="actions"><button id="open-setup">继续配置</button><button id="open-records">查看建议</button></div>`)}`;
  bind("open-setup", () => navigate("setup"));
  bind("open-records", () => navigate("records"));
}
function renderConversationChoices(rows) {
  const target = $("#chats");
  if (!target) return;
  target.replaceChildren(
    ...rows.map((r) => {
      const b = document.createElement("button");
      b.textContent = r.name;
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
}
function renderFeishuChoices(rows) {
  const target = $("#feishu-chats");
  if (!target) return;
  target.replaceChildren(
    ...rows.map((r) => {
      const b = document.createElement("button");
      b.textContent = r.name;
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
}
function runOptions() {
  return {
    send_enabled: $("#send-enabled").checked,
    autostart_monitor: $("#autostart-monitor").checked,
    login_enabled: $("#login-start").checked,
  };
}
function setup() {
  const hasSavedAccounts = settings.profiles.length > 0;
  const connected = hasSavedAccounts && accountReport?.matches_current;
  if (!hasSavedAccounts) {
    $("#content").innerHTML =
      `<section class="hero"><div><h2>先连接微信，再开启消息助手</h2><p>第一次使用，从这里完成取钥。账号和数据库目录会自动识别，密钥验证后保存到本机 Keychain。</p></div></section>${card("1 · 首次连接微信", `<p>没有密钥也可以开始。向导先检查环境，只有你点击「授权并开始取钥」后才会备份和临时调整微信。</p><div class="actions"><button id="first-connect" class="primary">首次连接微信（没有密钥）</button><button id="existing-credential">已有密钥或加密备份</button></div><p class="note">需要你完成 macOS 授权，以及按就绪提示登录微信。取钥结束会恢复官方客户端；日常监听只读取本地数据库。</p>`)}${card("接下来会做什么", `<ol class="journey"><li><strong>连接微信</strong><span>检查 → 授权 → 等待登录提示 → 验证、恢复与保存</span></li><li><strong>选择会话</strong><span>先用文件传输助手验证，再选择客户群</span></li><li><strong>连接 DSH 与飞书</strong><span>配置模型渠道和费用插件，选择内部接收群</span></li><li><strong>测试并运行</strong><span>确认一条新增问题能生成建议并送达飞书</span></li></ol><p><a href="https://github.com/CalWade/wx-watcher/blob/main/docs/FIRST_RUN.md">打开新机器完整操作指南 ↗</a></p>`)}<p class="muted">自动取钥当前仅支持已验证组合：Apple Silicon、macOS 27.0、微信 4.1.13（269630）及兼容的 Apple 调试工具。其他组合会说明阻塞原因。</p>`;
    bind("first-connect", () => {
      pendingAccount = accountReport?.current || null;
      captureReadiness = null;
      navigate("keytool");
    });
    bind("existing-credential", () => {
      navigate("keytool");
      $("#existing-options").open = true;
      $("#existing-options").scrollIntoView({ block: "start" });
    });
    return;
  }
  $("#content").innerHTML =
    `${card("自动接入", `<p class="muted">自动识别本机账号，复用已保存凭据、DSH 模型与费用插件，并读取飞书可选群。缺少费用插件时使用 DSH 自带的安装器补齐。</p><button id="auto-connect" class="primary">自动识别并连接</button><div id="onboarding-checks"></div>`)}<div class="steps"><span class="step ${connected ? "done" : ""}">1 连接微信</span><span class="step ${connected && settings.chat_id ? "done" : ""}">2 选择会话</span><span class="step">3 DSH 模型</span><span class="step ${settings.feishu_chat_id ? "done" : ""}">4 飞书接收群</span><span class="step">5 测试并运行</span></div><div class="two">${card("1 · 确认微信账号", `<p>上方分别显示微信当前登录账号、本项目选择的账号，以及各账号的凭据状态。</p><p class="muted">账号不同或当前账号尚无密钥时，请先使用上方入口连接或切换。之前的密钥不能直接用于新账号。</p><button id="go-keytool">查看凭据与取钥向导</button>`)}${card("2 · 选择微信会话", `<p class="muted">只监听明确选择的会话，首次扫描建立基线。</p>${field("搜索群名", "chat-query", settings.group_name)}<div class="actions"><button id="find-chats">搜索会话</button></div><div id="chats"></div><p class="note">${connected ? "当前" : "旧配置会话（切换后需重新选择）"}：${escape(settings.group_name || "尚未选择")}</p>`)}</div><div class="two">${card("3 · DSH 模型与用量", `<p class="muted">复用你在 DSH 中配置的模型渠道。DSH 和 dsh-cost-meter 需要保持可用。</p><div class="actions">${button("安装 / 更新桥接插件", "install-dsh")}${button("测试模型与记账", "test-dsh")}</div><p class="note">模型测试会发起一次真实调用并产生对应渠道用量。</p>`)}${card("4 · 飞书应用机器人", `<label>连接方式<select id="feishu-transport"><option value="cli" ${settings.feishu_transport === "cli" ? "selected" : ""}>复用已有 Lark CLI</option><option value="openapi" ${settings.feishu_transport === "openapi" ? "selected" : ""}>自建飞书应用</option></select></label><div class="actions"><button id="save-transport">保存连接方式</button></div>${settings.feishu_transport === "openapi" ? `<p><a href="https://open.feishu.cn/app" target="_blank" rel="noreferrer">打开飞书应用后台 ↗</a></p><button id="feishu-permissions">下载可批量导入的权限配置</button>${field("自建应用 App ID", "app-id", settings.feishu_app_id)}${field("App Secret（保存后不会显示）", "app-secret", "", "password")}<div class="actions"><button id="save-secret">保存到 Keychain</button></div>` : `<p class="note">复用本机 CLI 已连接的应用与机器人，无需再次填写密钥。</p>`}<div class="actions"><button id="find-feishu">获取机器人所在群</button></div><div id="feishu-chats"></div><p class="note">当前接收群：${escape(settings.feishu_chat_name || "尚未选择")}。先在飞书群中添加应用机器人。</p><div class="actions">${button("发送测试并回读", "test-feishu")}</div>`)}</div>${card("5 · 开启消息助手", `<label class="check"><input id="send-enabled" type="checkbox" ${settings.send_enabled ? "checked" : ""}> 将建议自动发到已选飞书内部群</label><label class="check"><input id="autostart-monitor" type="checkbox" ${settings.autostart_monitor ? "checked" : ""}> 打开本应用后自动恢复监控</label><label class="check"><input id="login-start" type="checkbox" ${state.login_installed ? "checked" : ""}> 登录 Mac 后在菜单栏启动应用</label><p class="muted">保持 Mac 微信登录、DSH 开启。开启前检查待发草稿；已有草稿也可能进入投递。</p><p class="note">「测试全链路并启动」会调用一次模型，并向上方选定飞书群发送一条连通性测试消息。</p><div class="actions"><button id="save-run">保存运行选项</button><button id="finish-setup" class="primary" ${settings.chat_id && settings.feishu_chat_id ? "" : "disabled"}>测试全链路并启动</button>${button("直接开始监控", "start")}${button("暂停监控", "stop")}</div>`)}`;
  bind("go-keytool", () => navigate("keytool"));
  bind("auto-connect", async () => {
    connectionReport = await action("auto-connect", {}, "自动连接本机环境");
    render();
  });
  bind("finish-setup", async () => {
    await action("finish-setup", runOptions(), "验证模型、费用与飞书并启动");
    navigate("overview");
  });
  if (connectionReport) {
    $("#onboarding-checks").innerHTML = connectionReport.checks
      .map(
        (c) =>
          `<p class="${c.ok ? "success" : "error"}">${c.ok ? "✓" : "○"} ${escape(c.stage)}${c.message ? "：" + escape(c.message) : ""}</p>`,
      )
      .join("");
    renderConversationChoices(connectionReport.conversations || []);
    renderFeishuChoices(connectionReport.feishu_chats || []);
  }
  bind("find-chats", async () => {
    renderConversationChoices(
      await action(
        "conversations",
        { query: $("#chat-query").value },
        "读取本地会话",
      ),
    );
  });
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
  bind("save-transport", async () => {
    await action(
      "settings",
      { feishu_transport: $("#feishu-transport").value },
      "保存飞书连接方式",
    );
    render();
  });
  bind("save-secret", async () => {
    await action(
      "feishu-secret",
      { app_id: $("#app-id").value, secret: $("#app-secret").value },
      "安全保存飞书凭据",
    );
    $("#app-secret").value = "";
  });
  bind("find-feishu", async () => {
    const result = await action("feishu-chats", {}, "读取机器人所在群");
    renderFeishuChoices(result.data?.chats || []);
    if (!result.data?.chats?.length)
      notice("机器人尚未加入可访问的群，请先在飞书中添加。");
  });
  bind("save-run", () => action("run-settings", runOptions(), "保存运行选项"));
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
      "停在登录页并确认",
      ["awaiting_login_page"],
      "不要点微信登录；先点击下方登录页确认",
    ],
    [
      "等待取钥监听就绪",
      ["attaching", "attached", "armed", "reattaching"],
      "工具连接中，请暂不登录",
    ],
    [
      "现在登录同一个账号",
      ["ready_for_login", "waiting"],
      "只有提示监听已就绪后，才登录并完成手机确认",
    ],
    [
      "验证、恢复与保存",
      ["verifying", "account_changed", "restoring", "captured", "completed"],
      "成功必须通过数据库验证、恢复官方客户端并保存 Keychain",
    ],
  ];
  return stages
    .map(
      ([title, phases, hint]) =>
        `<li class="${phases.includes(phase || "idle") ? "current" : ""}"><strong>${escape(title)}</strong><span>${escape(hint)}</span></li>`,
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
  const root = report?.db_root || target?.db_root || profile?.db_root || "";
  const targetIsSaved = profile && root === profile.db_root;
  $("#content").innerHTML =
    `<section class="hero"><div><h2>${profile ? "微信凭据与连接管理" : "首次连接微信 · 取钥向导"}</h2><p>先检查，后授权。密钥通过数据库验证并保存后，才进入会话、模型和飞书配置。</p></div>${targetIsSaved ? button("验证当前凭据", "verify-key") : ""}</section><div class="two">${card("1 · 检查这台 Mac", `<p class="muted">请先正常登录官方 Mac 微信，让消息资料保存到本机。检查不会退出、重签或调试微信。</p><button id="capture-check" class="primary">${report ? "重新检查" : "检查并识别微信账号"}</button><div id="capture-checks" aria-live="polite">${report ? report.checks.map((c) => `<div class="readiness ${c.ok ? "passed" : "blocked"}"><strong>${c.ok ? "✓" : "○"} ${escape(c.title)}</strong><p>${escape(c.message)}</p>${!c.ok && c.action === "xcode" ? '<a href="https://developer.apple.com/xcode/resources/">获取 Apple 开发工具 ↗</a>' : ""}${!c.ok && c.action === "wechat" ? '<a href="https://mac.weixin.qq.com/">官方微信网站 ↗</a>' : ""}</div>`).join("") : '<p class="note">新电脑还没有微信消息资料时，先登录并向文件传输助手发一条「NoteBridge 连接测试」，然后点击检查。</p>'}</div><p id="selected-account" class="muted">${report?.account_label || target?.label ? "本次取钥目标：" + escape(report?.account_label || target?.label) : "账号会在检查时识别；有多个历史账号时请先登录目标账号。"}</p><div class="actions"><button id="open-privacy">打开完整磁盘访问设置</button><button id="open-app-management">打开 App 管理设置</button></div><p class="note">权限列表没有本应用时，将窗口底部的应用图标拖入列表。开启后点底部「重新连接」，再回来检查。无需在页面输入系统密码。</p>`)}${card("2 · 按提示完成取钥", `<p>开始后工具会备份官方客户端、临时重签并连接调试器。完成或取消后尝试恢复官方微信；首次取钥不能保证零风控风险。</p><div class="actions"><button id="capture-start" class="primary" ${report?.ready ? "" : "disabled"}>授权并开始取钥</button></div><p class="muted">${report?.ready ? "检查通过，可以开始。系统权限仍由 macOS 在需要时确认。" : "先完成左侧检查；有阻塞项时不会进入取钥。"}</p><ol id="capture-timeline" class="journey">${captureTimeline(captureProgress?.phase)}</ol><div id="capture-progress" class="note" role="status">${escape(captureProgress?.message || "尚未开始。微信重开后先停在登录页；等向导明确提示「监听已就绪」才点击微信登录。")}</div><div class="actions"><button id="capture-login-ready" hidden>我已停在登录页，准备监听</button><button id="capture-cancel" disabled>取消并恢复</button></div>`)}</div>${card("本次账号连接结果", targetIsSaved ? `<div class="row"><strong>${escape(profile.label)}</strong><span class="pill ok">Keychain 已保存</span></div><p class="muted">验证：${escape(profile.verification?.verified || 0)} / ${escape(profile.verification?.total || 0)} 份数据库。取钥完成后，请在恢复的官方微信中正常登录，再继续配置。</p><div class="actions"><button id="continue-setup" class="primary">下一步：选择会话</button></div>` : '<div class="empty">当前取钥目标尚未连接。完成验证并保存后才能继续；其他账号的已存凭据保留在上方账号列表。</div>')}<details id="existing-options" class="card"><summary>已有密钥 / 加密备份 / 手动选择账号（高级选项）</summary><p class="muted">首次连接通常不需要填写这些路径。已有密钥请使用本人可读的文件；不要把密钥粘贴到聊天或日志。</p><label>数据库目录<div class="field-action"><input id="db-root" value="${escape(root)}" placeholder="自动识别或选择 db_storage 目录"><button id="pick-root">选择</button></div></label><div class="actions"><button id="find-accounts">识别本机账号</button></div><div id="account-choices"></div><label>密钥文件<div class="field-action"><input id="key-file" placeholder="选择现有密钥文件"><button id="pick-key">选择</button></div></label>${field("账号备注", "profile-label", report?.account_label || target?.label || profile?.label || "")}<div class="actions"><button id="import-key">验证并存入 Keychain</button></div><h3>加密备份与恢复</h3><p class="muted">备份使用独立口令加密。请保管口令；工具不会保存备份口令。</p>${field("备份 / 恢复口令（至少 12 个字符）", "backup-password", "", "password")}<div class="actions"><button id="backup-key" ${targetIsSaved ? "" : "disabled"}>下载本次账号的加密备份</button><button id="restore-key">导入加密备份</button><input id="backup-file" type="file" accept=".json" hidden></div></details>${card("取钥任务与恢复", `<p class="muted">中断的任务在这里继续查看；如恢复未完成，请先恢复官方客户端。</p><button id="refresh-captures">刷新取钥任务</button><div id="capture-sessions"></div>`)}`;
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
    render();
  });
  bind("continue-setup", () => navigate("setup"));
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
      !window.confirm(
        "本次取钥会退出并重新打开微信、临时重签和连接调试器；工具会备份并在结束时恢复官方客户端。系统将要求管理员授权。开始后请按向导提示操作登录。是否开始？",
      )
    )
      return;
    const result = await action(
      "capture-start",
      { db_root: $("#db-root").value },
      "准备取钥会话",
    );
    $("#job-dialog").close();
    captureProgress = result;
    watchCapture(result.capture_id);
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
  if (!rows.length)
    box.textContent = "没有首次取钥任务。已有密钥导入无需启动取钥。";
}
function watchCapture(id) {
  clearInterval(timer);
  const loginReady = $("#capture-login-ready");
  loginReady.hidden = true;
  loginReady.onclick = async () => {
    try {
      await action(
        "capture-login-ready",
        { capture_id: id },
        "确认已停在微信登录页",
      );
      $("#job-dialog").close();
      loginReady.hidden = true;
    } catch (error) {
      notice(error.message, true);
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
    if (busy || page !== "keytool") return;
    busy = true;
    try {
      const progress = await quietAction("capture-status", { capture_id: id });
      const target = $("#capture-progress");
      if (!target) return;
      captureProgress = progress;
      target.textContent = progress.message || progress.phase;
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
        $("#capture-cancel").disabled = true;
        await refresh();
        await loadCaptureSessions();
        if (progress.phase === "completed") {
          pendingAccount = null;
          captureReadiness = null;
          render();
          notice(
            `连接完成：${progress.verified} / ${progress.total} 份数据库验证通过，官方微信已恢复，密钥已保存。请正常登录恢复的微信，再点击「下一步：选择会话」。`,
          );
        }
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
    `<p class="muted">查看原问题、建议和待确认事项。投递结果不明确的任务请先核对飞书，避免重复发送。</p>` +
    (state.records.length
      ? state.records
          .map(
            (r, i) =>
              `<article class="record"><div class="row"><strong>${escape(r.group)}</strong><span class="pill ${r.status === "sent" ? "ok" : "warn"}">${escape(names[r.status] || r.status)}</span></div><p class="original">${escape(r.event?.content || r.event?.text || "")}</p>${r.proposal ? `<blockquote>${escape(r.proposal.suggested_reply || "无需回复")}</blockquote><p>${escape(r.proposal.reason)}</p><ul>${r.proposal.needs_confirmation.map((v) => `<li>${escape(v)}</li>`).join("")}</ul>` : ""}${["draft", "needs_review", "delivery_review", "budget_review"].includes(r.status) ? `<div class="actions"><button data-task="${escape(r.id)}" data-decision="retry">${r.status === "delivery_review" ? "确认未投递，重新安排" : "重新生成建议"}</button>${r.status === "delivery_review" ? `<button data-task="${escape(r.id)}" data-decision="mark-delivered">确认已投递</button>` : ""}<button data-task="${escape(r.id)}" data-decision="dismiss">忽略此任务</button></div>` : ""}${r.error ? `<p class="error">${escape(r.error)}</p>` : ""}<small>${escape(r.created_at)} · ${r.knowledge_sources?.map((s) => escape(s.name)).join("、") || "未附加知识文件"}</small></article>`,
          )
          .join("")
      : '<div class="empty">暂无任务。完成配置并开启监控后，新消息会出现在这里。</div>');
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
        if (!window.confirm(warning)) return;
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
      window.confirm(
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
  clearInterval(timer);
  page = labels[next] ? next : "overview";
  history.replaceState(null, "", "#" + page);
  $("#notice").textContent = "";
  $("#notice").className = "";
  render();
}
$("#job-close").addEventListener("click", () => $("#job-dialog").close());
document
  .querySelectorAll("[data-page]")
  .forEach((b) => b.addEventListener("click", () => navigate(b.dataset.page)));
(async () => {
  try {
    const params = new URLSearchParams(location.hash.slice(1));
    const token = params.get("token");
    page = params.get("page") || location.hash.slice(1) || "setup";
    if (token) await api("auth", { token });
    history.replaceState(null, "", "#" + page);
    await refresh();
    navigate(page);
    setInterval(async () => {
      try {
        await refresh();
        if (["overview", "records"].includes(page)) render();
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

import AppKit
import WebKit
import Darwin

// The native requester performs the FDA probe itself. It only opens and closes
// the protected system file: no TCC contents are read or modified.
func probeWeChatDirectory() -> Bool {
    let fm = FileManager.default
    let home = fm.homeDirectoryForCurrentUser
    let container = home.appendingPathComponent("Library/Containers/com.tencent.xinWeChat")
    let environment = ProcessInfo.processInfo.environment
    let settingsHome = (environment["NOTE_BRIDGE_HOME"] ?? environment["WX_WATCHER_HOME"]).map { URL(fileURLWithPath: $0) }
        ?? home.appendingPathComponent("Library/Application Support/NoteBridge")
    var target = container.appendingPathComponent("Data/Documents/xwechat_files")
    if let data = try? Data(contentsOf: settingsHome.appendingPathComponent("settings.json")),
       let state = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
       let profiles = state["profiles"] as? [[String: Any]],
       let profile = profiles.first(where: { ($0["id"] as? String) == (state["active_profile"] as? String) }),
       let path = profile["db_root"] as? String,
       path.hasPrefix(container.path + "/") {
        target = URL(fileURLWithPath: path).appendingPathComponent("contact")
    }
    let accessible = (try? fm.contentsOfDirectory(atPath: target.path)) != nil
    let descriptor = open("/Library/Application Support/com.apple.TCC/TCC.db", O_RDONLY | O_CLOEXEC)
    let probeError = descriptor >= 0 ? 0 : errno
    if descriptor >= 0 { close(descriptor) }
    // Bounded private diagnostics help distinguish system registration from source access.
    let report: [String: Any] = ["wechat_directory_readable": accessible, "fda_probe_allowed": descriptor >= 0,
                               "probe_errno": probeError, "pid": getpid(), "bundle": Bundle.main.bundleIdentifier ?? ""]
    if let bytes = try? JSONSerialization.data(withJSONObject: report) {
        let file = settingsHome.appendingPathComponent("native-access.json")
        try? bytes.write(to: file, options: .atomic)
        _ = chmod(file.path, 0o600)
    }
    return accessible
}

final class ApplicationDragView: NSView, NSDraggingSource {
    override func draw(_ dirtyRect: NSRect) {
        NSWorkspace.shared.icon(forFile: Bundle.main.bundlePath).draw(in: bounds.insetBy(dx: 3, dy: 3))
    }
    override func mouseDown(with event: NSEvent) {
        let item = NSDraggingItem(pasteboardWriter: Bundle.main.bundleURL as NSURL)
        item.setDraggingFrame(bounds, contents: NSWorkspace.shared.icon(forFile: Bundle.main.bundlePath))
        beginDraggingSession(with: [item], event: event, source: self)
    }
    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation { .copy }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!
    var item: NSStatusItem!
    var backend: Process?
    var timer: Timer?
    let isKeytool = CommandLine.arguments.contains("--credentials") || CommandLine.arguments.contains("--keytool") || (Bundle.main.object(forInfoDictionaryKey: "NoteBridgeSetupMode") as? Bool == true)
    var baseURL: URL?
    var outputBuffer = Data()
    var terminationSignals: [DispatchSourceSignal] = []
    var permissionPanel: NSPanel?
    var requestedLegacyShutdown = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let bar = NSMenu()
        let appEntry = NSMenuItem(); bar.addItem(appEntry)
        let appMenu = NSMenu(); appEntry.submenu = appMenu
        let quitItem = appMenu.addItem(withTitle: "退出 NoteBridge", action: #selector(quit), keyEquivalent: "q")
        quitItem.target = self
        let editEntry = NSMenuItem(); bar.addItem(editEntry)
        let editMenu = NSMenu(title: "编辑"); editEntry.submenu = editMenu
        for (title, action, key) in [("撤销", "undo:", "z"), ("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        NSApp.mainMenu = bar
        for value in [SIGTERM, SIGINT] {
            signal(value, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: value, queue: .main)
            source.setEventHandler { NSApp.terminate(nil) }
            source.resume(); terminationSignals.append(source)
        }
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "native")
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1160, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.contentMinSize = NSSize(width: 760, height: 560)
        window.title = isKeytool ? "NoteBridge Setup · 连接配置" : "NoteBridge · 本地消息助手"
        window.contentView = web
        window.center()
        if !CommandLine.arguments.contains("--background") {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "NB"
        let menu = NSMenu()
        menu.addItem(withTitle: "打开消息助手", action: #selector(show), keyEquivalent: "")
        menu.addItem(withTitle: "重新连接本地界面", action: #selector(restartInterface), keyEquivalent: "")
        menu.addItem(withTitle: "退出应用与后台服务", action: #selector(quit), keyEquivalent: "q")
        for entry in menu.items { entry.target = self }
        item.menu = menu
        launchBackend()
    }
    func launchBackend() {
        web.loadHTMLString("<p>正在打开本机工作区并检查升级状态，请稍候…</p>", baseURL: nil)
        let legacy = ["io.github.calwade.wx-watcher", "io.github.calwade.wx-keytool"]
            .flatMap { NSRunningApplication.runningApplications(withBundleIdentifier: $0) }
        if !legacy.isEmpty {
            guard !requestedLegacyShutdown else {
                web.loadHTMLString("<h1>旧版尚未退出</h1><p>请退出旧版应用，然后点击重新连接。已有配置和密钥仍然保留。</p>", baseURL: nil)
                return
            }
            requestedLegacyShutdown = true
            let legacyHome = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/WXWatcher")
            if let bytes = try? Data(contentsOf: legacyHome.appendingPathComponent("services.json")),
               let state = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
               let pids = state["pids"] as? [String: Int],
               pids.values.contains(where: { $0 > 1 && kill(pid_t($0), 0) == 0 }),
               let request = try? JSONSerialization.data(withJSONObject: ["resume_monitor": true]) {
                let file = legacyHome.appendingPathComponent("migration-request.json")
                try? request.write(to: file, options: .atomic); _ = chmod(file.path, 0o600)
            }
            for app in legacy { app.terminate() }
            DispatchQueue.global(qos: .utility).async {
                let deadline = Date().addingTimeInterval(30)
                while legacy.contains(where: { !$0.isTerminated }) && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
                DispatchQueue.main.async { self.launchBackend() }
            }
            return
        }
        let env = ProcessInfo.processInfo.environment
        if let override = env["NOTE_BRIDGE_UI_URL"] ?? env["WX_WATCHER_UI_URL"], let url = URL(string: override) {
            baseURL = URL(string: "\(url.scheme ?? "http")://\(url.host ?? "127.0.0.1"):\(url.port ?? 80)")
            web.load(URLRequest(url: url)); return
        }
        guard let resources = Bundle.main.resourceURL else { return }
        let executable = resources.appendingPathComponent("backend/notebridge")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            web.loadHTMLString("<h1>开发构建</h1><p>请使用 scripts/build_macos.py 构建完整应用，或设置 NOTE_BRIDGE_UI_URL 连接开发服务。</p>", baseURL: nil)
            return
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = ["desktop", "--no-browser"] + (isKeytool ? ["--credentials"] : [])
        var environment = ProcessInfo.processInfo.environment
        environment["NOTE_BRIDGE_APP_NAME"] = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String ?? "NoteBridge"
        environment["NOTE_BRIDGE_ACCESS_BIN"] = resources.appendingPathComponent("notebridge-access").path
        environment["NOTE_BRIDGE_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)
        process.environment = environment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        let reader = pipe.fileHandleForReading
        reader.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            DispatchQueue.main.async {
                guard let self else { return }
                self.outputBuffer.append(data)
                if self.outputBuffer.count > 65_536 { self.outputBuffer.removeAll(); return }
                while let newline = self.outputBuffer.firstIndex(of: 10) {
                    let line = self.outputBuffer.prefix(upTo: newline)
                    self.outputBuffer.removeSubrange(...newline)
                    if let json = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                       let value = json["desktop_url"] as? String, let url = URL(string: value),
                       url.host == "127.0.0.1", url.scheme == "http" {
                        self.baseURL = URL(string: "http://127.0.0.1:\(url.port ?? 8767)")
                        self.web.load(URLRequest(url: url))
                        if json["reused"] as? Bool != true {
                            DispatchQueue.global(qos: .utility).async {
                                _ = probeWeChatDirectory()
                            }
                        }
                    } else if let json = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                              let error = json["desktop_error"] as? String {
                        self.web.loadHTMLString("<h1>启动尚未完成</h1><p>请按系统提示处理后重新连接。数据和备份已保留。</p>", baseURL: nil)
                        let alert = NSAlert(); alert.messageText = "NoteBridge 启动尚未完成"; alert.informativeText = error
                        alert.beginSheetModal(for: self.window)
                    }
                }
            }
        }
        do { try process.run(); backend = process }
        catch { web.loadHTMLString("<h1>后台服务启动失败</h1><p>请查看应用运行日志。</p>", baseURL: nil) }
    }
    @objc func show() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc func quit() { NSApp.terminate(nil) }
    // Permission instructions are user-requested and never occupy the main window.
    func showPermissionHelp() {
        if let panel = permissionPanel {
            panel.makeKeyAndOrderFront(nil)
            return
        }
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 480, height: 210),
                            styleMask: [.titled, .closable, .utilityWindow], backing: .buffered, defer: false)
        panel.title = "NoteBridge · 权限帮助"
        panel.isReleasedWhenClosed = false
        panel.hidesOnDeactivate = false
        let content = NSView()
        panel.contentView = content
        let drag = ApplicationDragView(frame: .zero)
        let guidance = NSTextField(wrappingLabelWithString: "在系统设置中开启 NoteBridge 的读取权限。\n\n如果列表里没有应用，将左侧图标拖入列表。完成后点击「重新连接」。")
        guidance.font = NSFont.systemFont(ofSize: 13)
        guidance.textColor = .secondaryLabelColor
        let close = NSButton(title: "暂时关闭", target: self, action: #selector(hidePermissionHelp))
        let reconnect = NSButton(title: "重新连接", target: self, action: #selector(restartInterface))
        reconnect.bezelStyle = .rounded
        for view in [drag, guidance, close, reconnect] {
            content.addSubview(view)
            view.translatesAutoresizingMaskIntoConstraints = false
        }
        NSLayoutConstraint.activate([
            drag.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
            drag.topAnchor.constraint(equalTo: content.topAnchor, constant: 24),
            drag.widthAnchor.constraint(equalToConstant: 56), drag.heightAnchor.constraint(equalToConstant: 56),
            guidance.leadingAnchor.constraint(equalTo: drag.trailingAnchor, constant: 20),
            guidance.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            guidance.topAnchor.constraint(equalTo: content.topAnchor, constant: 24),
            close.trailingAnchor.constraint(equalTo: reconnect.leadingAnchor, constant: -12),
            close.centerYAnchor.constraint(equalTo: reconnect.centerYAnchor),
            reconnect.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            reconnect.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -24),
            reconnect.heightAnchor.constraint(equalToConstant: 32), close.heightAnchor.constraint(equalToConstant: 32),
        ])
        panel.center()
        permissionPanel = panel
        panel.makeKeyAndOrderFront(nil)
    }
    @objc func hidePermissionHelp() { permissionPanel?.orderOut(nil) }
    @objc func restartInterface() {
        let process = backend
        if process?.isRunning == true { process?.terminate() }
        DispatchQueue.global(qos: .utility).async {
            let deadline = Date().addingTimeInterval(30)
            while process?.isRunning == true && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
            DispatchQueue.main.async {
                guard process?.isRunning != true else { return }
                let configuration = NSWorkspace.OpenConfiguration()
                configuration.createsNewApplicationInstance = true
                NSWorkspace.shared.openApplication(at: Bundle.main.bundleURL, configuration: configuration) { app, _ in
                    if app != nil { DispatchQueue.main.async { NSApp.terminate(nil) } }
                }
            }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        show(); return true
    }
    func applicationWillTerminate(_ notification: Notification) {
        if backend?.isRunning == true { backend?.terminate() }
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" || (url.host == "127.0.0.1" && url.port == baseURL?.port) {
            decisionHandler(.allow)
        } else {
            if url.scheme == "https" { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.host == "127.0.0.1",
              message.frameInfo.securityOrigin.port == baseURL?.port,
              let request = message.body as? [String: Any],
              let kind = request["kind"] as? String, let id = request["id"] as? String,
              id.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "-" }) else { return }
        if kind == "open-app-management" {
            showPermissionHelp()
            NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AppBundles")!)
        } else if kind == "open-privacy" {
            showPermissionHelp()
            DispatchQueue.global(qos: .utility).async {
                _ = probeWeChatDirectory()
                DispatchQueue.main.async {
                    NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")!)
                }
            }
        } else if kind == "choose-file" || kind == "choose-directory" {
            let panel = NSOpenPanel()
            panel.canChooseDirectories = kind == "choose-directory"
            panel.canChooseFiles = kind == "choose-file"
            panel.allowsMultipleSelection = false
            panel.begin { [weak self] result in
                let value = result == .OK ? panel.url?.path : nil
                let data = try? JSONSerialization.data(withJSONObject: ["id": id, "value": value as Any? ?? NSNull()])
                if let data = data, let json = String(data: data, encoding: .utf8) {
                    self?.web.evaluateJavaScript("window.dispatchEvent(new CustomEvent('native-result',{detail:\(json)}))")
                }
            }
        }
    }
}
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()

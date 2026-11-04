import AppKit
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!
    var item: NSStatusItem!
    var backend: Process?
    var timer: Timer?
    let isKeytool = CommandLine.arguments.contains("--keytool") || (Bundle.main.object(forInfoDictionaryKey: "WXKeytoolMode") as? Bool == true)
    var baseURL: URL?
    var outputBuffer = Data()
    var terminationSignals: [DispatchSourceSignal] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let bar = NSMenu()
        let appEntry = NSMenuItem(); bar.addItem(appEntry)
        let appMenu = NSMenu(); appEntry.submenu = appMenu
        let quitItem = appMenu.addItem(withTitle: "退出 WX Watcher", action: #selector(quit), keyEquivalent: "q")
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
        window.title = isKeytool ? "WX Keytool · 微信凭据" : "WX Watcher · 微信消息助手"
        window.contentView = web
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "WX"
        let menu = NSMenu()
        menu.addItem(withTitle: "打开消息助手", action: #selector(show), keyEquivalent: "")
        menu.addItem(withTitle: "退出应用与后台服务", action: #selector(quit), keyEquivalent: "q")
        for entry in menu.items { entry.target = self }
        item.menu = menu
        launchBackend()
    }
    func launchBackend() {
        if let override = ProcessInfo.processInfo.environment["WX_WATCHER_UI_URL"], let url = URL(string: override) {
            baseURL = URL(string: "\(url.scheme ?? "http")://\(url.host ?? "127.0.0.1"):\(url.port ?? 80)")
            web.load(URLRequest(url: url)); return
        }
        guard let resources = Bundle.main.resourceURL else { return }
        let executable = resources.appendingPathComponent("backend/wx-watcher")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            web.loadHTMLString("<h1>开发构建</h1><p>请使用 scripts/build_macos.py 构建完整应用，或设置 WX_WATCHER_UI_URL 连接开发服务。</p>", baseURL: nil)
            return
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = ["desktop", "--no-browser"] + (isKeytool ? ["--keytool"] : [])
        var environment = ProcessInfo.processInfo.environment
        environment["WX_KEYTOOL_BIN"] = resources.appendingPathComponent("wx-keytool").path
        environment["WX_WATCHER_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)
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
                    }
                }
            }
        }
        do { try process.run(); backend = process }
        catch { web.loadHTMLString("<h1>后台服务启动失败</h1><p>请查看应用运行日志。</p>", baseURL: nil) }
    }
    @objc func show() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc func quit() { NSApp.terminate(nil) }
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
        if kind == "open-privacy" {
            NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")!)
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

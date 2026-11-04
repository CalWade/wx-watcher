import Foundation
import Darwin
import GreenBubblesAcquire

func system(_ executable: String, _ arguments: [String]) throws -> (Int32, String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: executable)
    p.arguments = arguments
    let out = Pipe()
    p.standardOutput = out
    p.standardError = out
    try p.run()
    let data = out.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}
func requireSystem(_ executable: String, _ arguments: [String]) throws {
    guard try system(executable, arguments).0 == 0 else { throw ToolError.invalid("system_step_failed") }
}
func lldbExecutable() -> String? {
    ["/Applications/Xcode.app/Contents/Developer/usr/bin/lldb", "/usr/bin/lldb"]
        .first(where: { FileManager.default.isExecutableFile(atPath: $0) })
}
func capturePreflight() -> [String: Any] {
    let app = URL(fileURLWithPath: "/Applications/WeChat.app")
    let bundle = Bundle(url: app)
    let version = bundle?.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
    let build = bundle?.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
    let details = (try? system("/usr/bin/codesign", ["-dv", "--verbose=2", app.path]).1) ?? ""
    let supported = version == "4.1.13" && build == "269630"
    let debugger = lldbExecutable() ?? ""
    let debuggerVersion = (try? system(debugger, ["--version"]).1) ?? ""
    let match = debuggerVersion.range(of: #"lldb-\d+"#, options: .regularExpression)
    let debuggerMajor = match.flatMap { Int(debuggerVersion[$0].dropFirst(5)) } ?? 0
    let os = ProcessInfo.processInfo.operatingSystemVersion
    let compatible = os.majorVersion < 27 || debuggerMajor >= 2000
    #if arch(arm64)
    let testedArchitecture = true
    #else
    let testedArchitecture = false
    #endif
    return ["ok": true, "wechat_version": version, "wechat_build": build, "tested_build": supported,
        "official_signature": details.contains("TeamIdentifier=5A4RE8SF68"),
        "hardened_runtime": details.contains("0x10000(runtime)"),
        "lldb": debugger, "lldb_version": debuggerVersion, "debugger_compatible": compatible,
        "tested_architecture": testedArchitecture, "requires_admin": true,
        "ready": supported && testedArchitecture && compatible && details.contains("TeamIdentifier=5A4RE8SF68") && !debugger.isEmpty]
}

final class CaptureJournal: @unchecked Sendable {
    let directory: URL
    let uid: uid_t
    let lock = NSLock()
    init(directory: URL, uid: uid_t) { self.directory = directory; self.uid = uid }
    func write(_ phase: String, _ message: String, _ extra: [String: Any] = [:]) {
        lock.lock(); defer { lock.unlock() }
        var value = extra
        value["phase"] = phase; value["message"] = message
        value["updated_at"] = ISO8601DateFormatter().string(from: Date())
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
            let file = directory.appendingPathComponent("progress.json")
            try? data.write(to: file, options: .atomic)
            _ = chmod(file.path, 0o600); _ = chown(file.path, uid, gid_t.max)
        }
    }
}

func validateJobDirectory(_ path: String) throws -> (URL, uid_t) {
    let dir = URL(fileURLWithPath: path).standardizedFileURL
    guard UUID(uuidString: dir.lastPathComponent) != nil,
          dir.resolvingSymlinksInPath().path == dir.path else { throw ToolError.invalid("unsafe_capture_directory") }
    let attrs = try FileManager.default.attributesOfItem(atPath: dir.path)
    guard let number = attrs[.ownerAccountID] as? NSNumber else { throw ToolError.invalid("invalid_capture_owner") }
    let uid = uid_t(number.uint32Value)
    guard uid >= 500, let user = getpwuid(uid) else { throw ToolError.invalid("invalid_capture_owner") }
    let home = String(cString: user.pointee.pw_dir)
    let prefix = home + "/Library/Application Support/WXWatcher/keytool/jobs/"
    guard dir.path == prefix + dir.lastPathComponent else { throw ToolError.invalid("unsafe_capture_directory") }
    return (dir, uid)
}

func stopOwnedWeChat(_ uid: uid_t) throws {
    for pid in (try? WeChatProcessLocator().processIDs()) ?? [] {
        let owner = try system("/bin/ps", ["-p", String(pid), "-o", "uid="]).1.trimmingCharacters(in: .whitespacesAndNewlines)
        guard owner == String(uid) else { throw ToolError.invalid("wechat_owned_by_another_user") }
        _ = kill(pid, SIGTERM)
    }
    let end = Date().addingTimeInterval(10)
    while Date() < end {
        if ((try? WeChatProcessLocator().processIDs()) ?? []).isEmpty { return }
        Thread.sleep(forTimeInterval: 0.1)
    }
    throw ToolError.invalid("wechat_did_not_exit")
}
func openWeChat(_ uid: uid_t) throws {
    try requireSystem("/bin/launchctl", ["asuser", String(uid), "/usr/bin/sudo", "-u", "#\(uid)",
                                      "/usr/bin/open", "-a", "/Applications/WeChat.app"])
}

func runCaptureJob(_ path: String, recoverOnly: Bool = false) throws {
    guard geteuid() == 0 else { throw ToolError.invalid("capture_requires_system_authorization") }
    let (directory, uid) = try validateJobDirectory(path)
    let journal = CaptureJournal(directory: directory, uid: uid)
    let app = "/Applications/WeChat.app"
    let backup = directory.appendingPathComponent("WeChat.original.app").path
    let pending = directory.appendingPathComponent("pending-key").path
    let cancel = directory.appendingPathComponent("cancel").path
    var modified = false
    var keyReady = FileManager.default.fileExists(atPath: pending)
    var failure = ""
    if recoverOnly {
        guard FileManager.default.fileExists(atPath: backup) else { throw ToolError.invalid("recovery_backup_missing") }
        modified = true
    } else {
        let preflight = capturePreflight()
        guard preflight["ready"] as? Bool == true else { throw ToolError.invalid("capture_preflight_failed") }
        journal.write("backup", "正在备份并验证官方微信客户端")
        try requireSystem("/usr/bin/codesign", ["--verify", "--deep", "--strict", app])
        guard !FileManager.default.fileExists(atPath: backup) else { throw ToolError.invalid("backup_already_exists") }
        try requireSystem("/usr/bin/ditto", [app, backup])
        try requireSystem("/usr/bin/codesign", ["--verify", "--deep", "--strict", backup])
        do {
            try stopOwnedWeChat(uid)
            modified = true // Set before signing: an interrupted sign also requires restoration.
            journal.write("preparing", "正在准备本次取钥，完成后恢复官方客户端")
            try requireSystem("/usr/bin/codesign", ["--force", "--deep", "--sign", "-", app])
            try openWeChat(uid)
            let inventory = try DatabaseSaltInventory(root: directory.appendingPathComponent("samples"))
            guard !inventory.entries.isEmpty else { throw ToolError.invalid("database_samples_missing") }
            var capture = LLDBPassphraseCapture(lldbPath: lldbExecutable()!)
            capture.isCancelled = { FileManager.default.fileExists(atPath: cancel) }
            capture.outputHandler = { message in
                if message.contains("capture ready") {
                    journal.write("ready_for_login", "监听已就绪。请在微信退出账号并重新登录同一个账号。", ["can_cancel": true])
                } else if message.contains("attaching") {
                    journal.write("attaching", "正在连接微信进程，请等待就绪提示", ["can_cancel": true])
                }
            }
            let deadline = Date().addingTimeInterval(600)
            while Date() < deadline {
                let candidate = try capture.capture(timeoutSeconds: max(1, Int(deadline.timeIntervalSinceNow)))
                var verified = 0
                for sample in inventory.saltVerificationSamples {
                    let derived = try SQLCipherKeyVerifier.deriveEncryptionKey(passphrase: candidate, salt: sample.salt)
                    if try SQLCipherKeyVerifier.verify(encryptionKey: derived, page1: sample.page1) { verified += 1 }
                }
                if verified == inventory.distinctSalts.count && verified > 0 {
                    journal.write("verifying", "候选密钥通过本账号数据库校验，正在恢复微信", ["verified": verified])
                    try SecretOutputWriter().write(candidate, to: URL(fileURLWithPath: pending))
                    _ = chown(pending, uid, gid_t.max)
                    keyReady = true
                    break
                }
                journal.write("waiting", "候选未匹配当前账号，继续等待；请确认登录账号正确", ["can_cancel": true])
            }
            if !keyReady { failure = "capture_timed_out" }
        } catch CaptureError.cancelled { failure = "cancelled" }
          catch let ToolError.invalid(code) { failure = code }
          catch { failure = "capture_failed" }
    }
    if modified {
        journal.write("restoring", "正在解除调试并恢复官方微信客户端")
        do {
            let signature = try system("/usr/bin/codesign", ["-dv", "--verbose=2", backup]).1
            guard signature.contains("TeamIdentifier=5A4RE8SF68") else { throw ToolError.invalid("backup_signature_invalid") }
            try requireSystem("/usr/bin/codesign", ["--verify", "--deep", "--strict", backup])
            try stopOwnedWeChat(uid)
            try FileManager.default.removeItem(atPath: app)
            try requireSystem("/usr/bin/ditto", [backup, app])
            try requireSystem("/usr/bin/codesign", ["--verify", "--deep", "--strict", app])
            let restored = capturePreflight()
            guard restored["official_signature"] as? Bool == true,
                  restored["hardened_runtime"] as? Bool == true else { throw ToolError.invalid("restore_signature_failed") }
            try openWeChat(uid)
        } catch {
            journal.write("recovery_required", "客户端恢复未完成。保留了官方备份，请使用恢复入口重试。", ["key_ready": keyReady])
            return
        }
    }
    journal.write(keyReady ? "captured" : (failure == "cancelled" ? "cancelled" : (recoverOnly ? "recovered" : "failed")),
                  keyReady ? "官方客户端已恢复，正在准备安全保存密钥" : (recoverOnly ? "官方客户端已恢复" : "本次取钥已结束，官方客户端已恢复"),
                  ["key_ready": keyReady, "official_restored": true, "error": failure])
}

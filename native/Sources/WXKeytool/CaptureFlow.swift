import Foundation
import Darwin
import GreenBubblesAcquire

struct SystemStepError: Error {
    let code: String
    let executable: String
    let status: Int32
    let detail: String
}

func system(_ executable: String, _ arguments: [String]) throws -> (Int32, String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: executable)
    p.arguments = arguments
    // Authorization jobs must not inherit a protected Desktop/Documents cwd.
    p.currentDirectoryURL = URL(fileURLWithPath: "/private/tmp", isDirectory: true)
    let out = Pipe()
    p.standardOutput = out
    p.standardError = out
    try p.run()
    let data = out.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}
func requireSystem(_ executable: String, _ arguments: [String], code: String = "system_step_failed") throws {
    let result = try system(executable, arguments)
    guard result.0 == 0 else {
        throw SystemStepError(code: code, executable: URL(fileURLWithPath: executable).lastPathComponent,
                              status: result.0, detail: String(result.1.suffix(2000)))
    }
}
func lldbExecutable() -> String? {
    ["/Applications/Xcode.app/Contents/Developer/usr/bin/lldb", "/usr/bin/lldb"]
        .first(where: { FileManager.default.isExecutableFile(atPath: $0) })
}
func probeFileAccess(_ path: String, write: Bool = false) -> (allowed: Bool, error: Int32) {
    // Opening without O_TRUNC/O_CREAT or a write syscall leaves the bytes intact.
    // The OS still checks app-bundle protection when write access is requested.
    let descriptor = open(path, (write ? O_WRONLY : O_RDONLY) | O_CLOEXEC | O_NOFOLLOW)
    guard descriptor >= 0 else { return (false, errno) }
    close(descriptor)
    return (true, 0)
}

func capturePreflight(dbRoot: String? = nil, checkApplicationAccess: Bool = false) -> [String: Any] {
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
    let testedOS = os.majorVersion == 27 && os.minorVersion == 0
    #if arch(arm64)
    let testedArchitecture = true
    #else
    let testedArchitecture = false
    #endif
    var result: [String: Any] = ["ok": true, "wechat_version": version, "wechat_build": build, "tested_build": supported,
        "official_signature": details.contains("TeamIdentifier=5A4RE8SF68"),
        "hardened_runtime": details.contains("0x10000(runtime)"),
        "lldb": debugger, "lldb_version": debuggerVersion, "debugger_compatible": compatible,
        "tested_architecture": testedArchitecture, "requires_admin": true,
        "tested_os": testedOS,
        "ready": supported && testedOS && testedArchitecture && compatible && details.contains("TeamIdentifier=5A4RE8SF68") && !debugger.isEmpty]
    if let dbRoot {
        let access = probeFileAccess(URL(fileURLWithPath: dbRoot).appendingPathComponent("contact/contact.db").path)
        result["database_readable"] = access.allowed
        result["database_access_errno"] = access.error
        if !access.allowed {
            result["ready"] = false
            result["permission_hint"] = "实际取钥工具尚不能读取微信数据库。请为当前安装版开启完整磁盘访问权限，重新打开应用后检查；已有授权可能因开发版签名变化失效。"
            return result
        }
    }
    if checkApplicationAccess {
        let access = probeFileAccess(app.appendingPathComponent("Contents/Info.plist").path, write: true)
        result["application_access_checked"] = true
        result["application_manageable"] = access.allowed
        result["application_access_errno"] = access.error
        if !access.allowed {
            result["ready"] = false
            result["permission_hint"] = "macOS 尚未允许本应用管理微信客户端。请在系统设置 → 隐私与安全性 → App 管理中允许当前应用，再检查取钥条件；此时尚未退出或修改微信。"
        }
    }
    return result
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
        value["helper_pid"] = getpid()
        value["updated_at"] = ISO8601DateFormatter().string(from: Date())
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
            let file = directory.appendingPathComponent("progress.json")
            try? data.write(to: file, options: .atomic)
            _ = chmod(file.path, 0o600); _ = chown(file.path, uid, gid_t.max)
            let history = directory.appendingPathComponent("progress.ndjson")
            if !FileManager.default.fileExists(atPath: history.path) { FileManager.default.createFile(atPath: history.path, contents: nil) }
            if let stream = try? FileHandle(forWritingTo: history) {
                defer { try? stream.close() }
                _ = try? stream.seekToEnd(); try? stream.write(contentsOf: data + Data([10]))
                _ = chmod(history.path, 0o600); _ = chown(history.path, uid, gid_t.max)
            }
        }
    }
    func recordFailure(_ error: SystemStepError) {
        let value: [String: Any] = ["step": error.code, "executable": error.executable,
                                   "exit_status": error.status, "detail": error.detail]
        if let data = try? JSONSerialization.data(withJSONObject: value) {
            let path = directory.appendingPathComponent("failure-details.json")
            try? data.write(to: path, options: .atomic)
            _ = chmod(path.path, 0o600); _ = chown(path.path, uid, gid_t.max)
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

struct WeChatOwnedProcess: Equatable {
    let pid: pid_t
    let uid: uid_t
}
func weChatBundleProcesses(_ listing: String) -> [WeChatOwnedProcess] {
    listing.split(separator: "\n").compactMap { line in
        let parts = line.split(maxSplits: 2, whereSeparator: \.isWhitespace)
        guard parts.count == 3, let pid = Int32(parts[0]), pid > 1,
              let uid = UInt32(parts[1]),
              parts[2].hasPrefix("/Applications/WeChat.app/Contents/") else { return nil }
        return WeChatOwnedProcess(pid: pid, uid: uid)
    }
}
func stopOwnedWeChat(_ uid: uid_t) throws {
    let end = Date().addingTimeInterval(10)
    var signalled = Set<pid_t>()
    while Date() < end {
        let processes = weChatBundleProcesses(try system("/bin/ps", ["-axo", "pid=,uid=,comm="]).1)
        if processes.isEmpty { return }
        // Validate every owner before terminating anything. Include applets and
        // crash handlers so signing never changes a still-running bundle helper.
        guard processes.allSatisfy({ $0.uid == uid }) else { throw ToolError.invalid("wechat_owned_by_another_user") }
        for process in processes where signalled.insert(process.pid).inserted { _ = kill(process.pid, SIGTERM) }
        Thread.sleep(forTimeInterval: 0.1)
    }
    throw ToolError.invalid("wechat_did_not_exit")
}
func openWeChat(_ uid: uid_t) throws {
    try requireSystem("/bin/launchctl", ["asuser", String(uid), "/usr/bin/sudo", "-u", "#\(uid)",
                                      "/usr/bin/open", "-a", "/Applications/WeChat.app"], code: "wechat_launch_failed")
}

func waitForLoginPage(_ directory: URL, ownerPID: Int32, deadline: Date) throws {
    while Date() < deadline {
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("cancel").path)
            || ownerPID <= 1 || kill(ownerPID, 0) != 0 { throw CaptureError.cancelled }
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("login-page-ready").path) { return }
        Thread.sleep(forTimeInterval: 0.1)
    }
    throw ToolError.invalid("login_page_confirmation_timed_out")
}

func databaseRootsFromOpenFiles(_ listing: String) -> Set<String> {
    var roots = Set<String>()
    for line in listing.split(separator: "\n") where line.hasPrefix("n") {
        let path = String(line.dropFirst())
        guard path.range(of: #"/db_storage/(?:contact/contact|session/session|message/message_\d+)\.db(?:-wal)?$"#,
                         options: .regularExpression) != nil,
              let end = path.range(of: "/db_storage/") else { continue }
        roots.insert(String(path[..<end.lowerBound]) + "/db_storage")
    }
    return roots
}

func currentlyOpenDatabaseRoots() -> Set<String> {
    var roots = Set<String>()
    for pid in (try? WeChatProcessLocator().processIDs()) ?? [] {
        if let listing = try? system("/usr/sbin/lsof", ["-n", "-a", "-p", String(pid), "-Fn"]).1 {
            roots.formUnion(databaseRootsFromOpenFiles(listing))
        }
    }
    return roots
}

func runCaptureJob(_ path: String, recoverOnly: Bool = false) throws {
    guard geteuid() == 0 else { throw ToolError.invalid("capture_requires_system_authorization") }
    let lockFD = open("/var/run/io.github.calwade.wx-keytool.lock", O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
    guard lockFD >= 0 else { throw ToolError.invalid("capture_lock_failed") }
    defer { close(lockFD) }
    guard flock(lockFD, LOCK_EX | LOCK_NB) == 0 else { throw ToolError.invalid("another_capture_job_is_running") }
    let (directory, uid) = try validateJobDirectory(path)
    let journal = CaptureJournal(directory: directory, uid: uid)
    let app = "/Applications/WeChat.app"
    let backup = directory.appendingPathComponent("WeChat.original.app").path
    let pending = directory.appendingPathComponent("pending-key").path
    let cancel = directory.appendingPathComponent("cancel").path
    let requestData = try Data(contentsOf: directory.appendingPathComponent("request.json"))
    let request = try JSONSerialization.jsonObject(with: requestData) as? [String: Any]
    let ownerPID = (request?["owner_pid"] as? Int32) ?? 0
    var modified = false
    var keyReady = FileManager.default.fileExists(atPath: pending)
    var failure = ""
    var observedRoots: [String] = []
    let deadline = Date().addingTimeInterval(600)
    do {
    if recoverOnly {
        guard FileManager.default.fileExists(atPath: backup) else { throw ToolError.invalid("recovery_backup_missing") }
        modified = true
    } else {
        journal.write("permissions", "正在检查实际系统任务的客户端访问权限")
        let preflight = capturePreflight(checkApplicationAccess: true)
        guard preflight["application_manageable"] as? Bool == true else {
            throw ToolError.invalid("application_management_denied")
        }
        guard preflight["ready"] as? Bool == true else { throw ToolError.invalid("capture_preflight_failed") }
        journal.write("backup", "正在备份并验证官方微信客户端")
        try requireSystem("/usr/bin/codesign", ["--verify", "--deep", "--strict", app])
        guard !FileManager.default.fileExists(atPath: backup) else { throw ToolError.invalid("backup_already_exists") }
        try requireSystem("/usr/bin/ditto", [app, backup])
        try requireSystem("/usr/bin/codesign", ["--verify", "--deep", "--strict", backup])
        do {
            if FileManager.default.fileExists(atPath: cancel) { throw CaptureError.cancelled }
            try stopOwnedWeChat(uid)
            modified = true // Set before signing: an interrupted sign also requires restoration.
            journal.write("preparing", "正在准备本次取钥，完成后恢复官方客户端")
            journal.write("signing", "正在准备临时调试签名")
            try requireSystem("/usr/bin/codesign", ["--force", "--deep", "--sign", "-", app], code: "wechat_temporary_sign_failed")
            journal.write("opening_client", "正在以当前用户启动微信")
            try openWeChat(uid)
            journal.write("awaiting_login_page", "微信已重新打开。请停在微信登录页，暂不点击登录；在向导点击「我已停在登录页，准备监听」。", ["can_cancel": true])
            try waitForLoginPage(directory, ownerPID: ownerPID, deadline: deadline)
            let inventory = try DatabaseSaltInventory(root: directory.appendingPathComponent("samples"))
            guard !inventory.entries.isEmpty else { throw ToolError.invalid("database_samples_missing") }
            var capture = LLDBPassphraseCapture(lldbPath: lldbExecutable()!)
            capture.isCancelled = {
                FileManager.default.fileExists(atPath: cancel) || ownerPID <= 1 || kill(ownerPID, 0) != 0
            }
            capture.outputHandler = { message in
                if message.contains("capture ready") {
                    journal.write("ready_for_login", "取钥监听已就绪。现在可以点击微信登录，并完成手机确认。请登录所选数据库对应的账号。", ["can_cancel": true])
                } else if message.contains("exited; waiting") {
                    journal.write("reattaching", "微信进程已退出，正在等待并连接新进程。请暂不点击登录。", ["can_cancel": true, "target_exited": true])
                } else if message.contains("attaching") {
                    let pid = Int(message.split(separator: " ").last ?? "") ?? 0
                    journal.write("attaching", "正在连接微信进程，请等待就绪提示", ["can_cancel": true, "target_pid": pid])
                } else if message.contains("attach completed") {
                    journal.write("attached", "已连接微信进程，正在安装取钥断点", ["can_cancel": true])
                } else if message.contains("breakpoint installed") {
                    journal.write("armed", "取钥断点已安装，正在恢复微信运行", ["can_cancel": true])
                }
            }
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
                let roots = currentlyOpenDatabaseRoots()
                if let expected = request?["db_root"] as? String, !roots.isEmpty, roots != [expected] {
                    observedRoots = roots.sorted()
                    journal.write("account_changed", "微信当前打开的账号目录与本次选择不同，正在停止取钥并恢复官方客户端。", ["verified": verified, "total": inventory.distinctSalts.count])
                    throw ToolError.invalid("account_changed")
                }
                journal.write("waiting", "本次候选未通过数据库校验，仍未保存密钥。请查看账号和进程状态。", ["can_cancel": true, "verified": verified, "total": inventory.distinctSalts.count])
            }
            if !keyReady { failure = "capture_timed_out" }
        } catch CaptureError.cancelled { failure = "cancelled" }
          catch CaptureError.timedOut { failure = "capture_timed_out" }
          catch CaptureError.attachFailed(_) { failure = "debugger_attach_failed" }
          catch CaptureError.lldbLaunchFailed { failure = "debugger_launch_failed" }
          catch CaptureError.exitedWithoutCapture { failure = "debugger_exited_without_capture" }
          catch CaptureError.noWeChatProcess { failure = "wechat_process_not_found" }
          catch let error as SystemStepError { failure = error.code; journal.recordFailure(error) }
          catch let ToolError.invalid(code) { failure = code }
          catch { failure = "capture_failed" }
    }
    } catch let error as SystemStepError {
        journal.recordFailure(error)
        journal.write(recoverOnly ? "recovery_required" : "failed", "系统准备步骤未完成，请查看本次任务诊断。", ["error": error.code, "official_restored": !recoverOnly])
        return
    } catch let ToolError.invalid(code) {
        let message = code == "application_management_denied"
            ? "系统后台任务未获得 App 管理权限，未退出或修改微信。请在前台完成权限检查后重试。"
            : "取钥准备未完成，未修改微信客户端。请检查环境后重试。"
        journal.write(recoverOnly ? "recovery_required" : "failed", recoverOnly ? "恢复条件检查失败，请保留备份并查看诊断。" : message, ["error": code, "official_restored": !recoverOnly])
        return
    } catch {
        journal.write("failed", "客户端备份未完成，未修改微信。请检查可用空间和权限后重试。", ["error": "backup_failed", "official_restored": true])
        return
    }
    if modified {
        journal.write("restoring", "正在解除调试并恢复官方微信客户端")
        do {
            let signature = try system("/usr/bin/codesign", ["-dv", "--verbose=2", backup]).1
            guard signature.contains("TeamIdentifier=5A4RE8SF68") else { throw ToolError.invalid("backup_signature_invalid") }
            try requireSystem("/usr/bin/codesign", ["--verify", "--deep", "--strict", backup])
            try stopOwnedWeChat(uid)
            let staging = URL(fileURLWithPath: "/Applications/.WXWatcher-restore-\(directory.lastPathComponent).app")
            let previous = URL(fileURLWithPath: "/Applications/.WXWatcher-previous-\(UUID().uuidString).app")
            if FileManager.default.fileExists(atPath: staging.path) { try FileManager.default.removeItem(at: staging) }
            try requireSystem("/usr/bin/ditto", [backup, staging.path])
            try replaceApplication(application: URL(fileURLWithPath: app), staged: staging, previous: previous) { url in
                try requireSystem("/usr/bin/codesign", ["--verify", "--deep", "--strict", url.path])
                let details = try system("/usr/bin/codesign", ["-dv", "--verbose=2", url.path]).1
                guard details.contains("TeamIdentifier=5A4RE8SF68"), details.contains("0x10000(runtime)")
                else { throw ToolError.invalid("restore_signature_failed") }
            }
            let restored = capturePreflight()
            guard restored["official_signature"] as? Bool == true,
                  restored["hardened_runtime"] as? Bool == true else { throw ToolError.invalid("restore_signature_failed") }
            try openWeChat(uid)
        } catch {
            journal.write("recovery_required", "客户端恢复未完成。保留了官方备份，请使用恢复入口重试。", ["key_ready": keyReady])
            return
        }
    }
    let failedMessage = failure == "account_changed"
        ? "取钥期间微信打开了另一套账号目录，未保存候选密钥；官方微信已恢复。请选择当前账号的目录后再取钥。"
        : failure == "login_page_confirmation_timed_out"
        ? "等待登录页确认超时，官方微信已恢复，未保存密钥。"
        : failure == "wechat_temporary_sign_failed"
        ? "临时调试签名未完成，官方微信已恢复。请检查本应用的 App 管理权限，并查看任务诊断。"
        : failure == "debugger_attach_failed"
        ? "调试器未完成附加或未在期限内恢复微信运行；官方微信已恢复，未保存密钥。"
        : failure == "capture_timed_out"
        ? "等待有效密钥超时，未保存密钥；官方微信已恢复。"
        : (failure == "wechat_launch_failed" ? "微信启动未完成，官方客户端已恢复。请查看任务诊断。" : "本次取钥已结束，官方客户端已恢复")
    journal.write(keyReady ? "captured" : (failure == "cancelled" ? "cancelled" : (recoverOnly ? "recovered" : "failed")),
                  keyReady ? "官方客户端已恢复，正在准备安全保存密钥" : (recoverOnly ? "官方客户端已恢复" : failedMessage),
                  ["key_ready": keyReady, "official_restored": true, "error": failure, "observed_db_roots": observedRoots])
}

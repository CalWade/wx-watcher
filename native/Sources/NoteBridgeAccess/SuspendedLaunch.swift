import Darwin
import Foundation

/// Creates the process without executing dyld, application code, or showing a
/// login window. Used only by the user-side half of the authorized transaction.
func spawnSuspended(executable: String, arguments: [String] = []) throws -> pid_t {
    var attributes: posix_spawnattr_t?
    var actions: posix_spawn_file_actions_t?
    guard posix_spawnattr_init(&attributes) == 0 else { throw ToolError.invalid("spawn_attributes_failed") }
    defer { posix_spawnattr_destroy(&attributes) }
    guard posix_spawn_file_actions_init(&actions) == 0 else { throw ToolError.invalid("spawn_actions_failed") }
    defer { posix_spawn_file_actions_destroy(&actions) }
    guard posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_START_SUSPENDED | POSIX_SPAWN_CLOEXEC_DEFAULT)) == 0
    else { throw ToolError.invalid("suspended_launch_unavailable") }
    // Never inherit the privileged helper's pipes or private job descriptors.
    for fd: Int32 in 0...2 {
        guard posix_spawn_file_actions_addopen(&actions, fd, "/dev/null", fd == 0 ? O_RDONLY : O_WRONLY, 0) == 0
        else { throw ToolError.invalid("spawn_io_failed") }
    }
    let argv = ([executable] + arguments).map { strdup($0) } + [nil]
    defer { for item in argv { free(item) } }
    let envp = ProcessInfo.processInfo.environment.map { strdup("\($0.key)=\($0.value)") } + [nil]
    defer { for item in envp { free(item) } }
    var pid: pid_t = 0
    let status = argv.withUnsafeBufferPointer { pointer in
        envp.withUnsafeBufferPointer { environment in
            posix_spawn(&pid, executable, &actions, &attributes, pointer.baseAddress!, environment.baseAddress!)
        }
    }
    guard status == 0, pid > 1 else { throw ToolError.invalid("wechat_suspended_launch_failed") }
    return pid
}

func launchSuspendedWeChatForJob(_ path: String) throws -> pid_t {
    let (directory, uid) = try validateJobDirectory(path)
    guard getuid() == uid, geteuid() == uid else { throw ToolError.invalid("suspended_launch_owner_mismatch") }
    let data = try Data(contentsOf: directory.appendingPathComponent("progress.json"))
    let progress = try JSONSerialization.jsonObject(with: data) as? [String: Any]
    guard progress?["phase"] as? String == "preparing_listener",
          let parent = progress?["helper_pid"] as? Int32, parent > 1, kill(parent, 0) == 0 || errno == EPERM,
          !FileManager.default.fileExists(atPath: directory.appendingPathComponent("cancel").path)
    else { throw ToolError.invalid("suspended_launch_job_not_active") }
    let existing = weChatBundleProcesses(try system("/bin/ps", ["-axo", "pid=,uid=,comm="]).1)
    guard existing.isEmpty else { throw ToolError.invalid("wechat_already_running") }
    return try spawnSuspended(executable: "/Applications/WeChat.app/Contents/MacOS/WeChat")
}

func startSuspendedWeChat(_ uid: uid_t, directory: URL) throws -> pid_t {
    let helper = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL.path
    let result = try system("/bin/launchctl", ["asuser", String(uid), "/usr/bin/sudo", "-H", "-u", "#\(uid)",
                                               helper, "--suspend-wechat-job", directory.path])
    guard result.0 == 0,
          let data = result.1.data(using: .utf8),
          let value = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          let pid = value["pid"] as? Int32, pid > 1
    else { throw ToolError.invalid("wechat_suspended_launch_failed") }
    return pid
}

/// Only stopped processes in the selected user's WeChat bundle can be killed.
/// SIGTERM cannot finish a never-resumed process. This also repairs interrupted
/// launches after a helper crash, without resuming a login window unprotected.
func suspendedWeChatProcesses(_ listing: String, uid: uid_t) -> [pid_t] {
    listing.split(separator: "\n").compactMap { line in
        let parts = line.split(maxSplits: 3, whereSeparator: \.isWhitespace)
        guard parts.count == 4, let pid = Int32(parts[0]), pid > 1,
              UInt32(parts[1]) == uid, parts[2].contains("T"),
              parts[3].hasPrefix("/Applications/WeChat.app/Contents/") else { return nil }
        return pid
    }
}
func stopSuspendedWeChat(_ uid: uid_t) throws {
    let listing = try system("/bin/ps", ["-axo", "pid=,uid=,stat=,comm="]).1
    for pid in suspendedWeChatProcesses(listing, uid: uid) { _ = kill(pid, SIGKILL) }
}

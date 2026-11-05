import Darwin
import Foundation

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
    guard kill(pid, 0) == 0, getsid(pid) == pid, getpgid(pid) == pid
    else { throw ToolError.invalid("wechat_process_exited_before_listener") }
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

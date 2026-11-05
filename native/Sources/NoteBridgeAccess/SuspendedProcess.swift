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
    // Foundation Process gives the short-lived launchctl/sudo/helper chain its
    // own process group. Leaving a stopped child in that group sends SIGHUP and
    // SIGCONT when it becomes orphaned on helper exit. A new session prevents
    // that implicit resume/termination before the debugger can attach.
    let flags = POSIX_SPAWN_START_SUSPENDED | POSIX_SPAWN_CLOEXEC_DEFAULT | POSIX_SPAWN_SETSID
    guard posix_spawnattr_setflags(&attributes, Int16(flags)) == 0
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


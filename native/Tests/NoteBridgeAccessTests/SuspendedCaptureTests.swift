import Darwin
import Foundation
import Testing
import GreenBubblesAcquire
@testable import NoteBridgeAccess

/// Compile the production spawn function into a short-lived helper so this
/// exercises Foundation Process's process group and the actual handoff, not
/// just a child whose parent stays alive inside the test runner.
func launchThroughExitedHelper(_ directory: URL, executable: String, arguments: [String]) throws -> pid_t {
    let source = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().appendingPathComponent("Sources/NoteBridgeAccess/SuspendedProcess.swift")
    let main = directory.appendingPathComponent("main.swift")
    try """
    import Foundation
    import Darwin
    enum ToolError: Error { case invalid(String) }
    let pid = try spawnSuspended(executable: CommandLine.arguments[1], arguments: Array(CommandLine.arguments.dropFirst(2)))
    print(pid)
    """.write(to: main, atomically: true, encoding: .utf8)
    let helper = directory.appendingPathComponent("launch-helper")
    try requireSystem("/usr/bin/xcrun", ["swiftc", source.path, main.path, "-o", helper.path])
    let result = try system(helper.path, [executable] + arguments)
    #expect(result.0 == 0)
    return try #require(Int32(result.1.trimmingCharacters(in: .whitespacesAndNewlines)))
}

@Suite struct SuspendedLaunchTests {
    @Test func pausedTargetSurvivesLauncherExitWithoutRunning() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let marker = dir.appendingPathComponent("started")
        let pid = try launchThroughExitedHelper(dir, executable: "/bin/sh", arguments: ["-c", "echo started > \(marker.path)"])
        defer { kill(pid, SIGKILL) }
        Thread.sleep(forTimeInterval: 0.3)
        #expect(kill(pid, 0) == 0, "Paused child must survive the short-lived helper exiting")
        #expect(getsid(pid) == pid, "The child must not inherit the launcher's session")
        #expect(!FileManager.default.fileExists(atPath: marker.path))
        kill(pid, SIGCONT)
        let end = Date().addingTimeInterval(5)
        while !FileManager.default.fileExists(atPath: marker.path), Date() < end { Thread.sleep(forTimeInterval: 0.05) }
        #expect(FileManager.default.fileExists(atPath: marker.path))
    }
    @Test func cleanupOnlySelectsStoppedOwnedWeChatProcesses() {
        let listing = """
        10 501 T /Applications/WeChat.app/Contents/MacOS/WeChat
        11 501 S /Applications/WeChat.app/Contents/MacOS/WeChat
        12 502 T /Applications/WeChat.app/Contents/MacOS/WeChat
        13 501 T /Applications/Other.app/Contents/MacOS/Other
        14 501 T /Applications/WeChat.app.old/Contents/MacOS/WeChat
        """
        #expect(suspendedWeChatProcesses(listing, uid: 501) == [10])
    }

    @Test func suspendedProcessDoesNotRunUntilReleased() throws {
        let path = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).path
        let pid = try spawnSuspended(executable: "/bin/sh", arguments: ["-c", "echo started > \(path)"])
        defer { kill(pid, SIGKILL); var status: Int32 = 0; waitpid(pid, &status, 0); try? FileManager.default.removeItem(atPath: path) }
        Thread.sleep(forTimeInterval: 0.2)
        #expect(!FileManager.default.fileExists(atPath: path))
        kill(pid, SIGCONT)
        let end = Date().addingTimeInterval(5)
        while !FileManager.default.fileExists(atPath: path), Date() < end { Thread.sleep(forTimeInterval: 0.05) }
        #expect(FileManager.default.fileExists(atPath: path))
    }
}

// Explicit opt-in: invokes the real local debugger against a disposable program,
// never WeChat, account databases, Keychain, or the privileged capture transaction.
@Suite(.serialized, .enabled(if: ProcessInfo.processInfo.environment["NOTEBRIDGE_TEST_LLDB"] == "1"))
struct LiveSuspendedCaptureTests {
    func fixture(_ mode: String, handoff: Bool = false) throws -> (URL, pid_t) {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let code = """
        #include <CommonCrypto/CommonKeyDerivation.h>
        #include <stdio.h>
        #include <unistd.h>
        int main(int argc, char **argv) {
          FILE *f = fopen(argv[1], "w"); fprintf(f, "%u", getuid()); fclose(f);
          if (argv[2][0] == 'w') sleep(120);
          unsigned char key[32] = {0}, salt[16] = {0}, out[32];
          CCKeyDerivationPBKDF(2, (const char *)key, 32, salt, 16, 5, 256000, out, 32);
          sleep(120); return 0;
        }
        """
        try code.write(to: dir.appendingPathComponent("probe.c"), atomically: true, encoding: .utf8)
        try requireSystem("/usr/bin/clang", ["-O0", "-g", dir.appendingPathComponent("probe.c").path, "-o", dir.appendingPathComponent("probe").path])
        let executable = dir.appendingPathComponent("probe").path
        let arguments = [dir.appendingPathComponent("started").path, mode]
        let pid = try handoff
            ? launchThroughExitedHelper(dir, executable: executable, arguments: arguments)
            : spawnSuspended(executable: executable, arguments: arguments)
        return (dir, pid)
    }
    func clean(_ dir: URL, _ pid: pid_t) {
        kill(pid, SIGKILL); var status: Int32 = 0; waitpid(pid, &status, 0)
        try? FileManager.default.removeItem(at: dir)
    }
    @Test func capturesFirstDerivationFromSuspendedLaunch() throws {
        let (dir, pid) = try fixture("capture", handoff: true)
        defer { clean(dir, pid) }
        #expect(!FileManager.default.fileExists(atPath: dir.appendingPathComponent("started").path))
        var capture = LLDBPassphraseCapture(lldbPath: try #require(lldbExecutable()))
        capture.beforeFailureCleanup = { kill(pid, SIGKILL) }
        let key = try capture.capture(processID: pid, timeoutSeconds: 90)
        #expect(key.withUnsafeBytes { Array($0) } == [UInt8](repeating: 0, count: 32))
        #expect(try String(contentsOf: dir.appendingPathComponent("started"), encoding: .utf8) == String(getuid()))
    }
    @Test func missingTargetReportsFailureInsteadOfWaitingForTimeout() throws {
        let (dir, pid) = try fixture("wait")
        defer { try? FileManager.default.removeItem(at: dir) }
        kill(pid, SIGKILL); var status: Int32 = 0; waitpid(pid, &status, 0)
        let capture = LLDBPassphraseCapture(lldbPath: try #require(lldbExecutable()))
        let started = Date()
        #expect(throws: CaptureError.attachFailed("target_missing")) {
            _ = try capture.capture(processID: pid, timeoutSeconds: 15)
        }
        #expect(Date().timeIntervalSince(started) < 10)
        #expect(!FileManager.default.fileExists(atPath: dir.appendingPathComponent("started").path))
    }
    @Test func cancelledSetupNeverRunsTarget() throws {
        let (dir, pid) = try fixture("wait")
        defer { clean(dir, pid) }
        var capture = LLDBPassphraseCapture(lldbPath: try #require(lldbExecutable()))
        capture.beforeFailureCleanup = { kill(pid, SIGKILL) }
        capture.isCancelled = { true }
        #expect(throws: CaptureError.cancelled) { _ = try capture.capture(processID: pid, timeoutSeconds: 10) }
        #expect(!FileManager.default.fileExists(atPath: dir.appendingPathComponent("started").path))
    }
    @Test func failedSetupDoesNotFallThroughToResume() throws {
        let (dir, pid) = try fixture("wait")
        defer { clean(dir, pid) }
        let script = LLDBCaptureScript.script(processID: pid).replacingOccurrences(of:
            "CCKeyDerivationPBKDF", with: "no_such_symbol_for_failure_test")
        // The production script accepts pending symbols, but rejects a failed
        // attach before constructing any breakpoint or issuing continue.
        let p = Process(); let input = Pipe(); let output = Pipe()
        p.executableURL = URL(fileURLWithPath: try #require(lldbExecutable()))
        p.arguments = ["--no-lldbinit"]; p.standardInput = input; p.standardOutput = output; p.standardError = output
        try p.run()
        let badAttach = script.replacingOccurrences(of: "process attach -p \(pid)", with: "not-a-command")
        try input.fileHandleForWriting.write(contentsOf: Data((badAttach + "quit\n").utf8))
        try input.fileHandleForWriting.close()
        let result = String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        p.waitUntilExit()
        #expect(result.contains("RuntimeError: GB_CAPTURE_SETUP_FAILED"))
        #expect(!result.split(separator: "\n").contains("GB_CAPTURE_RUNNING"))
        #expect(!FileManager.default.fileExists(atPath: dir.appendingPathComponent("started").path))
    }
}

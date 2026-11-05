import Foundation
import Darwin
import GreenBubblesAcquire

struct VerifiedCaptureAccount {
    let root: URL
    let verified: Int
}

func validatedCaptureRoot(_ path: String, base: URL) throws -> URL {
    let root = URL(fileURLWithPath: path).standardizedFileURL
    guard root.path == path, root.resolvingSymlinksInPath().path == root.path,
          root.lastPathComponent == "db_storage",
          root.deletingLastPathComponent().deletingLastPathComponent().path == base.standardizedFileURL.path
    else { throw ToolError.invalid("unsafe_capture_account") }
    // Foundation may leave symlinks unresolved when the final component does
    // not exist yet. Inspect each account component even before first login.
    for component in [base, root.deletingLastPathComponent(), root] {
        var metadata = stat()
        if lstat(component.path, &metadata) == 0 {
            guard metadata.st_mode & S_IFMT == S_IFDIR else { throw ToolError.invalid("unsafe_capture_account") }
        } else if errno != ENOENT { throw ToolError.invalid("unsafe_capture_account") }
    }
    return root
}

/// Only inspect the exact process started by this job. A separate client or a
/// recent historical directory cannot determine which account owns a candidate.
func captureDatabaseRoots(pid: pid_t, uid: uid_t) throws -> Set<String> {
    let listing = try system("/bin/ps", ["-p", String(pid), "-o", "pid=,uid=,comm="]).1
    guard weChatBundleProcesses(listing) == [WeChatOwnedProcess(pid: pid, uid: uid)]
    else { throw CaptureError.targetExited }
    return databaseRootsFromOpenFiles(try system("/usr/sbin/lsof", ["-n", "-a", "-p", String(pid), "-Fn"]).1)
}

/// The KDF call can precede directory/page creation on a new account. Keep the
/// candidate in memory and wait for a verifiable contact database. Never create
/// or write files in the source account. Inject observations/time for isolation.
func awaitCaptureDatabase(candidate: PassphraseSecret, base: URL, expected: String?, deadline: Date,
                          cancelled: () -> Bool, observe: () throws -> Set<String>,
                          now: () -> Date = Date.init,
                          sleep: (TimeInterval) -> Void = Thread.sleep) throws -> VerifiedCaptureAccount {
    if let expected { _ = try validatedCaptureRoot(expected, base: base) }
    var sawCompletePages = false
    while now() < deadline {
        if cancelled() { throw CaptureError.cancelled }
        let roots = try observe()
        for path in roots { _ = try validatedCaptureRoot(path, base: base) }
        guard roots.count <= 1 else { throw ToolError.invalid("capture_account_ambiguous") }
        if let path = roots.first {
            if let expected, path != expected { throw ToolError.invalid("account_changed") }
            let root = try validatedCaptureRoot(path, base: base)
            // DB open handles may precede a complete encrypted first page. Try
            // again for absent, short or partially written pages until bounded.
            if let contact = try? DatabaseSaltInventory(root: root.appendingPathComponent("contact")),
               contact.entries.contains(where: { $0.relativePath == "contact.db" }),
               let inventory = try? DatabaseSaltInventory(root: root), !inventory.entries.isEmpty {
                sawCompletePages = true
                var verified = 0
                for sample in inventory.saltVerificationSamples {
                    if cancelled() { throw CaptureError.cancelled }
                    let key = try SQLCipherKeyVerifier.deriveEncryptionKey(passphrase: candidate, salt: sample.salt)
                    if try SQLCipherKeyVerifier.verify(encryptionKey: key, page1: sample.page1) { verified += 1 }
                }
                if verified == inventory.distinctSalts.count {
                    // Ensure the live account did not change while checking pages.
                    guard try observe() == roots else { throw ToolError.invalid("account_changed") }
                    return VerifiedCaptureAccount(root: root, verified: verified)
                }
            }
        }
        sleep(min(1, max(0, deadline.timeIntervalSince(now()))))
    }
    throw ToolError.invalid(sawCompletePages ? "candidate_verification_failed" : "database_initialization_timed_out")
}

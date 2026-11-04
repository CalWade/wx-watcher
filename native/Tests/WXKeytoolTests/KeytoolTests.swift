import Testing
import Foundation
import Darwin
import GreenBubblesAcquire
@testable import WXKeytool

@Suite struct KeytoolTests {
    @Test func clientShutdownIncludesBundleHelpersAndExcludesOtherApps() {
        let listing = """
        12 501 /Applications/WeChat.app/Contents/MacOS/WeChat
        13 501 /Applications/WeChat.app/Contents/MacOS/WeChatAppEx.app/Contents/MacOS/WeChatAppEx
        14 501 /Applications/WeChat.app/Contents/MacOS/Helpers/WeChat Helper (GPU)
        15 501 /Applications/WeChat.app/Contents/Frameworks/crashpad_handler
        16 501 /Applications/WeChat.app.old/Contents/MacOS/WeChat
        17 501 /Applications/Other.app/Contents/MacOS/Other
        """
        #expect(weChatBundleProcesses(listing).map(\.pid) == [12, 13, 14, 15])
        #expect(weChatBundleProcesses("18 502 /Applications/WeChat.app/Contents/MacOS/WeChat").first?.uid == 502)
    }
    @Test func loginPageGateHonorsConfirmationCancellationAndDeadline() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        #expect(throws: ToolError.invalid("login_page_confirmation_timed_out")) {
            try waitForLoginPage(directory, ownerPID: getpid(), deadline: Date().addingTimeInterval(-1))
        }
        try Data().write(to: directory.appendingPathComponent("login-page-ready"))
        try waitForLoginPage(directory, ownerPID: getpid(), deadline: Date().addingTimeInterval(1))
        try Data().write(to: directory.appendingPathComponent("cancel"))
        #expect(throws: CaptureError.cancelled) {
            try waitForLoginPage(directory, ownerPID: getpid(), deadline: Date().addingTimeInterval(1))
        }
    }
    @Test func accountTrackingIgnoresPreviousAccountCaches() {
        let listing = """
        n/example/old/db_storage/MMKV/storage_common
        n/example/old/db_storage/message/message_0.kvdb-shm
        n/example/current/db_storage/contact/contact.db
        n/example/current/db_storage/message/message_0.db-wal
        """
        #expect(databaseRootsFromOpenFiles(listing) == ["/example/current/db_storage"])
    }
    @Test func permissionProbePreservesBytesAndReportsDeniedAccess() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("permission-probe")
        let bytes = Data("unchanged permission fixture".utf8)
        try bytes.write(to: file)
        #expect(probeFileAccess(file.path, write: true).allowed)
        #expect(try Data(contentsOf: file) == bytes)
        try FileManager.default.setAttributes([.posixPermissions: 0o400], ofItemAtPath: file.path)
        let access = probeFileAccess(file.path, write: true)
        #expect(!access.allowed)
        #expect(access.error == EACCES)
        #expect(try Data(contentsOf: file) == bytes)
    }
    @Test func keyValidation() throws {
        #expect(try keyData(String(repeating: "ab", count: 32)).count == 32)
        #expect(throws: ToolError.self) { try keyData("bad") }
        #expect(throws: ToolError.self) { try keyData(String(repeating: "Ａ", count: 64)) }
        #expect(throws: ToolError.self) { try keyQuery("../../private") }
    }
    @Test func missingDataAccessIsNotReportedAsWrongKey() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.posixPermissions: 0o000], ofItemAtPath: directory.path)
        defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
            try? FileManager.default.removeItem(at: directory)
        }
        #expect(throws: ToolError.invalid("database_access_denied")) {
            try verify(Data(repeating: 0, count: 32), root: directory.path)
        }
    }
    @Test func backupDerivationRejectsShortPasswords() {
        #expect(throws: ToolError.self) { try derive("short", salt: Data(repeating: 1, count: 16)) }
    }
    @Test func secretReadRequiresExplicitLocalFlag() {
        #expect(throws: ToolError.self) { try request(["operation": "read", "key_ref": UUID().uuidString], internalRead: false) }
    }
    @Test func encryptedBackupsRoundTripAndRejectWrongPasswordOrTampering() throws {
        let secret = Data(repeating: 0x37, count: 32)
        let backup = try encryptedBackup(secret, password: "synthetic-password-long")
        #expect(try openBackup(backup, password: "synthetic-password-long") == secret)
        #expect(throws: (any Error).self) { try openBackup(backup, password: "wrong-password-long") }
        var changed = backup
        var sealed = Data(base64Encoded: backup["sealed"] as! String)!
        sealed[20] ^= 1
        changed["sealed"] = sealed.base64EncodedString()
        #expect(throws: (any Error).self) { try openBackup(changed, password: "synthetic-password-long") }
        changed = backup; changed["rounds"] = 1
        #expect(throws: ToolError.self) { try openBackup(changed, password: "synthetic-password-long") }
    }
    @Test func restoreRejectsInvalidStagedClientBeforeMovingCurrent() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let current = directory.appendingPathComponent("current.app")
        let staged = directory.appendingPathComponent("staged.app")
        let previous = directory.appendingPathComponent("previous.app")
        try Data("current".utf8).write(to: current)
        try Data("invalid".utf8).write(to: staged)
        #expect(throws: ToolError.self) {
            try replaceApplication(application: current, staged: staged, previous: previous) { _ in
                throw ToolError.invalid("invalid_signature")
            }
        }
        #expect(try String(contentsOf: current, encoding: .utf8) == "current")
        #expect(!FileManager.default.fileExists(atPath: previous.path))
    }
    @Test func restoreRollsBackAfterPostMoveValidationFailure() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let current = directory.appendingPathComponent("current.app")
        let staged = directory.appendingPathComponent("staged.app")
        let previous = directory.appendingPathComponent("previous.app")
        try Data("previous client".utf8).write(to: current)
        try Data("replacement client".utf8).write(to: staged)
        #expect(throws: ToolError.self) {
            try replaceApplication(application: current, staged: staged, previous: previous) { url in
                if url == current { throw ToolError.invalid("validation_failed") }
            }
        }
        #expect(try String(contentsOf: current, encoding: .utf8) == "previous client")
        #expect(!FileManager.default.fileExists(atPath: previous.path))
    }
    @Test func successfulReplacementRemovesPreviousCopy() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let current = directory.appendingPathComponent("current.app")
        let staged = directory.appendingPathComponent("staged.app")
        let previous = directory.appendingPathComponent("previous.app")
        try Data("old".utf8).write(to: current)
        try Data("official".utf8).write(to: staged)
        try replaceApplication(application: current, staged: staged, previous: previous) { _ in }
        #expect(try String(contentsOf: current, encoding: .utf8) == "official")
        #expect(!FileManager.default.fileExists(atPath: previous.path))
    }
}

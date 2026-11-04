import Testing
import Foundation
@testable import WXKeytool

@Suite struct KeytoolTests {
    @Test func keyValidation() throws {
        #expect(try keyData(String(repeating: "ab", count: 32)).count == 32)
        #expect(throws: ToolError.self) { try keyData("bad") }
        #expect(throws: ToolError.self) { try keyQuery("../../private") }
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

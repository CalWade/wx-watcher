import Testing
import Foundation
import CommonCrypto
import CryptoKit
import GreenBubblesAcquire
@testable import NoteBridgeAccess

@Suite struct CaptureAccountTests {
    private func secret() throws -> PassphraseSecret {
        try PassphraseSecret(bytes: stride(from: 0, to: 32, by: 1).map { UInt8(truncatingIfNeeded: $0 < 16 ? $0 * 17 : ($0 - 16) * 17 + 16) })
    }
    private func page() -> Data {
        // Synthetic known-answer encryption key from the verifier fixture.
        let hex = "558f516983adfb41ebc008ec57bbcca6b87301326710cce41760982d67bd984a"
        let key = stride(from: 0, to: hex.count, by: 2).map { n -> UInt8 in
            let start = hex.index(hex.startIndex, offsetBy: n)
            return UInt8(hex[start..<hex.index(start, offsetBy: 2)], radix: 16)!
        }
        var bytes = [UInt8](repeating: 0x55, count: 4096)
        bytes.replaceSubrange(0..<16, with: (0..<16).map(UInt8.init))
        let salt = bytes[0..<16].map { $0 ^ 0x3a }
        var macKey = [UInt8](repeating: 0, count: 32)
        let status = key.withUnsafeBytes { keyBuffer in
            CCKeyDerivationPBKDF(CCPBKDFAlgorithm(kCCPBKDF2), keyBuffer.bindMemory(to: Int8.self).baseAddress,
                key.count, salt, salt.count, CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA512), 2, &macKey, 32)
        }
        precondition(status == kCCSuccess)
        var authenticated = Data(bytes[16..<4032])
        authenticated.append(contentsOf: [1, 0, 0, 0])
        let mac = HMAC<SHA512>.authenticationCode(for: authenticated, using: SymmetricKey(data: macKey))
        bytes.replaceSubrange(4032..<4096, with: Array(mac))
        return Data(bytes)
    }
    private func temporaryBase() throws -> URL {
        let base = FileManager.default.temporaryDirectory.resolvingSymlinksInPath().appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        return base
    }
    private func put(_ bytes: Data, root: URL) throws {
        try FileManager.default.createDirectory(at: root.appendingPathComponent("contact"), withIntermediateDirectories: true)
        try bytes.write(to: root.appendingPathComponent("contact/contact.db"))
    }
    @Test func firstLoginWaitsForDirectoryAndCompletePageWithoutWritingSource() throws {
        let base = try temporaryBase(); defer { try? FileManager.default.removeItem(at: base) }
        let root = base.appendingPathComponent("new/db_storage")
        // A complete old account must not be chosen before the controlled login.
        try put(page(), root: base.appendingPathComponent("old/db_storage"))
        let bytes = page()
        var tick = 0
        let result = try awaitCaptureDatabase(candidate: secret(), base: base, expected: nil,
            deadline: Date(timeIntervalSince1970: 10), cancelled: { false },
            observe: { tick == 0 ? [] : [root.path] },
            now: { Date(timeIntervalSince1970: Double(tick)) },
            sleep: { _ in
                tick += 1
                if tick == 1 { try! put(Data(bytes.prefix(100)), root: root) }
                if tick == 2 { try! put(bytes, root: root) }
            })
        #expect(tick == 2)
        #expect(result.root.path == root.path)
        #expect(result.verified == 1)
        #expect(try Data(contentsOf: root.appendingPathComponent("contact/contact.db")) == bytes)
        #expect(try FileManager.default.contentsOfDirectory(atPath: root.path) == ["contact"])
    }
    @Test func selectedAccountAndAmbiguousRootsAreRejected() throws {
        let base = try temporaryBase(); defer { try? FileManager.default.removeItem(at: base) }
        let a = base.appendingPathComponent("a/db_storage").path
        let b = base.appendingPathComponent("b/db_storage").path
        #expect(throws: ToolError.invalid("account_changed")) {
            try awaitCaptureDatabase(candidate: secret(), base: base, expected: a,
                deadline: Date().addingTimeInterval(2), cancelled: { false }, observe: { [b] })
        }
        #expect(throws: ToolError.invalid("capture_account_ambiguous")) {
            try awaitCaptureDatabase(candidate: secret(), base: base, expected: nil,
                deadline: Date().addingTimeInterval(2), cancelled: { false }, observe: { [a,b] })
        }
    }
    @Test func cancellationAndProcessExitStopWaitingImmediately() throws {
        let base = try temporaryBase(); defer { try? FileManager.default.removeItem(at: base) }
        #expect(throws: CaptureError.cancelled) {
            try awaitCaptureDatabase(candidate: secret(), base: base, expected: nil,
                deadline: Date().addingTimeInterval(120), cancelled: { true }, observe: { Issue.record("must not inspect after cancel"); return [] })
        }
        #expect(throws: CaptureError.targetExited) {
            try awaitCaptureDatabase(candidate: secret(), base: base, expected: nil,
                deadline: Date().addingTimeInterval(120), cancelled: { false }, observe: { throw CaptureError.targetExited })
        }
    }
    @Test func missingDataAndWrongKeyHaveDistinctBoundedFailures() throws {
        let base = try temporaryBase(); defer { try? FileManager.default.removeItem(at: base) }
        let root = base.appendingPathComponent("new/db_storage")
        var tick = 0
        #expect(throws: ToolError.invalid("database_initialization_timed_out")) {
            try awaitCaptureDatabase(candidate: secret(), base: base, expected: nil,
                deadline: Date(timeIntervalSince1970: 2), cancelled: { false }, observe: { [root.path] },
                now: { Date(timeIntervalSince1970: Double(tick)) }, sleep: { _ in tick += 1 })
        }
        #expect(tick == 2)
        try put(page(), root: root)
        tick = 0
        #expect(throws: ToolError.invalid("candidate_verification_failed")) {
            try awaitCaptureDatabase(candidate: PassphraseSecret(bytes: [UInt8](repeating: 9, count: 32)), base: base, expected: nil,
                deadline: Date(timeIntervalSince1970: 2), cancelled: { false }, observe: { [root.path] },
                now: { Date(timeIntervalSince1970: Double(tick)) }, sleep: { _ in tick += 1 })
        }
        #expect(tick == 2)
    }
    @Test func symlinksAndRootsOutsideUserContainerAreRejected() throws {
        let base = try temporaryBase(); defer { try? FileManager.default.removeItem(at: base) }
        try FileManager.default.createSymbolicLink(atPath: base.appendingPathComponent("alias").path, withDestinationPath: "/private/tmp")
        for path in ["/other/user/db_storage", base.appendingPathComponent("alias/db_storage").path] {
            #expect(throws: ToolError.invalid("unsafe_capture_account")) { try validatedCaptureRoot(path, base: base) }
        }
    }
    @Test func accountChangingDuringVerificationNeverReturnsBinding() throws {
        let base = try temporaryBase(); defer { try? FileManager.default.removeItem(at: base) }
        let root = base.appendingPathComponent("new/db_storage")
        try put(page(), root: root)
        var observations = 0
        #expect(throws: ToolError.invalid("account_changed")) {
            try awaitCaptureDatabase(candidate: secret(), base: base, expected: nil,
                deadline: Date().addingTimeInterval(10), cancelled: { false }, observe: {
                    observations += 1
                    return observations == 1 ? [root.path] : []
                })
        }
    }
}

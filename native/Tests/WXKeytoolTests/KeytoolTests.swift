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
}

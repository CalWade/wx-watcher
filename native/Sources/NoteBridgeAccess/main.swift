import Foundation
import Security
import CryptoKit
import CommonCrypto
import Darwin
import GreenBubblesAcquire

enum ToolError: Error, Equatable { case invalid(String) }
enum CredentialNamespace {
    static let service = "io.github.calwade.notebridge.account-key.v1"
    static let legacy = "io.github.calwade.wx-watcher.account-key.v1"
}

func output(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) { print(text) }
}
func text(_ d: [String: Any], _ name: String) throws -> String {
    guard let v = d[name] as? String, !v.isEmpty else { throw ToolError.invalid("missing_" + name) }
    return v
}
func keyData(_ hex: String) throws -> Data {
    guard hex.utf8.count == 64, hex.utf8.allSatisfy({ (48...57).contains($0) || (65...70).contains($0) || (97...102).contains($0) }) else { throw ToolError.invalid("invalid_key") }
    return Data(stride(from: 0, to: 64, by: 2).map { i in
        let a = hex.index(hex.startIndex, offsetBy: i)
        return UInt8(hex[a..<hex.index(a, offsetBy: 2)], radix: 16)!
    })
}
func keyQuery(_ id: String, serviceSecret: Bool = false, legacy: Bool = false) throws -> [CFString: Any] {
    guard UUID(uuidString: id) != nil else { throw ToolError.invalid("invalid_key_reference") }
    return [kSecClass: kSecClassGenericPassword, kSecAttrService: (legacy ? CredentialNamespace.legacy : CredentialNamespace.service) + (serviceSecret ? ".app-secret" : ""),
            kSecAttrAccount: id]
}
func loadValue(_ id: String, serviceSecret: Bool = false) throws -> Data {
    for legacy in [false, true] {
        var query = try keyQuery(id, serviceSecret: serviceSecret, legacy: legacy)
        query[kSecReturnData] = true
        query[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { continue }
        guard status == errSecSuccess, let data = result as? Data, !data.isEmpty, data.count <= 4096
        else { throw ToolError.invalid("keychain_read_\(status)") }
        if legacy {
            // The OS may ask the user to approve the new executable. Once read,
            // copy into the new namespace under the same opaque reference.
            try saveKey(data, id, serviceSecret: serviceSecret, allowExisting: true)
        }
        return data
    }
    throw ToolError.invalid("keychain_read_\(errSecItemNotFound)")
}
func loadKey(_ id: String) throws -> Data {
    let data = try loadValue(id)
    guard data.count == 32 else { throw ToolError.invalid("invalid_key_length") }
    return data
}
func saveKey(_ data: Data, _ id: String, serviceSecret: Bool = false, allowExisting: Bool = false) throws {
    var query = try keyQuery(id, serviceSecret: serviceSecret)
    query[kSecValueData] = data
    query[kSecAttrLabel] = serviceSecret ? "NoteBridge Feishu app secret" : "NoteBridge account database key"
    let status = SecItemAdd(query as CFDictionary, nil)
    guard status == errSecSuccess || (allowExisting && status == errSecDuplicateItem)
    else { throw ToolError.invalid("keychain_write_\(status)") }
}
func verify(_ data: Data, root: String) throws -> [String: Any] {
    let directory = URL(fileURLWithPath: root)
    guard let handle = opendir(directory.path) else {
        throw ToolError.invalid(errno == EPERM || errno == EACCES ? "database_access_denied" : "database_root_unavailable")
    }
    closedir(handle)
    let contact = directory.appendingPathComponent("contact/contact.db")
    let fd = open(contact.path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW)
    guard fd >= 0 else {
        throw ToolError.invalid(errno == EPERM || errno == EACCES ? "database_access_denied" : "database_contact_missing")
    }
    close(fd)
    let secret = try PassphraseSecret(bytes: Array(data))
    let inventory = try DatabaseSaltInventory(root: directory)
    guard !inventory.entries.isEmpty else { throw ToolError.invalid("no_readable_database_pages") }
    var verified = 0
    for sample in inventory.saltVerificationSamples {
        let key = try SQLCipherKeyVerifier.deriveEncryptionKey(passphrase: secret, salt: sample.salt)
        if try SQLCipherKeyVerifier.verify(encryptionKey: key, page1: sample.page1) { verified += 1 }
    }
    guard verified > 0 else { throw ToolError.invalid("key_does_not_match_database") }
    return ["verified": verified, "total": inventory.distinctSalts.count,
            "all_verified": verified == inventory.distinctSalts.count]
}
func derive(_ password: String, salt: Data) throws -> SymmetricKey {
    guard password.count >= 12, password.utf8.count <= 1024 else { throw ToolError.invalid("backup_password_minimum_12_characters") }
    var result = [UInt8](repeating: 0, count: 32)
    let bytes = Array(password.utf8)
    let status = bytes.withUnsafeBytes { p in salt.withUnsafeBytes { s in
        CCKeyDerivationPBKDF(CCPBKDFAlgorithm(kCCPBKDF2), p.baseAddress?.assumingMemoryBound(to: Int8.self),
            bytes.count, s.baseAddress?.assumingMemoryBound(to: UInt8.self), salt.count,
            CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA512), 256000, &result, 32)
    }}
    guard status == kCCSuccess else { throw ToolError.invalid("backup_kdf_failed") }
    return SymmetricKey(data: result)
}
func random(_ size: Int) throws -> Data {
    var data = Data(count: size)
    guard data.withUnsafeMutableBytes({ SecRandomCopyBytes(kSecRandomDefault, size, $0.baseAddress!) }) == errSecSuccess
    else { throw ToolError.invalid("random_failed") }
    return data
}

func encryptedBackup(_ data: Data, password: String) throws -> [String: Any] {
    guard data.count == 32 else { throw ToolError.invalid("invalid_backup_key") }
    let salt = try random(16)
    let key = try derive(password, salt: salt)
    let sealed = try AES.GCM.seal(data, using: key, authenticating: Data("notebridge-backup-v1".utf8))
    return ["format": "notebridge-backup-v1", "kdf": "PBKDF2-SHA512", "rounds": 256000,
            "salt": salt.base64EncodedString(), "sealed": sealed.combined!.base64EncodedString()]
}
func openBackup(_ backup: [String: Any], password: String) throws -> Data {
    guard let format = backup["format"] as? String,
          ["notebridge-backup-v1", "wx-keytool-backup-v1"].contains(format), backup["kdf"] as? String == "PBKDF2-SHA512",
          backup["rounds"] as? Int == 256000,
          let salt = Data(base64Encoded: try text(backup, "salt")), salt.count == 16,
          let sealed = Data(base64Encoded: try text(backup, "sealed")), sealed.count == 60
    else { throw ToolError.invalid("invalid_backup") }
    return try AES.GCM.open(AES.GCM.SealedBox(combined: sealed), using: derive(password, salt: salt),
                            authenticating: Data(format.utf8))
}

func request(_ input: [String: Any], internalRead: Bool) throws -> [String: Any] {
    let op = try text(input, "operation")
    switch op {
    case "doctor":
        var chain: SecKeychain?
        let defaultStatus = SecKeychainCopyDefault(&chain)
        var keychainStatus: SecKeychainStatus = 0
        let probeStatus = chain.map { SecKeychainGetStatus($0, &keychainStatus) } ?? errSecNoSuchKeychain
        return ["ok": true, "version": "0.3.1", "platform": "macOS",
                "os": ProcessInfo.processInfo.operatingSystemVersionString,
                "lldb": lldbExecutable() ?? "",
                "keychain_service": CredentialNamespace.service, "keychain_default_status": defaultStatus,
                "keychain_probe_status": probeStatus, "keychain_flags": keychainStatus]
    case "capture_preflight":
        return capturePreflight(dbRoot: try text(input, "db_root"), checkApplicationAccess: true)
    case "connection_preflight":
        return capturePreflight(dbRoot: input["db_root"] as? String)
    case "verify":
        let data: Data
        if input["key_ref"] != nil { data = try loadKey(text(input, "key_ref")) }
        else { data = try keyData(text(input, "key_hex")) }
        return try verify(data, root: text(input, "db_root")).merging(["ok": true]) { a, _ in a }
    case "store":
        let data = try keyData(text(input, "key_hex"))
        let report = try verify(data, root: text(input, "db_root"))
        guard report["all_verified"] as? Bool == true else { throw ToolError.invalid("partial_database_verification") }
        let id = UUID().uuidString.lowercased()
        try saveKey(data, id)
        do {
            guard try loadKey(id) == data else { throw ToolError.invalid("keychain_readback_failed") }
        } catch {
            _ = SecItemDelete(try keyQuery(id) as CFDictionary)
            throw error
        }
        return report.merging(["ok": true, "key_ref": id]) { a, _ in a }
    case "read":
        guard internalRead else { throw ToolError.invalid("read_requires_internal_read_flag") }
        let data = try loadKey(text(input, "key_ref"))
        return ["ok": true, "key_hex": data.map { String(format: "%02x", $0) }.joined()]
    case "store_secret":
        let secret = try text(input, "secret")
        guard secret.utf8.count <= 4096 else { throw ToolError.invalid("secret_too_large") }
        let id = UUID().uuidString.lowercased()
        try saveKey(Data(secret.utf8), id, serviceSecret: true)
        return ["ok": true, "key_ref": id]
    case "read_secret":
        guard internalRead else { throw ToolError.invalid("read_requires_internal_read_flag") }
        guard let secret = String(data: try loadValue(text(input, "key_ref"), serviceSecret: true), encoding: .utf8)
        else { throw ToolError.invalid("invalid_secret") }
        return ["ok": true, "secret": secret]
    case "delete":
        for legacy in [false, true] {
            let status = SecItemDelete(try keyQuery(text(input, "key_ref"), legacy: legacy) as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else { throw ToolError.invalid("keychain_\(status)") }
        }
        return ["ok": true]
    case "export_backup":
        let data = try loadKey(text(input, "key_ref"))
        return ["ok": true, "backup": try encryptedBackup(data, password: text(input, "password"))]
    case "import_backup":
        guard let backup = input["backup"] as? [String: Any] else { throw ToolError.invalid("invalid_backup") }
        let data = try openBackup(backup, password: text(input, "password"))
        let report = try verify(data, root: text(input, "db_root"))
        guard report["all_verified"] as? Bool == true else { throw ToolError.invalid("partial_database_verification") }
        let id = UUID().uuidString.lowercased()
        try saveKey(data, id)
        return report.merging(["ok": true, "key_ref": id]) { a, _ in a }
    default: throw ToolError.invalid("unknown_operation")
    }
}

if let index = CommandLine.arguments.firstIndex(of: "--capture-job"), CommandLine.arguments.count > index+1 {
    do { try runCaptureJob(CommandLine.arguments[index+1], recoverOnly: CommandLine.arguments.contains("--recover")) }
    catch let ToolError.invalid(code) { output(["ok": false, "error": code]); exit(2) }
    catch { output(["ok": false, "error": "capture_transaction_failed"]); exit(2) }
} else if CommandLine.arguments.contains("--help") {
    print("notebridge-access 0.3.1 — local WeChat database credentials\nRead one JSON request from stdin: doctor, verify, store, export_backup, import_backup, delete.\nSecret input is never accepted in process arguments. Internal reader use requires --internal-read.")
} else {
    do {
        let raw = FileHandle.standardInput.readDataToEndOfFile()
        guard raw.count <= 1_048_576,
              let input = try JSONSerialization.jsonObject(with: raw) as? [String: Any]
        else { throw ToolError.invalid("invalid_request") }
        output(try request(input, internalRead: CommandLine.arguments.contains("--internal-read")))
    } catch let ToolError.invalid(code) {
        output(["ok": false, "error": code]); exit(2)
    } catch {
        output(["ok": false, "error": "operation_failed", "type": String(describing: type(of: error))]); exit(2)
    }
}

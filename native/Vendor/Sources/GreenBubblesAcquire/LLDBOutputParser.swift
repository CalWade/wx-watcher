// Derived from wcdb-key-tool (https://github.com/TANGandXUE/wcdb-key-tool), MIT license.

import Foundation

/// Parses lldb `memory read --size 1 --count 32 --format x` hexdump output into
/// the captured 32-byte passphrase.
///
/// A byte line looks like `0x16fdfddd8: 0x00 0x11 0x22 0x33 0x44 0x55 0x66 0x77`
/// (lowercase hexadecimal, one address prefix, byte groups only). Any other
/// lldb output is ignored. Returns the first 32 bytes once at least 32 have
/// been collected, or `nil` while the output is still incomplete or malformed.
public enum LLDBOutputParser {
  /// Only fixed stage names cross into the job journal. Never persist raw LLDB
  /// errors, which can contain file paths or captured data.
  public static let setupFailureStages: Set<String> = [
    "configuration", "attach", "target_missing", "attach_denied", "target_not_stopped",
    "breakpoint", "breakpoint_commands", "resume", "listener_setup_failed",
    "attach_timeout", "listener_setup_timeout",
  ]

  public static func detectSetupFailure(in output: String) -> String? {
    let prefix = "RuntimeError: GB_CAPTURE_SETUP_FAILED"
    for raw in output.split(whereSeparator: \.isNewline) {
      let line = raw.trimmingCharacters(in: .whitespaces)
      if line == prefix { return "listener_setup_failed" }
      guard line.hasPrefix(prefix + ":") else { continue }
      let stage = String(line.dropFirst(prefix.count + 1))
      return setupFailureStages.contains(stage) ? stage : "listener_setup_failed"
    }
    return nil
  }

  public static func parsePassphrase(from output: String) -> [UInt8]? {
    var bytes: [UInt8] = []
    for rawLine in output.split(whereSeparator: \.isNewline) {
      let line = rawLine.trimmingCharacters(in: .whitespaces)
      guard let bytesInLine = parseByteLine(line) else { continue }
      bytes.append(contentsOf: bytesInLine)
    }
    guard bytes.count >= 32 else { return nil }
    return Array(bytes.prefix(32))
  }

  /// Detects a failed `process attach` so the capture can fail fast instead of
  /// stalling until the timeout. Returns lldb's error detail when found.
  public static func detectAttachFailure(in output: String) -> String? {
    for rawLine in output.split(whereSeparator: \.isNewline) {
      let line = rawLine.trimmingCharacters(in: .whitespaces)
      guard line.hasPrefix("error:") else { continue }
      let detail = String(line.dropFirst("error:".count))
        .trimmingCharacters(in: .whitespaces)
      if detail.contains("attach") || detail.contains("Not allowed") {
        return detail
      }
    }
    return nil
  }

  /// Detects the target process exiting (or being detached) before the
  /// breakpoint hit, e.g. when WeChat's logout flow restarts its main process.
  public static func detectTargetExit(in output: String) -> Bool {
    for rawLine in output.split(whereSeparator: \.isNewline) {
      let line = rawLine.trimmingCharacters(in: .whitespaces)
      guard line.hasPrefix("Process ") else { continue }
      if line.contains(" exited") || line.contains(" detached") {
        return true
      }
    }
    return false
  }

  /// Whether a line is a `memory read --format x` byte-dump line (the shape
  /// that carries the captured bytes; used to redact verbose output).
  public static func isByteLine(_ line: String) -> Bool {
    parseByteLine(line) != nil
  }

  private static func parseByteLine(_ line: String) -> [UInt8]? {
    guard line.hasPrefix("0x"), let colon = line.firstIndex(of: ":") else { return nil }
    let address = line[line.index(line.startIndex, offsetBy: 2)..<colon]
    guard !address.isEmpty, address.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) else {
      return nil
    }
    let tokens = line[line.index(after: colon)...]
      .split(whereSeparator: \.isWhitespace)
    guard !tokens.isEmpty else { return nil }
    var bytes: [UInt8] = []
    for token in tokens {
      guard token.hasPrefix("0x"), token.count == 4 else { return nil }
      let value = token.dropFirst(2)
      guard value.allSatisfy({ $0.isHexDigit && !$0.isUppercase }),
        let byte = UInt8(value, radix: 16)
      else { return nil }
      bytes.append(byte)
    }
    return bytes
  }
}

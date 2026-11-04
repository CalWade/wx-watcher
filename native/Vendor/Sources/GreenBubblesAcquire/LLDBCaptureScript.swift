// Derived from wcdb-key-tool (https://github.com/TANGandXUE/wcdb-key-tool), MIT license.

import Foundation

public enum LLDBTargetArchitecture: String, Sendable {
  case arm64
  // swift-format-ignore: AlwaysUseLowerCamelCase
  case x86_64

  public static var current: LLDBTargetArchitecture {
    #if arch(arm64)
      return .arm64
    #else
      return .x86_64
    #endif
  }

  /// Register holding `CCKeyDerivationPBKDF`'s `password` argument.
  public var passwordRegister: String {
    switch self {
    case .arm64: return "x1"
    case .x86_64: return "rsi"
    }
  }

  /// Register holding `CCKeyDerivationPBKDF`'s `passwordLen` argument.
  public var lengthRegister: String {
    switch self {
    case .arm64: return "x2"
    case .x86_64: return "rdx"
    }
  }

  /// Avoid unrelated 32-byte inputs to CommonCrypto. These are the
  /// SQLCipher4 account-key derivation parameters already used by the verifier.
  public var databaseKeyCondition: String {
    switch self {
    case .arm64:
      return "$x2 == 32 && $x0 == 2 && $x4 == 16 && $x5 == 5 && $x6 == 256000"
    case .x86_64:
      return "$rdx == 32 && $rdi == 2 && $r8 == 16 && $r9 == 5"
    }
  }
}

/// Generates the lldb command script whose ordering was validated live against
/// the pinned WeChat build: attach, break on the system `CCKeyDerivationPBKDF`
/// symbol when `passwordLen == 32`, read the 32-byte passphrase from the
/// password register when the breakpoint hits, then detach and quit.
public enum LLDBCaptureScript {
  public static func script(
    processID: pid_t,
    architecture: LLDBTargetArchitecture = .current
  ) -> String {
    """
    settings set target.preload-symbols false
    settings set target.process.stop-on-exec false
    settings set interpreter.prompt-on-quit false
    process attach -p \(processID)
    script print("GB_CAPTURE_ATTACHED")
    breakpoint set -n CCKeyDerivationPBKDF -c '\(architecture.databaseKeyCondition)'
    breakpoint command add 1
    memory read --size 1 --count 32 --format x $\(architecture.passwordRegister)
    detach
    quit
    DONE
    script print("GB_CAPTURE_ARMED")
    process continue

    """
  }
}

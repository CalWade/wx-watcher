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

/// Generates checked LLDB setup for an existing, optionally suspended target.
/// The suspended first-call ordering is tested against a disposable program;
/// real-client compatibility is recorded separately in docs/VALIDATION.md.
public enum LLDBCaptureScript {
  public static func script(
    processID: pid_t,
    architecture: LLDBTargetArchitecture = .current
  ) -> String {
    // Execute setup as one checked Python transaction. An LLDB command error
    // must not fall through to `continue` or publish a false ready marker.
    let setup = """
    import lldb
    def checked(command, stage):
        result = lldb.SBCommandReturnObject()
        lldb.debugger.GetCommandInterpreter().HandleCommand(command, result)
        if not result.Succeeded():
            detail = (result.GetError() or "").lower()
            if stage == "attach" and any(s in detail for s in ("no such process", "does not exist", "doesn't exist")):
                stage = "target_missing"
            elif stage == "attach" and any(s in detail for s in ("not allowed", "permission", "not permitted")):
                stage = "attach_denied"
            raise RuntimeError("GB_CAPTURE_SETUP_FAILED:" + stage)
    checked("settings set target.preload-symbols false", "configuration")
    checked("settings set target.process.stop-on-exec false", "configuration")
    checked("settings set interpreter.prompt-on-quit false", "configuration")
    lldb.debugger.SetAsync(False)
    checked("process attach -p \(processID)", "attach")
    target = lldb.debugger.GetSelectedTarget()
    if target.GetProcess().GetState() != lldb.eStateStopped:
        raise RuntimeError("GB_CAPTURE_SETUP_FAILED:target_not_stopped")
    print("GB_CAPTURE_ATTACHED", flush=True)
    bp = target.BreakpointCreateByName("CCKeyDerivationPBKDF")
    if not bp.IsValid() or not bp.IsEnabled():
        raise RuntimeError("GB_CAPTURE_SETUP_FAILED:breakpoint")
    bp.SetCondition("\(architecture.databaseKeyCondition)")
    commands = lldb.SBStringList()
    commands.AppendString("memory read --size 1 --count 32 --format x $\(architecture.passwordRegister)")
    commands.AppendString("detach")
    commands.AppendString("quit")
    bp.SetCommandLineCommands(commands)
    installed = lldb.SBStringList()
    bp.GetCommandLineCommands(installed)
    if installed.GetSize() != 3:
        raise RuntimeError("GB_CAPTURE_SETUP_FAILED:breakpoint_commands")
    print("GB_CAPTURE_ARMED", flush=True)
    lldb.debugger.SetAsync(True)
    checked("process continue", "resume")
    print("GB_CAPTURE_RUNNING", flush=True)
    """
    // JSON string literals are also valid Python strings for this ASCII script.
    let encoded = String(data: try! JSONSerialization.data(withJSONObject: setup, options: [.fragmentsAllowed]), encoding: .utf8)!
    return "script exec(\(encoded))\n"
  }
}

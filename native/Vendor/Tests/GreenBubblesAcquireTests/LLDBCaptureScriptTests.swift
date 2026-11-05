import Foundation
import Testing
@testable import GreenBubblesAcquire

struct LLDBCaptureScriptTests {
  @Test func setupIsCheckedAndResumeIsLast() throws {
    let script = LLDBCaptureScript.script(processID: 4242)
    let encoded = String(script.dropFirst("script exec(".count).dropLast(2)).trimmingCharacters(in: .newlines)
    let python = try #require(JSONSerialization.jsonObject(with: Data(encoded.utf8), options: [.fragmentsAllowed]) as? String)
    let steps = ["process attach -p 4242", "GB_CAPTURE_ATTACHED", "BreakpointCreateByName", "SetCommandLineCommands", "GetCommandLineCommands", "GB_CAPTURE_ARMED", "checked(\"process continue\")", "GB_CAPTURE_RUNNING"]
    var offset = python.startIndex
    for step in steps {
      let found = try #require(python.range(of: step, range: offset..<python.endIndex))
      offset = found.upperBound
    }
    #expect(python.contains("raise RuntimeError"))
    #expect(python.contains("GetState() != lldb.eStateStopped"))
  }
  @Test func registersMatchArchitecture() {
    #expect(LLDBTargetArchitecture.arm64.passwordRegister == "x1")
    #expect(LLDBTargetArchitecture.x86_64.passwordRegister == "rsi")
    #expect(LLDBCaptureScript.script(processID: 1, architecture: .arm64).contains("$x6 == 256000"))
    #expect(LLDBCaptureScript.script(processID: 1, architecture: .x86_64).contains("$rsi"))
  }
}

import Foundation

/// Stage and validate the replacement before moving the current client. If the final
/// validation fails, put the previous client back. Paths are fixed by CaptureFlow.
func replaceApplication(
    application: URL, staged: URL, previous: URL,
    validate: (URL) throws -> Void
) throws {
    let fm = FileManager.default
    guard !fm.fileExists(atPath: previous.path),
          staged.resolvingSymlinksInPath() == staged.standardizedFileURL,
          application.resolvingSymlinksInPath() == application.standardizedFileURL
    else { throw ToolError.invalid("unsafe_restore_path") }
    try validate(staged)
    let hadApplication = fm.fileExists(atPath: application.path)
    if hadApplication { try fm.moveItem(at: application, to: previous) }
    do {
        try fm.moveItem(at: staged, to: application)
        try validate(application)
    } catch {
        // Keep the previous client available even if rollback itself cannot finish.
        if fm.fileExists(atPath: application.path) { try? fm.removeItem(at: application) }
        if hadApplication { try? fm.moveItem(at: previous, to: application) }
        throw error
    }
    if hadApplication { try? fm.removeItem(at: previous) }
}

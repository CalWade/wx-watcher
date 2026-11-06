import Foundation

/// Only the composed loopback backend may invoke native UI operations.
public struct TrustedInterface: Equatable, Sendable {
    public let port: Int

    public init?(url: URL) {
        guard url.scheme?.lowercased() == "http", url.host == "127.0.0.1",
              url.user == nil, url.password == nil else { return nil }
        let port = url.port ?? 80
        guard (1...65535).contains(port) else { return nil }
        self.port = port
    }

    public func allowsNavigation(to url: URL) -> Bool {
        if url.absoluteString == "about:blank" { return true }
        return TrustedInterface(url: url) == self
    }

    public func accepts(scheme: String, host: String, port: Int, mainFrame: Bool) -> Bool {
        mainFrame && scheme.lowercased() == "http" && host == "127.0.0.1" && port == self.port
    }
}

public struct NativeInterfaceRequest: Sendable {
    public enum Kind: String, Sendable {
        case openAppManagement = "open-app-management"
        case openPrivacy = "open-privacy"
        case chooseFile = "choose-file"
        case chooseDirectory = "choose-directory"
    }
    public let kind: Kind
    public let id: String

    public init?(body: Any) {
        guard let request = body as? [String: Any],
              let kind = request["kind"] as? String,
              let parsedKind = Kind(rawValue: kind),
              let id = request["id"] as? String,
              !id.isEmpty, id.utf8.count <= 80,
              id.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 })
        else { return nil }
        self.kind = parsedKind
        self.id = id
    }
}

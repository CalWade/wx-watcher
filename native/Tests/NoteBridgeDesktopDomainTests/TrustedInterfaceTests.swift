import Foundation
import Testing
@testable import NoteBridgeDesktopDomain

@Suite struct TrustedInterfaceTests {
    @Test func onlyAnExplicitLoopbackHttpOriginCanOwnTheInterface() throws {
        let origin = try #require(TrustedInterface(url: URL(string: "http://127.0.0.1:12345/#token=synthetic")!))
        #expect(origin.port == 12345)
        for value in ["https://127.0.0.1:12345", "http://localhost:12345", "http://example.com:12345", "http://user@127.0.0.1:12345", "file:///tmp/page"] {
            #expect(TrustedInterface(url: URL(string: value)!) == nil)
        }
        #expect(TrustedInterface(url: URL(string: "http://127.0.0.1/")!)?.port == 80)
    }

    @Test func navigationAndBridgeMessagesStayWithinTheSameOrigin() throws {
        let origin = try #require(TrustedInterface(url: URL(string: "http://127.0.0.1:12345")!))
        #expect(origin.allowsNavigation(to: URL(string: "about:blank")!))
        #expect(origin.allowsNavigation(to: URL(string: "http://127.0.0.1:12345/page")!))
        #expect(!origin.allowsNavigation(to: URL(string: "http://127.0.0.1:12346")!))
        #expect(!origin.allowsNavigation(to: URL(string: "https://127.0.0.1:12345")!))
        #expect(origin.accepts(scheme: "http", host: "127.0.0.1", port: 12345, mainFrame: true))
        #expect(!origin.accepts(scheme: "https", host: "127.0.0.1", port: 12345, mainFrame: true))
        #expect(!origin.accepts(scheme: "http", host: "127.0.0.1", port: 12345, mainFrame: false))
        #expect(!origin.accepts(scheme: "http", host: "localhost", port: 12345, mainFrame: true))
        #expect(!origin.accepts(scheme: "http", host: "127.0.0.1", port: 12346, mainFrame: true))
    }

    @Test func nativeRequestsHaveBoundedKnownKindsAndSafeIdentifiers() throws {
        for kind in ["open-app-management", "open-privacy", "choose-file", "choose-directory"] {
            let request = try #require(NativeInterfaceRequest(body: ["kind": kind, "id": "fixture-123"]))
            #expect(request.kind.rawValue == kind)
            #expect(request.id == "fixture-123")
        }
        for body: Any in [[:], ["kind": "execute", "id": "ok"], ["kind": "choose-file", "id": ""], ["kind": "choose-file", "id": "');alert(1)"], ["kind": "choose-file", "id": String(repeating: "a", count: 81)], ["kind": "choose-file", "id": 123], "invalid"] {
            #expect(NativeInterfaceRequest(body: body) == nil)
        }
    }
}

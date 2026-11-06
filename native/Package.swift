// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "NoteBridge", platforms: [.macOS(.v14)],
    products: [
        .executable(name: "notebridge-access", targets: ["NoteBridgeAccess"]),
        .executable(name: "notebridge-app", targets: ["NoteBridgeApp"]),
    ],
    targets: [
        .target(name: "NoteBridgeDesktopDomain"),
        .testTarget(name: "NoteBridgeDesktopDomainTests", dependencies: ["NoteBridgeDesktopDomain"]),
        .target(name: "GreenBubblesAcquire", path: "Vendor/Sources/GreenBubblesAcquire"),
        .executableTarget(name: "NoteBridgeAccess", dependencies: ["GreenBubblesAcquire"],
                          linkerSettings: [.linkedFramework("Security")]),
        .executableTarget(name: "NoteBridgeApp", dependencies: ["NoteBridgeDesktopDomain"], linkerSettings: [
            .linkedFramework("AppKit"), .linkedFramework("WebKit")]),
        .testTarget(name: "GreenBubblesAcquireTests", dependencies: ["GreenBubblesAcquire"],
                    path: "Vendor/Tests/GreenBubblesAcquireTests"),
        .testTarget(name: "NoteBridgeAccessTests", dependencies: ["NoteBridgeAccess"]),
    ]
)

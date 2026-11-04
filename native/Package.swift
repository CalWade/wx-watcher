// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "WXWatcher", platforms: [.macOS(.v14)],
    products: [
        .executable(name: "wx-keytool", targets: ["WXKeytool"]),
        .executable(name: "wx-watcher-app", targets: ["WXWatcherApp"]),
    ],
    targets: [
        .target(name: "GreenBubblesAcquire", path: "Vendor/Sources/GreenBubblesAcquire"),
        .executableTarget(name: "WXKeytool", dependencies: ["GreenBubblesAcquire"],
                          linkerSettings: [.linkedFramework("Security")]),
        .executableTarget(name: "WXWatcherApp", linkerSettings: [
            .linkedFramework("AppKit"), .linkedFramework("WebKit")]),
        .testTarget(name: "GreenBubblesAcquireTests", dependencies: ["GreenBubblesAcquire"],
                    path: "Vendor/Tests/GreenBubblesAcquireTests"),
        .testTarget(name: "WXKeytoolTests", dependencies: ["WXKeytool"]),
    ]
)

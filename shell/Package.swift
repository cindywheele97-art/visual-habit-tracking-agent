// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "GlimpseShell",
    platforms: [.macOS(.v14)],
    targets: [
        .target(
            name: "GlimpseShellLib",
            path: "Sources/GlimpseShellLib"
        ),
        .executableTarget(
            name: "GlimpseShell",
            dependencies: ["GlimpseShellLib"],
            path: "Sources/GlimpseShell"
        ),
        .testTarget(
            name: "GlimpseShellTests",
            dependencies: ["GlimpseShellLib"],
            path: "Tests/GlimpseShellTests"
        ),
    ]
)


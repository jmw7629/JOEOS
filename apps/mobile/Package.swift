// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "JoeOSMobilePolicy",
    platforms: [
        .iOS(.v17),
        .macOS(.v13),
    ],
    products: [
        .library(name: "JoeOSCore", targets: ["JoeOSCore"]),
    ],
    targets: [
        .target(
            name: "JoeOSCore",
            path: "Sources/JoeOSCore"
        ),
        .testTarget(
            name: "JoeOSCoreTests",
            dependencies: ["JoeOSCore"],
            path: "Tests/JoeOSCoreTests"
        ),
    ]
)

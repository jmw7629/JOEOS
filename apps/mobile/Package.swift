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
        .library(name: "JoeOSIntelligence", targets: ["JoeOSIntelligence"]),
    ],
    targets: [
        .target(
            name: "JoeOSCore",
            path: "Sources/JoeOSCore"
        ),
        .target(
            name: "JoeOSIntelligence",
            dependencies: ["JoeOSCore"],
            path: "Sources/JoeOSIntelligence"
        ),
        .testTarget(
            name: "JoeOSCoreTests",
            dependencies: ["JoeOSCore"],
            path: "Tests/JoeOSCoreTests"
        ),
        .testTarget(
            name: "JoeOSIntelligenceTests",
            dependencies: ["JoeOSIntelligence", "JoeOSCore"],
            path: "Tests/JoeOSIntelligenceTests"
        ),
    ]
)

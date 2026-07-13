// swift-tools-version: 6.0
// Tool package that regenerates the committed Swift API client from the
// shared OpenAPI contract. Not part of the app build — invoked by
// scripts/generate-swift-client.sh (and the ios.yml CI drift check).
import PackageDescription

let package = Package(
    name: "openapi-generator-tool",
    platforms: [.macOS(.v13)],
    dependencies: [
        // Pinned exactly so regeneration is reproducible across machines/CI;
        // bump deliberately alongside a client regeneration commit.
        .package(url: "https://github.com/apple/swift-openapi-generator", exact: "1.10.3")
    ]
)

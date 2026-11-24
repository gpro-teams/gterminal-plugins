// swift-tools-version:5.9
// gTerminal 独立进程插件的 Swift 模板（API v1，MIT）。复制这个目录，改清单里的 id、名字，swift build -c release。
import PackageDescription

let package = Package(
    name: "hello",
    platforms: [.macOS(.v13)],
    targets: [.executableTarget(name: "hello")]
)

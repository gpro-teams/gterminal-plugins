// gTerminal 独立进程插件的 Swift 模板（API v1，MIT）。
// 一页：一个清单（存在宿主替插件存的 storage 里）、点一行进它的子页、底下一张表加一项、全部清掉（带确认）。
// 协议见 sdk/README.md：stdin / stdout 一行一条 JSON-RPC 2.0，调试输出写 stderr（stdout 上只能写协议）。
// 只用 Foundation，不依赖 gTerminal 的代码。改成你自己的：换掉清单里的 id、名字，改 page / act 两个函数。
import Foundation

typealias Obj = [String: Any]

// MARK: - 收发

func send(_ msg: Obj) {
    guard var d = try? JSONSerialization.data(withJSONObject: msg) else { return }
    d.append(0x0A)
    FileHandle.standardOutput.write(d)
}

/// 调试输出：写 stderr。插件出错时宿主把最后几行写在它的页面上
func debug(_ s: String) {
    FileHandle.standardError.write(Data((s + "\n").utf8))
}

func readMessage() -> Obj? {
    while let line = readLine() {
        if let m = (try? JSONSerialization.jsonObject(with: Data(line.utf8))) as? Obj { return m }
        debug("stdin 上有一行不是 JSON，跳过")
    }
    return nil
}

/// 等自己请求的回包时读到的宿主消息，先攒着，回到主循环再处理
var queued: [Obj] = []
var nextID = 0

/// 调宿主（storage.get / set、ui.openPage）：发出去，等同一个 id 的回包。插件发的 id 用字符串，和宿主的数字 id 分开
@discardableResult
func callHost(_ method: String, _ params: Obj) -> Obj? {
    nextID += 1
    let id = "p\(nextID)"
    send(["jsonrpc": "2.0", "id": id, "method": method, "params": params])
    while let m = readMessage() {
        if m["method"] == nil, m["id"] as? String == id {
            if let e = m["error"] as? Obj { debug("\(method) 回了错误：\(e)") }
            return m["result"] as? Obj
        }
        queued.append(m)
    }
    return nil
}

// MARK: - 状态（宿主替插件存：storage 的值只能是字，这里存一段 JSON）

var items: [String] = []

func load() {
    guard let s = callHost("storage.get", ["key": "items"])?["value"] as? String,
        let a = (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [String]
    else { return }
    items = a
}

func save() {
    let d = (try? JSONSerialization.data(withJSONObject: items)) ?? Data("[]".utf8)
    callHost("storage.set", ["key": "items", "value": String(decoding: d, as: UTF8.self)])
}

// MARK: - 页面（组件见 sdk/api/v1/page.schema.json）

/// path：""（首页）、"item/<名字>"（子页）
func page(_ path: String) -> Obj {
    if path.hasPrefix("item/") {
        let name = String(path.dropFirst(5))
        return [
            "title": name,
            "nodes": [
                ["type": "note", "text": "这是子页，path 是 \(path)。按左上角返回回到首页。"],
                [
                    "type": "button", "id": "remove", "title": "删掉这一项", "danger": true, "action": ["id": "remove", "value": name],
                    "confirm": ["text": "删掉「\(name)」？", "confirmTitle": "删掉", "danger": true],
                ],
            ],
        ]
    }
    var nodes: [Obj] = [["type": "caption", "text": items.isEmpty ? "还没有，先在下面加一项" : "\(items.count) 项"]]
    for (i, name) in items.enumerated() {
        nodes.append(["type": "row", "row": ["id": "item\(i)", "title": name, "tap": ["id": "open", "value": name]]])
    }
    nodes.append([
        "type": "form", "id": "add",
        "rows": [[["type": "field", "id": "name", "label": "名字"]]],
        "submit": ["id": "add.submit", "title": "加一项", "primary": true, "action": ["id": "add"]],
    ])
    if !items.isEmpty {
        nodes.append([
            "type": "button", "id": "clear", "title": "全部清掉", "danger": true, "action": ["id": "clear"],
            "confirm": ["text": "\(items.count) 项都清掉？", "confirmTitle": "清掉", "danger": true],
        ])
    }
    return ["title": "Hello", "nodes": nodes]
}

/// 页面上点了什么。返回以后宿主自己重画当前页；要换一页就在这里调 ui.openPage（只有处理 page.action 时能调）
func act(_ action: Obj) {
    let value = action["value"] as? String ?? ""
    switch action["id"] as? String {
    case "open":
        callHost("ui.openPage", ["path": "item/\(value)"])
    case "add":
        // 表单提交：inputs 是这张表的值（字段 id → 字）
        let name = ((action["inputs"] as? [String: String])?["name"] ?? "").trimmingCharacters(in: .whitespaces)
        if !name.isEmpty, !items.contains(name) {
            items.append(name)
            save()
        }
    case "remove":
        items.removeAll { $0 == value }
        save()
        callHost("ui.openPage", ["path": ""])
    case "clear":
        items = []
        save()
    default:
        debug("不认识的动作：\(action)")
    }
}

// MARK: - 主循环

func handle(_ method: String, _ params: Obj) -> Obj? {
    switch method {
    case "initialize":
        load()
        return ["api": "1"]
    case "page.render":
        return page(params["path"] as? String ?? "")
    case "page.action":
        act(params["action"] as? Obj ?? [:])
        return [:]
    case "settings.status":
        return ["text": items.isEmpty ? "还没有" : "\(items.count) 项"]
    case "settings.setOn":
        return [:]
    default:
        return nil
    }
}

func next() -> Obj? { queued.isEmpty ? readMessage() : queued.removeFirst() }

while let m = next() {
    guard let method = m["method"] as? String else { continue }  // 晚到的回包
    if method == "shutdown" { break }
    guard let id = m["id"] else { continue }  // 别的通知（events.quota 这类，这个模板没订）
    if let r = handle(method, m["params"] as? Obj ?? [:]) {
        send(["jsonrpc": "2.0", "id": id, "result": r])
    } else {
        send(["jsonrpc": "2.0", "id": id, "error": ["code": -32601, "message": "没有这个方法：\(method)"]])
    }
}

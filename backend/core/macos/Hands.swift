// `Jarvis --hands`: windows and controls of other apps through macOS Accessibility, for the
// assistant's hands worker (workers/hands/mac.py). Running inside Jarvis.app means macOS asks once
// to allow "Jarvis" in Privacy & Security → Accessibility. One JSON object per line:
//   in:  {"id":1,"op":"windows"}  {"id":2,"op":"elements","window_id":"w3","query":"save","limit":50} …
//   out: {"id":1,"ok":true,"result":…}  {"id":2,"ok":false,"error":"…"}
// Handles ("w3", "e17") stay valid while this process runs; the worker maps them to short ids.
import AppKit
import ApplicationServices

struct HandsError: Error {
    let message: String
}

/// JSON null for a missing value.
func orNull(_ value: Any?) -> Any { value ?? NSNull() }

final class HandsServer {
    var handles: [String: AXUIElement] = [:]
    private var known: [AXUIElement: String] = [:]
    private var order: [String] = []
    private var counter = 0
    private var exposed: Set<pid_t> = []
    private let maxHandles = 5000
    let systemWide = AXUIElementCreateSystemWide()

    func run() -> Never {
        AXUIElementSetMessagingTimeout(systemWide, 2.0)  // a frozen app can't hang the assistant
        Thread.detachNewThread {
            while let line = readLine(strippingNewline: true) {
                DispatchQueue.main.async { self.serve(line) }
            }
            DispatchQueue.main.async { exit(0) }  // input closed: done, after answering what's queued
        }
        // A run loop with nothing registered returns at once, which would end the helper before it
        // answered anything: the timer keeps it running (and NSWorkspace's app list current).
        Timer.scheduledTimer(withTimeInterval: 3600, repeats: true) { _ in }
        RunLoop.main.run()
        exit(0)
    }

    private func serve(_ line: String) {
        guard let data = line.data(using: .utf8),
              let message = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let id = message["id"] as? Int, let op = message["op"] as? String else { return }
        var reply: [String: Any] = ["id": id]
        do {
            reply["result"] = try handle(op, message)
            reply["ok"] = true
        } catch let error as HandsError {
            reply["ok"] = false
            reply["error"] = error.message
        } catch {
            reply["ok"] = false
            reply["error"] = "\(error)"
        }
        guard let out = try? JSONSerialization.data(withJSONObject: reply) else { return }
        FileHandle.standardOutput.write(out)
        FileHandle.standardOutput.write(Data([0x0A]))
    }

    private func handle(_ op: String, _ m: [String: Any]) throws -> Any {
        if op == "trusted" {
            let prompt = (m["prompt"] as? Bool) ?? false
            let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: prompt] as CFDictionary
            return ["trusted": AXIsProcessTrustedWithOptions(options)]
        }
        guard AXIsProcessTrusted() else {
            throw HandsError(message: "Jarvis isn't allowed to control this Mac. In System Settings → Privacy & "
                + "Security → Accessibility: if Jarvis is there (even switched on, after an update), select it, "
                + "remove it with the minus button, add it again with + (your Applications folder → Jarvis), then reopen Jarvis; "
                + "if it isn't there, add it with +")
        }
        switch op {
        case "windows": return windows()
        case "elements":
            return try elements(of: try element(m["window_id"]), query: (m["query"] as? String) ?? "",
                                limit: (m["limit"] as? Int) ?? 50)
        case "describe": return info(try element(m["element_id"]), depth: 0)
        case "press": return try press(try element(m["element_id"]))
        case "set_value":
            return try setValue(try element(m["element_id"]), (m["text"] as? String) ?? "",
                                append: (m["mode"] as? String) == "append")
        case "focus": return focus(try element(m["window_id"]))
        case "key":
            return try key(window: try element(m["window_id"]), code: (m["keycode"] as? Int) ?? -1,
                           flags: (m["flags"] as? [String]) ?? [])
        case "scroll":
            return try scroll(window: try element(m["window_id"]), dx: (m["dx"] as? Int) ?? 0,
                              dy: (m["dy"] as? Int) ?? 0)
        case "menu": return try menu(window: try element(m["window_id"]), path: (m["path"] as? [String]) ?? [])
        case "move_resize": return try moveResize(try element(m["window_id"]), (m["bounds"] as? [String: Int]) ?? [:])
        case "insert_text": return try insertText((m["text"] as? String) ?? "")
        case "focused":
            guard let el = focusedElement() else { return NSNull() }
            return info(el, depth: 0)
        case "selected_text":
            guard let el = focusedElement(), !isSecure(el) else { return "" }
            return string(el, kAXSelectedTextAttribute) ?? ""
        case "front_app": return frontApp()?.localizedName ?? ""
        case "visible_apps": return visibleApps()
        case "click":
            return click(x: (m["x"] as? Int) ?? 0, y: (m["y"] as? Int) ?? 0, button: (m["button"] as? String) ?? "left")
        default: throw HandsError(message: "unknown operation \(op)")
        }
    }

    // ---- handles ------------------------------------------------------------------------

    func ref(_ el: AXUIElement, _ prefix: String) -> String {
        if let id = known[el] { return id }
        counter += 1
        let id = "\(prefix)\(counter)"
        handles[id] = el
        known[el] = id
        order.append(id)
        if order.count > maxHandles {
            let old = order.removeFirst()
            if let gone = handles.removeValue(forKey: old) { known[gone] = nil }
        }
        return id
    }

    func element(_ raw: Any?) throws -> AXUIElement {
        guard let id = raw as? String, let el = handles[id] else {
            throw HandsError(message: "that window or control is gone; list it again")
        }
        return el
    }

    // ---- reading ------------------------------------------------------------------------

    func attribute(_ el: AXUIElement, _ name: String) -> AnyObject? {
        var value: AnyObject?
        return AXUIElementCopyAttributeValue(el, name as CFString, &value) == .success ? value : nil
    }

    func string(_ el: AXUIElement, _ name: String) -> String? { attribute(el, name) as? String }

    func flag(_ el: AXUIElement, _ name: String) -> Bool? { (attribute(el, name) as? NSNumber)?.boolValue }

    func children(_ el: AXUIElement) -> [AXUIElement] {
        (attribute(el, kAXChildrenAttribute) as? [AXUIElement]) ?? []
    }

    func pid(_ el: AXUIElement) -> pid_t {
        var pid: pid_t = 0
        AXUIElementGetPid(el, &pid)
        return pid
    }

    func frame(_ el: AXUIElement) -> [String: Int]? {
        guard let p = attribute(el, kAXPositionAttribute), let s = attribute(el, kAXSizeAttribute),
              CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return nil }
        var point = CGPoint.zero
        var size = CGSize.zero
        guard AXValueGetValue(p as! AXValue, .cgPoint, &point),  // swiftlint:disable:this force_cast
              AXValueGetValue(s as! AXValue, .cgSize, &size) else { return nil }  // swiftlint:disable:this force_cast
        return ["x": Int(point.x), "y": Int(point.y), "width": Int(size.width), "height": Int(size.height)]
    }

    func isSecure(_ el: AXUIElement) -> Bool { string(el, kAXSubroleAttribute) == "AXSecureTextField" }

    func focusedElement() -> AXUIElement? {
        guard let el = attribute(systemWide, kAXFocusedUIElementAttribute),
              CFGetTypeID(el) == AXUIElementGetTypeID() else { return nil }
        return (el as! AXUIElement)  // swiftlint:disable:this force_cast
    }

    func frontApp() -> NSRunningApplication? {
        guard let app = attribute(systemWide, kAXFocusedApplicationAttribute),
              CFGetTypeID(app) == AXUIElementGetTypeID() else { return NSWorkspace.shared.frontmostApplication }
        return NSRunningApplication(processIdentifier: pid(app as! AXUIElement))  // swiftlint:disable:this force_cast
    }

    private func windows() -> [[String: Any]] {
        let front = frontApp()?.processIdentifier
        var found: [[String: Any]] = []
        for app in NSWorkspace.shared.runningApplications where app.activationPolicy == .regular {
            let axApp = AXUIElementCreateApplication(app.processIdentifier)
            let focused = attribute(axApp, kAXFocusedWindowAttribute)
            for win in (attribute(axApp, kAXWindowsAttribute) as? [AXUIElement]) ?? [] {
                let isFocused = app.processIdentifier == front && focused != nil && CFEqual(focused, win)
                found.append([
                    "window_id": ref(win, "w"), "app": app.localizedName ?? "",
                    "title": string(win, kAXTitleAttribute) ?? "", "bounds": orNull(frame(win)),
                    "focused": isFocused, "on_screen": !(flag(win, kAXMinimizedAttribute) ?? false),
                ])
            }
        }
        return found
    }

    private func visibleApps() -> [String] {
        NSWorkspace.shared.runningApplications.compactMap { app in
            guard app.activationPolicy == .regular, !app.isHidden else { return nil }
            let wins = (attribute(AXUIElementCreateApplication(app.processIdentifier), kAXWindowsAttribute)
                as? [AXUIElement]) ?? []
            return wins.contains { !(flag($0, kAXMinimizedAttribute) ?? false) } ? app.localizedName : nil
        }
    }

    private static let roleNames = [
        "AXButton": "button", "AXTextField": "text field", "AXTextArea": "text area", "AXCheckBox": "check box",
        "AXRadioButton": "radio button", "AXLink": "link", "AXMenuItem": "menu item", "AXPopUpButton": "pop-up button",
        "AXStaticText": "text", "AXComboBox": "combo box", "AXTabGroup": "tab group", "AXImage": "image",
        "AXRow": "row", "AXCell": "cell", "AXSlider": "slider", "AXSearchField": "search field",
        "AXMenuButton": "menu button", "AXList": "list", "AXTable": "table", "AXWebArea": "web page",
    ]
    private static let textRoles: Set = ["AXTextField", "AXTextArea", "AXComboBox", "AXSearchField", "AXStaticText"]

    func info(_ el: AXUIElement, depth: Int) -> [String: Any] {
        let role = string(el, kAXRoleAttribute) ?? ""
        let secure = isSecure(el)
        let value = secure ? nil : string(el, kAXValueAttribute)
        let labels = [string(el, kAXTitleAttribute), string(el, kAXDescriptionAttribute),
                      role == "AXStaticText" ? value : nil, string(el, "AXPlaceholderValue"), string(el, kAXHelpAttribute)]
        var states: [String] = []
        if flag(el, kAXEnabledAttribute) == false { states.append("disabled") }
        if flag(el, kAXFocusedAttribute) == true { states.append("focused") }
        if flag(el, kAXSelectedAttribute) == true { states.append("selected") }
        if secure { states.append("password") }
        var names: CFArray?
        let actions = AXUIElementCopyActionNames(el, &names) == .success ? ((names as? [String]) ?? []) : []
        let fallback = role.hasPrefix("AX") ? String(role.dropFirst(2)).lowercased() : role.lowercased()
        return [
            "element_id": ref(el, "e"), "role": Self.roleNames[role] ?? fallback,
            "name": labels.compactMap { $0 }.first { !$0.isEmpty } ?? "",
            "text": orNull(Self.textRoles.contains(role) && !secure ? value : nil),
            "states": states, "actions": actions.map { $0.hasPrefix("AX") ? String($0.dropFirst(2)).lowercased() : $0 },
            "bounds": orNull(frame(el)), "depth": depth,
        ]
    }

    /// Chrome, Edge, Brave and Electron apps (Slack, Discord, Teams, WhatsApp…) build the
    /// accessibility of their pages only when asked; until then a web page is one empty box.
    /// Asked once per app; apps that don't know the attribute ignore it.
    private func exposeContent(of window: AXUIElement) {
        let owner = pid(window)
        guard owner > 0, !exposed.contains(owner) else { return }
        exposed.insert(owner)
        let app = AXUIElementCreateApplication(owner)
        if AXUIElementSetAttributeValue(app, "AXManualAccessibility" as CFString, kCFBooleanTrue) == .success {
            Thread.sleep(forTimeInterval: 0.4)  // the page's tree is built on the app's side
        }
    }

    private func elements(of window: AXUIElement, query: String, limit: Int) throws -> [[String: Any]] {
        exposeContent(of: window)
        let wanted = query.lowercased()
        var queue: [(AXUIElement, Int)] = [(window, 0)]
        var next = 0
        var found: [[String: Any]] = []
        while next < queue.count && found.count < limit && next < 4000 {
            let (el, depth) = queue[next]
            next += 1
            if depth > 0 {
                let item = info(el, depth: depth)
                let name = item["name"] as? String ?? ""
                let actions = item["actions"] as? [String] ?? []
                let text = item["text"] as? String ?? ""
                let useful = !name.isEmpty || !text.isEmpty || actions.contains("press")
                let haystack = "\(item["role"] ?? "") \(name) \(text)".lowercased()
                if useful && (wanted.isEmpty || haystack.contains(wanted)) { found.append(item) }
            }
            if depth < 20 { queue += children(el).map { ($0, depth + 1) } }
        }
        return found
    }
}

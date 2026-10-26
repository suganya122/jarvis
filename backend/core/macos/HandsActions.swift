// What `Jarvis --hands` does to other apps: press controls, set text, keys, scrolling, menus,
// moving windows. Each returns {"ok", "evidence", "error"}: what was read back afterwards, so the
// assistant never reports an action it can't see happened.
import AppKit
import ApplicationServices

extension HandsServer {
    private func result(_ ok: Bool, _ evidence: [String: Any] = [:], error: String? = nil) -> [String: Any] {
        ["ok": ok, "evidence": evidence, "error": orNull(ok ? nil : (error ?? "it didn't work"))]
    }

    func press(_ el: AXUIElement) throws -> [String: Any] {
        let status = AXUIElementPerformAction(el, kAXPressAction as CFString)
        return result(status == .success, ["ax_status": status.rawValue],
                      error: "the control didn't accept a press (\(status.rawValue))")
    }

    func setValue(_ el: AXUIElement, _ text: String, append: Bool) throws -> [String: Any] {
        if isSecure(el) { throw HandsError(message: "won't type into a password field") }
        AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        let wanted = append ? (string(el, kAXValueAttribute) ?? "") + text : text
        let status = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, wanted as CFString)
        let now = string(el, kAXValueAttribute) ?? ""
        return result(now == wanted, ["ax_status": status.rawValue, "chars": now.count],
                      error: "the field didn't take the text")
    }

    func focus(_ window: AXUIElement) -> [String: Any] {
        let owner = pid(window)
        NSRunningApplication(processIdentifier: owner)?.activate(options: [.activateIgnoringOtherApps])
        AXUIElementPerformAction(window, kAXRaiseAction as CFString)
        AXUIElementSetAttributeValue(window, kAXMainAttribute as CFString, kCFBooleanTrue)
        for _ in 0..<10 where frontApp()?.processIdentifier != owner {
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        }
        let front = frontApp()?.processIdentifier == owner
        return result(front, ["front_app": frontApp()?.localizedName ?? ""], error: "the window didn't come to the front")
    }

    /// Keys go only to the window asked for: it's brought to the front and checked first.
    func key(window: AXUIElement, code: Int, flags: [String]) throws -> [String: Any] {
        guard code >= 0 else { throw HandsError(message: "unknown key") }
        if frontApp()?.processIdentifier != pid(window), focus(window)["ok"] as? Bool != true {
            throw HandsError(message: "couldn't bring that window to the front, so no keys were sent")
        }
        var mask = CGEventFlags()
        for name in flags {
            switch name {
            case "ctrl": mask.insert(.maskControl)
            case "shift": mask.insert(.maskShift)
            case "alt": mask.insert(.maskAlternate)
            case "meta": mask.insert(.maskCommand)
            default: break
            }
        }
        let source = CGEventSource(stateID: .hidSystemState)
        for down in [true, false] {
            let event = CGEvent(keyboardEventSource: source, virtualKey: CGKeyCode(code), keyDown: down)
            event?.flags = mask
            event?.post(tap: .cghidEventTap)
        }
        return result(true, ["front_app": frontApp()?.localizedName ?? ""])
    }

    func scroll(window: AXUIElement, dx: Int, dy: Int) throws -> [String: Any] {
        guard let box = frame(window) else { throw HandsError(message: "can't tell where that window is") }
        let center = CGPoint(x: box["x", default: 0] + box["width", default: 0] / 2,
                             y: box["y", default: 0] + box["height", default: 0] / 2)
        let event = CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 2,
                            wheel1: Int32(dy), wheel2: Int32(dx), wheel3: 0)
        event?.location = center  // goes to the window under this point, without moving the pointer
        event?.post(tap: .cghidEventTap)
        return result(event != nil, ["at": ["x": Int(center.x), "y": Int(center.y)]])
    }

    /// path like ["File", "Export…"]: each item is pressed in turn, as a person clicks through.
    func menu(window: AXUIElement, path: [String]) throws -> [String: Any] {
        let app = AXUIElementCreateApplication(pid(window))
        guard let bar = attribute(app, kAXMenuBarAttribute), CFGetTypeID(bar) == AXUIElementGetTypeID() else {
            throw HandsError(message: "that app has no menu bar")
        }
        var current = bar as! AXUIElement  // swiftlint:disable:this force_cast
        for (index, title) in path.enumerated() {
            let items = children(current).flatMap { string($0, kAXRoleAttribute) == "AXMenu" ? children($0) : [$0] }
            guard let match = items.first(where: {
                (string($0, kAXTitleAttribute) ?? "").lowercased().trimmingCharacters(in: .whitespaces)
                    .replacingOccurrences(of: "…", with: "...") ==
                    title.lowercased().trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "…", with: "...")
            }) else {
                cancelMenus()
                throw HandsError(message: "no menu item '\(title)'")
            }
            if flag(match, kAXEnabledAttribute) == false {
                cancelMenus()
                throw HandsError(message: "'\(title)' is greyed out")
            }
            AXUIElementPerformAction(match, kAXPressAction as CFString)
            current = match
            if index < path.count - 1 { RunLoop.main.run(until: Date().addingTimeInterval(0.15)) }
        }
        return result(true, ["menu": path])
    }

    private func cancelMenus() {
        for down in [true, false] {
            CGEvent(keyboardEventSource: nil, virtualKey: 53, keyDown: down)?.post(tap: .cghidEventTap)  // Escape
        }
    }

    func moveResize(_ window: AXUIElement, _ bounds: [String: Int]) throws -> [String: Any] {
        var point = CGPoint(x: bounds["x", default: 0], y: bounds["y", default: 0])
        var size = CGSize(width: bounds["width", default: 0], height: bounds["height", default: 0])
        guard size.width > 0, size.height > 0, let p = AXValueCreate(.cgPoint, &point),
              let s = AXValueCreate(.cgSize, &size) else { throw HandsError(message: "that size isn't possible") }
        AXUIElementSetAttributeValue(window, kAXPositionAttribute as CFString, p)
        AXUIElementSetAttributeValue(window, kAXSizeAttribute as CFString, s)
        AXUIElementSetAttributeValue(window, kAXPositionAttribute as CFString, p)  // a resize can push it back
        let now = frame(window) ?? [:]
        let close = abs(now["x", default: -9999] - Int(point.x)) <= 24 && abs(now["y", default: -9999] - Int(point.y)) <= 24
        return result(close, ["bounds": now], error: "the window didn't move there (the app may limit its size)")
    }

    /// At the caret of whatever has keyboard focus (dictation).
    func insertText(_ text: String) throws -> [String: Any] {
        guard let el = focusedElement() else { throw HandsError(message: "nothing has keyboard focus") }
        if isSecure(el) { throw HandsError(message: "won't type into a password field") }
        let status = AXUIElementSetAttributeValue(el, kAXSelectedTextAttribute as CFString, text as CFString)
        if status == .success { return result(true, ["via": "accessibility", "chars": text.count]) }
        let units = Array(text.utf16)  // apps that don't take it through Accessibility: type it
        for start in stride(from: 0, to: units.count, by: 20) {
            let chunk = Array(units[start..<min(start + 20, units.count)])
            for down in [true, false] {
                let event = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: down)
                event?.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: chunk)
                event?.post(tap: .cghidEventTap)
            }
        }
        return result(true, ["via": "typing", "chars": text.count])
    }

    func click(x: Int, y: Int, button: String) -> [String: Any] {
        let point = CGPoint(x: x, y: y)
        let (kind, down, up): (CGMouseButton, CGEventType, CGEventType) = switch button {
        case "right": (.right, .rightMouseDown, .rightMouseUp)
        case "middle": (.center, .otherMouseDown, .otherMouseUp)
        default: (.left, .leftMouseDown, .leftMouseUp)
        }
        for type in [down, up] {
            CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: kind)?
                .post(tap: .cghidEventTap)
        }
        return result(true, ["at": ["x": x, "y": y], "front_app": frontApp()?.localizedName ?? ""])
    }
}

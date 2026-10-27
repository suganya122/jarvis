// The menu bar app: an orb in the menu bar, ⌃⌥Space to talk, and the assistant (Python) run as
// a child process. No windows of its own: the assistant shows its floating box when you talk.
import AppKit
import Foundation

/// Talks to the assistant on this Mac only (127.0.0.1), with its local auth token.
enum Assistant {
    static let port = Int(ProcessInfo.processInfo.environment["JARVIS_PORT"] ?? "") ?? 8765
    static var dataDir: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/local-assistant")
    }

    static var token: String? {
        (try? String(contentsOf: dataDir.appendingPathComponent("auth_token"), encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func post(_ path: String, _ body: [String: Any]) {
        guard let url = URL(string: "http://127.0.0.1:\(port)\(path)"), let token = token else { return }
        var request = URLRequest(url: url, timeoutInterval: 5)
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        URLSession.shared.dataTask(with: request).resume()
    }

    static func get(_ path: String, done: @escaping ([String: Any]?) -> Void) {
        guard let url = URL(string: "http://127.0.0.1:\(port)\(path)"), let token = token else { return done(nil) }
        var request = URLRequest(url: url, timeoutInterval: 3)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        URLSession.shared.dataTask(with: request) { data, _, _ in
            done(data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] })
        }.resume()
    }
}

/// Runs `python -m core` (the assistant) and restarts it if it stops by itself.
final class Daemon {
    private var process: Process?
    private var stopping = false
    private var restarts: [Date] = []

    private var python: String? {
        Bundle.main.object(forInfoDictionaryKey: "JarvisPython") as? String
    }

    func start() {
        guard let python = python, FileManager.default.isExecutableFile(atPath: python) else {
            NSLog("Jarvis: the assistant isn't installed (JarvisPython missing); run the installer again")
            return
        }
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-m", "core"]
        var env = ProcessInfo.processInfo.environment
        env["JARVIS_SPEECH_HELPER"] = Bundle.main.executablePath  // Apple speech recognition: this app
        child.environment = env
        let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Jarvis.log")
        FileManager.default.createFile(atPath: logs.path, contents: nil)
        if let handle = try? FileHandle(forWritingTo: logs) {
            handle.truncateFile(atOffset: 0)
            child.standardOutput = handle
            child.standardError = handle
        }
        child.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async { self?.exited() }
        }
        do {
            try child.run()
            process = child
        } catch {
            NSLog("Jarvis: couldn't start the assistant: \(error)")
        }
    }

    private func exited() {
        guard !stopping else { return }
        let now = Date()
        restarts = restarts.filter { now.timeIntervalSince($0) < 300 } + [now]
        if restarts.count <= 3 {
            start()
        } else {
            NSLog("Jarvis: the assistant keeps stopping; see ~/Library/Logs/Jarvis.log")
        }
    }

    /// Asks the assistant to stop and waits at most a few seconds, then makes it. Waiting without
    /// a limit left Jarvis.app half-quit (its ring dead in the menu bar) whenever the assistant
    /// was slow to stop, e.g. with the settings page still open in a browser.
    func stop() {
        stopping = true
        guard let child = process, child.isRunning else { return }
        child.terminate()
        let deadline = Date().addingTimeInterval(5)
        while child.isRunning && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        if child.isRunning { kill(child.processIdentifier, SIGKILL) }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let daemon = Daemon()
    private let hotkey = Hotkey()
    private let terminateSignal = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
    private var quitting = false
    private var statusItem: NSStatusItem!
    private let stateItem = NSMenuItem(title: "Starting…", action: nil, keyEquivalent: "")
    private let pauseItem = NSMenuItem(title: "Pause listening", action: #selector(togglePause), keyEquivalent: "")
    private var paused = false
    private var timer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = Self.orb(filled: false)
        statusItem.button?.toolTip = "Jarvis"
        let menu = NSMenu()
        stateItem.isEnabled = false
        menu.addItem(stateItem)
        menu.addItem(.separator())
        menu.addItem(item("Talk (hold ⌃⌥Space)", #selector(talk)))
        menu.addItem(item("Go offline", #selector(goOffline)))
        pauseItem.target = self
        menu.addItem(pauseItem)
        menu.addItem(item("Settings…", #selector(openSettings), key: ","))
        menu.addItem(item("Show log", #selector(openLog)))
        menu.addItem(.separator())
        menu.addItem(item("Quit Jarvis", #selector(quit), key: "q"))
        statusItem.menu = menu

        daemon.start()
        // Stopped from outside (pkill, an update): close down like Quit, assistant included.
        signal(SIGTERM, SIG_IGN)
        terminateSignal.setEventHandler { [weak self] in
            guard let self = self else { exit(0) }
            if self.quitting { exit(0) }  // asked again while quitting: don't wait any longer
            self.quitting = true
            NSApp.terminate(nil)
        }
        terminateSignal.resume()
        hotkey.onPress = { Assistant.post("/api/voice/command", ["action": "toggle_chat"]) }
        hotkey.onRelease = { Assistant.post("/api/voice/command", ["action": "finish"]) }
        hotkey.register()
        timer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in self?.refresh() }
    }

    func applicationWillTerminate(_ notification: Notification) {
        daemon.stop()
    }

    private func item(_ title: String, _ action: Selector, key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        return item
    }

    /// What the menu says, and a filled orb while it's listening, thinking or speaking.
    private func refresh() {
        Assistant.get("/api/voice") { json in
            let handsFree = json?["hands_free"] as? [String: Any]
            let state = handsFree?["state"] as? String ?? "starting"
            let detail = handsFree?["detail"] as? String ?? ""
            DispatchQueue.main.async {
                let busy = ["listening", "thinking", "speaking", "approval"].contains(state)
                self.statusItem.button?.image = Self.orb(filled: busy)
                switch state {
                case "idle": self.stateItem.title = "Waiting for “Hey Jarvis”"
                case "listening": self.stateItem.title = "Live: just talk"
                case "off": self.stateItem.title = "Off: \(detail)"
                case "starting": self.stateItem.title = "Starting…"
                default: self.stateItem.title = state.capitalized + "…"
                }
            }
        }
    }

    @objc private func talk() {
        Assistant.post("/api/voice/command", ["action": "toggle_chat"])
    }

    @objc private func goOffline() {
        Assistant.post("/api/voice/command", ["action": "offline"])
    }

    @objc private func togglePause() {
        paused.toggle()
        Assistant.post("/api/control/pause", ["paused": paused])
        pauseItem.title = paused ? "Resume listening" : "Pause listening"
    }

    @objc private func openSettings() {
        guard let token = Assistant.token,
              let url = URL(string: "http://127.0.0.1:\(Assistant.port)/#token=\(token)") else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func openLog() {
        let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Jarvis.log")
        NSWorkspace.shared.open(logs)
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    /// A small ring (idle) or filled circle (busy), drawn as a template so it matches the menu bar.
    private static func orb(filled: Bool) -> NSImage {
        let image = NSImage(size: NSSize(width: 18, height: 18), flipped: false) { rect in
            let circle = NSBezierPath(ovalIn: rect.insetBy(dx: 3, dy: 3))
            NSColor.black.setStroke()
            NSColor.black.setFill()
            circle.lineWidth = 1.8
            if filled { circle.fill() } else { circle.stroke() }
            return true
        }
        image.isTemplate = true
        return image
    }
}

// Jarvis.app: the macOS front of the assistant (menu bar, ⌃⌥Space, Apple speech recognition).
// Built on your Mac by install.sh with `swiftc`; the assistant itself runs as a child process,
// so macOS asks for the microphone, speech recognition and Accessibility once, for "Jarvis".
//   Jarvis            the menu bar app (starts the assistant)
//   Jarvis --speech   on-device speech recognition for the assistant's voice worker
//   Jarvis --version  print the version and exit (used by tests)
import AppKit

let arguments = CommandLine.arguments
if arguments.contains("--version") {
    print("Jarvis.app 1.0")
    exit(0)
}
if arguments.contains("--speech") {
    SpeechServer().run()
}
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)  // menu bar only: no Dock icon
app.run()

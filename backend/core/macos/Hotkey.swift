// ⌃⌥Space from any app: press to talk, let go to send. Carbon's hot key API needs no
// Accessibility permission and only sees this one key combination.
import Carbon

final class Hotkey {
    var onPress: (() -> Void)?
    var onRelease: (() -> Void)?
    private var ref: EventHotKeyRef?

    func register() {
        var types = [
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased)),
        ]
        let me = Unmanaged.passUnretained(self).toOpaque()
        InstallEventHandler(GetApplicationEventTarget(), { _, event, user in
            guard let event = event, let user = user else { return noErr }
            let hotkey = Unmanaged<Hotkey>.fromOpaque(user).takeUnretainedValue()
            if GetEventKind(event) == UInt32(kEventHotKeyPressed) {
                hotkey.onPress?()
            } else {
                hotkey.onRelease?()
            }
            return noErr
        }, 2, &types, me, nil)
        let id = EventHotKeyID(signature: OSType(0x4A52_5653), id: 1)  // "JRVS"
        RegisterEventHotKey(UInt32(kVK_Space), UInt32(controlKey | optionKey), id, GetApplicationEventTarget(), 0, &ref)
    }
}

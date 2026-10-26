"""macOS: make the overlay process a background accessory: no Dock icon, no "Python" menu
bar, never the active app. Tk creates an NSApplication; we set its activation policy through
the Objective-C runtime with ctypes (no extra packages)."""

import ctypes
import ctypes.util
import sys

ACCESSORY = 1  # NSApplicationActivationPolicyAccessory: like a menu bar helper


def run_as_accessory() -> bool:
    if sys.platform != "darwin":
        return False
    path = ctypes.util.find_library("objc")
    if not path:
        return False
    objc = ctypes.cdll.LoadLibrary(path)
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
    send_long = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(
        ("objc_msgSend", objc)
    )
    app = send_id(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
    if not app:
        return False
    return bool(send_long(app, objc.sel_registerName(b"setActivationPolicy:"), ACCESSORY))

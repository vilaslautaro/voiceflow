"""Native Win32 low-level keyboard hook for Ctrl+Win suppression.

Uses SetWindowsHookExW with WH_KEYBOARD_LL to intercept Win key events
BEFORE Windows processes them. Returns 1 directly from the hook callback
to suppress the Win key, which is more reliable than pynput's suppress_event().

This prevents Windows from opening Start menu, Live Captions, on-screen
keyboard, or any other Win-key shortcut when Ctrl is held.
"""

import ctypes
import ctypes.wintypes as wt
import threading
from typing import Callable, Optional

# Win32 constants
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LWIN = 0x5B
VK_RWIN = 0x5C


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


# Low-level hook callback type: LRESULT CALLBACK(int nCode, WPARAM, LPARAM)
HOOKPROC = ctypes.CFUNCTYPE(
    ctypes.c_long,   # return LRESULT
    ctypes.c_int,    # nCode
    wt.WPARAM,       # wParam (message type)
    wt.LPARAM,       # lParam (pointer to KBDLLHOOKSTRUCT)
)

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# Properly define arg/return types for 64-bit compatibility
_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
_user32.SetWindowsHookExW.restype = wt.HHOOK

_user32.CallNextHookEx.argtypes = [
    wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
_user32.CallNextHookEx.restype = ctypes.c_long

_user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
_user32.UnhookWindowsHookEx.restype = wt.BOOL

_user32.GetMessageW.argtypes = [
    ctypes.POINTER(wt.MSG), wt.HWND, ctypes.c_uint, ctypes.c_uint]
_user32.GetMessageW.restype = wt.BOOL

_user32.PostThreadMessageW.argtypes = [
    wt.DWORD, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
_user32.PostThreadMessageW.restype = wt.BOOL

_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wt.DWORD


class HotkeyHook:
    """Low-level keyboard hook that suppresses Win key when Ctrl is held.

    Provides callbacks for hotkey press and release events, suitable for
    both Push-to-Talk (hold) and Toggle modes.

    Usage:
        hook = HotkeyHook(
            on_hotkey_down=lambda: print("Ctrl+Win pressed"),
            on_hotkey_up=lambda: print("Ctrl+Win released"),
        )
        hook.start()   # installs hook in background thread
        ...
        hook.stop()    # removes hook
    """

    def __init__(
        self,
        on_hotkey_down: Optional[Callable[[], None]] = None,
        on_hotkey_up: Optional[Callable[[], None]] = None,
    ):
        self._on_down = on_hotkey_down or (lambda: None)
        self._on_up = on_hotkey_up or (lambda: None)

        self._hook: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None

        # Key state tracking
        self._ctrl_held = False
        self._win_held = False
        self._hotkey_active = False  # True while Ctrl+Win is held together

        # MUST keep a reference to the callback to prevent garbage collection
        self._hook_proc_ref = HOOKPROC(self._ll_keyboard_proc)

    @property
    def ctrl_held(self) -> bool:
        return self._ctrl_held

    @property
    def win_held(self) -> bool:
        return self._win_held

    @property
    def hotkey_active(self) -> bool:
        return self._hotkey_active

    def start(self) -> None:
        """Install the hook in a background thread with its own message pump."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Remove the hook and stop the message pump thread."""
        if self._thread_id is not None:
            # Post WM_QUIT to the hook thread's message loop
            _user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        self._thread = None
        self._thread_id = None

    # ─── Internal ───

    def _run(self) -> None:
        """Thread entry: install hook and run message pump."""
        self._thread_id = _kernel32.GetCurrentThreadId()

        # hMod=0 is required for Python — passing GetModuleHandleW(None)
        # returns an invalid handle that causes ERROR_MOD_NOT_FOUND (126).
        self._hook = _user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._hook_proc_ref,
            0,
            0,
        )

        if not self._hook:
            return

        # Message pump — required for low-level hooks to receive events
        msg = wt.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

        # Cleanup on exit
        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _ll_keyboard_proc(
        self, nCode: int, wParam: int, lParam: int
    ) -> int:
        """Low-level keyboard hook callback.

        Returns 1 to suppress a key event, or calls CallNextHookEx to
        pass it through to the next hook / OS.
        """
        if nCode < 0:
            return _user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = kb.vkCode
        is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)

        # ── Track Ctrl state ──
        if vk in (VK_LCONTROL, VK_RCONTROL):
            if is_down:
                self._ctrl_held = True
            elif is_up:
                self._ctrl_held = False
                # If hotkey was active and Ctrl released → fire release
                if self._hotkey_active:
                    self._hotkey_active = False
                    self._win_held = False
                    try:
                        self._on_up()
                    except Exception:
                        pass

        # ── Track Win state + suppress when Ctrl is held ──
        elif vk in (VK_LWIN, VK_RWIN):
            if self._ctrl_held:
                # Ctrl is held → suppress Win key from reaching OS
                if is_down:
                    if not self._hotkey_active:
                        self._win_held = True
                        self._hotkey_active = True
                        try:
                            self._on_down()
                        except Exception:
                            pass
                elif is_up:
                    self._win_held = False
                    if self._hotkey_active:
                        self._hotkey_active = False
                        try:
                            self._on_up()
                        except Exception:
                            pass

                # SUPPRESS: return 1 to block Win key from Windows
                return 1

            else:
                # Win without Ctrl → let it through to OS
                if is_down:
                    self._win_held = True
                elif is_up:
                    self._win_held = False

        # Pass all other keys through
        return _user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

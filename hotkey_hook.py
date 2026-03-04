"""Cross-platform **configurable** global hotkey hook.

Windows: Win32 WH_KEYBOARD_LL — suppresses trigger key via hook.
macOS:   Quartz CGEventTap — suppresses via active event tap.
Linux:   Not yet supported (falls back to no-op).

The hotkey combo is specified as a string like ``"Alt+Z"`` or ``"Ctrl+Win"``.
The last token is the *trigger key*; all preceding tokens are *modifiers*.
"""

import sys
import threading
from typing import Callable, List, Optional, Set, Tuple

# ────────────────────────────────────────────────────────────────
#  Common definitions
# ────────────────────────────────────────────────────────────────

MODIFIER_NAMES = frozenset({"Ctrl", "Alt", "Shift", "Win", "Cmd"})

DEFAULT_HOTKEY = "Ctrl+Cmd" if sys.platform == "darwin" else "Alt+Z"

# Preset combos shown in the UI
if sys.platform == "darwin":
    HOTKEY_PRESETS = ["Ctrl+Cmd", "Alt+Z", "Cmd+Shift+Z"]
else:
    HOTKEY_PRESETS = ["Alt+Z", "Ctrl+Win", "Ctrl+Shift+Z", "Alt+X", "Alt+Space"]


def format_hotkey_display(combo: str) -> str:
    """``'Alt+Z'`` → ``'Alt + Z'``."""
    return " + ".join(p.strip() for p in combo.split("+"))


def _split_combo(combo: str) -> Tuple[List[str], str]:
    """Split ``'Alt+Z'`` → ``(['Alt'], 'Z')``."""
    parts = [p.strip() for p in combo.split("+")]
    if len(parts) < 2:
        raise ValueError(f"Invalid hotkey combo (need at least 2 keys): {combo}")
    return parts[:-1], parts[-1]


# ────────────────────────────────────────────────────────────────
#  Windows: WH_KEYBOARD_LL — configurable combo
# ────────────────────────────────────────────────────────────────

if sys.platform == "win32":

    import ctypes
    import ctypes.wintypes as wt

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wt.DWORD),
            ("scanCode", wt.DWORD),
            ("flags", wt.DWORD),
            ("time", wt.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    HOOKPROC = ctypes.CFUNCTYPE(
        ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

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

    # ── VK code map ──
    _WIN_VK: dict[str, tuple[int, ...]] = {
        "Ctrl": (0xA2, 0xA3),   # L/R Control
        "Alt": (0xA4, 0xA5),    # L/R Menu
        "Shift": (0xA0, 0xA1),  # L/R Shift
        "Win": (0x5B, 0x5C),    # L/R Win
        "Cmd": (0x5B, 0x5C),    # alias for Win
        "Space": (0x20,),
        "Tab": (0x09,),
        "Escape": (0x1B,),
    }
    # Letters A-Z
    for _c in range(26):
        _WIN_VK[chr(0x41 + _c)] = (0x41 + _c,)
    # F1-F12
    for _i in range(1, 13):
        _WIN_VK[f"F{_i}"] = (0x70 + _i - 1,)

    def _parse_combo_win(combo: str):
        """Return (modifier_groups, trigger_vks)."""
        mod_names, trigger_name = _split_combo(combo)
        modifier_groups: list[tuple[int, ...]] = []
        for m in mod_names:
            if m not in _WIN_VK:
                raise ValueError(f"Unknown key: {m}")
            modifier_groups.append(_WIN_VK[m])
        if trigger_name not in _WIN_VK:
            raise ValueError(f"Unknown key: {trigger_name}")
        trigger_vks = _WIN_VK[trigger_name]
        return modifier_groups, trigger_vks

    class _WindowsHotkeyHook:
        """Generic low-level keyboard hook for any modifier+trigger combo."""

        def __init__(
            self,
            combo: str,
            on_hotkey_down: Optional[Callable[[], None]] = None,
            on_hotkey_up: Optional[Callable[[], None]] = None,
        ):
            self._combo = combo
            self._on_down = on_hotkey_down or (lambda: None)
            self._on_up = on_hotkey_up or (lambda: None)

            modifier_groups, trigger_vks = _parse_combo_win(combo)
            self._modifier_groups = modifier_groups
            self._trigger_vk_set: Set[int] = set(trigger_vks)

            # Modifier VK → group index (for fast lookup)
            self._mod_vk_to_group: dict[int, int] = {}
            for i, group in enumerate(modifier_groups):
                for vk in group:
                    self._mod_vk_to_group[vk] = i

            # State
            self._mod_held: dict[int, bool] = {
                i: False for i in range(len(modifier_groups))}
            self._hotkey_active = False
            self._combo_used = False  # prevents modifier side-effects

            self._hook: Optional[int] = None
            self._thread: Optional[threading.Thread] = None
            self._thread_id: Optional[int] = None
            self._hook_proc_ref = HOOKPROC(self._ll_keyboard_proc)

        @property
        def hotkey_active(self) -> bool:
            return self._hotkey_active

        def start(self) -> None:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        def stop(self) -> None:
            if self._thread_id is not None:
                _user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
            if self._hook:
                _user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
            self._thread = None
            self._thread_id = None

        def _run(self) -> None:
            self._thread_id = _kernel32.GetCurrentThreadId()
            self._hook = _user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._hook_proc_ref, 0, 0)
            if not self._hook:
                return
            msg = wt.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
            if self._hook:
                _user32.UnhookWindowsHookEx(self._hook)
                self._hook = None

        def _ll_keyboard_proc(self, nCode: int, wParam: int, lParam: int) -> int:
            if nCode < 0:
                return _user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

            kb = ctypes.cast(
                lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode
            is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)

            # ── Modifier key ──
            if vk in self._mod_vk_to_group:
                gidx = self._mod_vk_to_group[vk]
                if is_down:
                    self._mod_held[gidx] = True
                elif is_up:
                    self._mod_held[gidx] = False
                    if self._hotkey_active:
                        self._hotkey_active = False
                        try:
                            self._on_up()
                        except Exception:
                            pass
                    # Suppress modifier release after combo was used
                    # (prevents Alt menu flash, Win Start menu, etc.)
                    if self._combo_used:
                        if not any(self._mod_held.values()):
                            self._combo_used = False
                        return 1
                return _user32.CallNextHookEx(
                    self._hook, nCode, wParam, lParam)

            # ── Trigger key ──
            if vk in self._trigger_vk_set:
                all_mods = all(self._mod_held.values())
                if is_down and all_mods:
                    self._combo_used = True
                    if not self._hotkey_active:
                        self._hotkey_active = True
                        try:
                            self._on_down()
                        except Exception:
                            pass
                    return 1  # suppress
                elif is_up:
                    if self._hotkey_active:
                        self._hotkey_active = False
                        try:
                            self._on_up()
                        except Exception:
                            pass
                        return 1  # suppress release too

            return _user32.CallNextHookEx(self._hook, nCode, wParam, lParam)


# ────────────────────────────────────────────────────────────────
#  macOS: Quartz CGEventTap — configurable combo
# ────────────────────────────────────────────────────────────────

elif sys.platform == "darwin":

    try:
        import Quartz
        _HAS_QUARTZ = True
    except ImportError:
        _HAS_QUARTZ = False

    # Modifier name → CGEvent flag mask
    _MAC_MOD_FLAGS: dict[str, int] = {}
    if _HAS_QUARTZ:
        _MAC_MOD_FLAGS = {
            "Ctrl": Quartz.kCGEventFlagMaskControl,
            "Alt": Quartz.kCGEventFlagMaskAlternate,
            "Shift": Quartz.kCGEventFlagMaskShift,
            "Cmd": Quartz.kCGEventFlagMaskCommand,
            "Win": Quartz.kCGEventFlagMaskCommand,
        }

    # macOS virtual key codes (from Carbon/Events.h)
    _MAC_KEYCODES: dict[str, int] = {
        "A": 0, "S": 1, "D": 2, "F": 3, "H": 4, "G": 5, "Z": 6,
        "X": 7, "C": 8, "V": 9, "B": 11, "Q": 12, "W": 13, "E": 14,
        "R": 15, "Y": 16, "T": 17, "O": 31, "U": 32, "I": 34,
        "P": 35, "L": 37, "J": 38, "K": 40, "N": 45, "M": 46,
        "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22,
        "7": 26, "8": 28, "9": 25, "0": 29,
        "Space": 49, "Tab": 48, "Escape": 53,
        "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96,
        "F6": 97, "F7": 98, "F8": 100, "F9": 101, "F10": 109,
        "F11": 103, "F12": 111,
    }

    def _parse_combo_mac(combo: str):
        """Return (modifier_flag_list, trigger_keycode_or_None, trigger_flag_or_None)."""
        mod_names, trigger_name = _split_combo(combo)
        mod_flags = []
        for m in mod_names:
            if m not in _MAC_MOD_FLAGS:
                raise ValueError(f"Unknown modifier on macOS: {m}")
            mod_flags.append(_MAC_MOD_FLAGS[m])
        # Trigger: could be a modifier (flags-only) or a regular key
        if trigger_name in _MAC_MOD_FLAGS:
            return mod_flags, None, _MAC_MOD_FLAGS[trigger_name]
        elif trigger_name in _MAC_KEYCODES:
            return mod_flags, _MAC_KEYCODES[trigger_name], None
        else:
            raise ValueError(f"Unknown key on macOS: {trigger_name}")

    class _MacHotkeyHook:
        """Quartz CGEventTap hook for configurable combo."""

        def __init__(
            self,
            combo: str,
            on_hotkey_down: Optional[Callable[[], None]] = None,
            on_hotkey_up: Optional[Callable[[], None]] = None,
        ):
            self._combo = combo
            self._on_down = on_hotkey_down or (lambda: None)
            self._on_up = on_hotkey_up or (lambda: None)

            self._modifier_flags, self._trigger_keycode, self._trigger_flag = \
                _parse_combo_mac(combo)

            self._hotkey_active = False
            self._thread: Optional[threading.Thread] = None
            self._run_loop_ref = None
            self._tap = None

        @property
        def hotkey_active(self) -> bool:
            return self._hotkey_active

        def start(self) -> None:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        def stop(self) -> None:
            if self._run_loop_ref is not None:
                try:
                    Quartz.CFRunLoopStop(self._run_loop_ref)
                except Exception:
                    pass
            if self._tap is not None:
                try:
                    Quartz.CGEventTapEnable(self._tap, False)
                except Exception:
                    pass
            self._thread = None
            self._run_loop_ref = None
            self._tap = None

        def _all_mod_flags_held(self, flags: int) -> bool:
            return all(bool(flags & m) for m in self._modifier_flags)

        def _event_callback(self, proxy, event_type, event, refcon):
            if event_type == Quartz.kCGEventTapDisabledByTimeout:
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event

            flags = Quartz.CGEventGetFlags(event)
            all_mods = self._all_mod_flags_held(flags)

            # ── Modifier-only combo (e.g. Ctrl+Cmd) ──
            if self._trigger_flag is not None and self._trigger_keycode is None:
                trigger_held = bool(flags & self._trigger_flag)
                if event_type == Quartz.kCGEventFlagsChanged:
                    both = all_mods and trigger_held
                    if both and not self._hotkey_active:
                        self._hotkey_active = True
                        try:
                            self._on_down()
                        except Exception:
                            pass
                    elif not both and self._hotkey_active:
                        self._hotkey_active = False
                        try:
                            self._on_up()
                        except Exception:
                            pass
                    return event
                if self._hotkey_active:
                    return None  # suppress regular keys while active
                return event

            # ── Combo with regular key trigger (e.g. Alt+Z) ──
            if event_type == Quartz.kCGEventFlagsChanged:
                if self._hotkey_active and not all_mods:
                    self._hotkey_active = False
                    try:
                        self._on_up()
                    except Exception:
                        pass
                return event

            if event_type in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
                keycode = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode)
                if keycode == self._trigger_keycode and all_mods:
                    if event_type == Quartz.kCGEventKeyDown:
                        if not self._hotkey_active:
                            self._hotkey_active = True
                            try:
                                self._on_down()
                            except Exception:
                                pass
                        return None  # suppress
                    else:  # KeyUp
                        if self._hotkey_active:
                            self._hotkey_active = False
                            try:
                                self._on_up()
                            except Exception:
                                pass
                        return None  # suppress

            if self._hotkey_active:
                return None
            return event

        def _run(self) -> None:
            if not _HAS_QUARTZ:
                print(
                    "ERROR: Quartz framework not available.\n"
                    "  pip install pyobjc-framework-Quartz")
                return
            mask = (
                Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
            )
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                0, mask, self._event_callback, None)
            if tap is None:
                print(
                    "ERROR: Could not create event tap.\n"
                    "  Grant Accessibility permissions.")
                return
            self._tap = tap
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            run_loop = Quartz.CFRunLoopGetCurrent()
            self._run_loop_ref = run_loop
            Quartz.CFRunLoopAddSource(
                run_loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            Quartz.CFRunLoopRun()


# ────────────────────────────────────────────────────────────────
#  Fallback: No-op (Linux / unsupported)
# ────────────────────────────────────────────────────────────────

else:

    class _NullHotkeyHook:
        def __init__(self, combo: str,
                     on_hotkey_down=None, on_hotkey_up=None):
            self._hotkey_active = False

        @property
        def hotkey_active(self) -> bool:
            return False

        def start(self) -> None:
            print("WARNING: Global hotkey not supported on this platform.")

        def stop(self) -> None:
            pass


# ────────────────────────────────────────────────────────────────
#  Public API
# ────────────────────────────────────────────────────────────────

def HotkeyHook(
    combo: Optional[str] = None,
    on_hotkey_down: Optional[Callable[[], None]] = None,
    on_hotkey_up: Optional[Callable[[], None]] = None,
):
    """Create the platform-appropriate hotkey hook for *combo*.

    *combo* is a ``"+"``-separated string like ``"Alt+Z"`` or ``"Ctrl+Win"``.
    If *None*, the platform default is used.
    """
    if combo is None:
        combo = DEFAULT_HOTKEY
    if sys.platform == "win32":
        return _WindowsHotkeyHook(combo, on_hotkey_down, on_hotkey_up)
    elif sys.platform == "darwin":
        return _MacHotkeyHook(combo, on_hotkey_down, on_hotkey_up)
    else:
        return _NullHotkeyHook(combo, on_hotkey_down, on_hotkey_up)

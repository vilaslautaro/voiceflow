"""Lightweight sound playback for VoiceFlow (start/stop recording cues).

Plays short WAV files from ``assets/sounds/``.
- Windows: uses ``winsound`` (stdlib, zero dependencies) with SND_ASYNC.
- macOS: uses ``afplay`` via subprocess in a daemon thread.
- Other: silent fallback.
"""

import os
import sys
import threading

_SOUNDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "assets", "sounds"
)


def play_sound(name: str) -> None:
    """Play a sound by name (e.g. ``"start"`` or ``"stop"``).

    Non-blocking on all platforms.
    """
    path = os.path.join(_SOUNDS_DIR, f"{name}.wav")
    if not os.path.isfile(path):
        return

    if sys.platform == "win32":
        try:
            import winsound
            # SND_ASYNC returns immediately; SND_FILENAME reads from disk
            winsound.PlaySound(
                path,
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        except Exception:
            pass
    elif sys.platform == "darwin":
        def _play():
            try:
                import subprocess
                subprocess.run(
                    ["afplay", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                )
            except Exception:
                pass
        threading.Thread(target=_play, daemon=True).start()

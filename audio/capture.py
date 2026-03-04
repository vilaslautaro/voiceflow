import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd


def list_input_devices() -> list[dict]:
    """Return a list of available input (microphone) devices.

    Filters to only show devices from the default host API to avoid
    duplicate entries from multiple audio backends (WASAPI, MME, DirectSound).

    Each entry has keys: 'index', 'name', 'is_default'.
    """
    devices = sd.query_devices()
    default_input = sd.default.device[0]  # default input device index

    # Determine the host API of the default device to filter duplicates
    try:
        default_host_api = devices[default_input]["hostapi"]
    except (IndexError, KeyError):
        default_host_api = None

    result = []
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            # Only show devices from the same host API as the default device
            if default_host_api is not None and d.get("hostapi") != default_host_api:
                continue
            result.append({
                "index": i,
                "name": d["name"],
                "is_default": (i == default_input),
            })
    return result


class AudioCapture:
    """Manages microphone input via sounddevice."""

    SAMPLE_RATE = 16000
    CHANNELS = 1
    BLOCKSIZE = 8000  # 0.5 seconds at 16kHz

    def __init__(self):
        self._stream: Optional[sd.InputStream] = None
        self._callback: Optional[Callable[[np.ndarray], None]] = None

    def start(
        self,
        callback: Callable[[np.ndarray], None],
        device: Optional[int] = None,
    ) -> None:
        """Start capturing audio from the microphone.

        Args:
            callback: Called with each audio chunk (1D float32 numpy array).
            device: Optional sounddevice device index.
        """
        self._callback = callback
        self._stream = sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=self.CHANNELS,
            blocksize=self.BLOCKSIZE,
            dtype="float32",
            device=device,
            callback=self._audio_callback,
        )
        self._stream.start()

    def stop(self) -> None:
        """Stop capturing audio."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            print(f"Audio status: {status}")
        if self._callback is not None:
            self._callback(indata[:, 0].copy())


class LoopbackCapture:
    """Captures system audio (what the PC is playing) via WASAPI loopback."""

    SAMPLE_RATE = 16000
    CHANNELS = 1
    BLOCK_DURATION = 0.5  # seconds per read

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._callback: Optional[Callable[[np.ndarray], None]] = None

    def start(
        self,
        callback: Callable[[np.ndarray], None],
        device: Optional[int] = None,
    ) -> None:
        """Start capturing system audio via loopback."""
        self._callback = callback
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _capture_loop(self) -> None:
        import soundcard as sc

        speaker = sc.default_speaker()
        num_frames = int(self.SAMPLE_RATE * self.BLOCK_DURATION)
        with speaker.recorder(
            samplerate=self.SAMPLE_RATE,
            channels=self.CHANNELS,
            blocksize=num_frames,
        ) as recorder:
            while self._running:
                try:
                    data = recorder.record(numframes=num_frames)
                    # soundcard returns (frames, channels) float64
                    chunk = data[:, 0].astype(np.float32)
                    if self._callback is not None:
                        self._callback(chunk)
                except Exception as e:
                    if self._running:
                        print(f"Loopback capture error: {e}")
                    break

    def stop(self) -> None:
        """Stop capturing system audio."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

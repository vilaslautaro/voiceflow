"""Deepgram Nova-3 real-time STT engine.

Uses Deepgram's WebSocket streaming API for low-latency speech-to-text.
Requires: pip install deepgram-sdk
"""

import threading
from typing import Callable, Optional

import numpy as np

from engines.base import STTEngine
from postprocessing.config import get_config, get_api_key


# Deepgram language code mapping (matches app's LANGUAGE_MAP values)
_LANG_MAP = {
    "es": "es",
    "en": "en-US",
    "fr": "fr",
    "pt": "pt-BR",
    "de": "de",
    "it": "it",
    "zh": "zh",
    "ja": "ja",
    "ko": "ko",
    "ru": "ru",
}


class DeepgramEngine(STTEngine):
    """Real-time streaming STT via Deepgram Nova-3 WebSocket API."""

    @property
    def name(self) -> str:
        return "Deepgram Nova-3"

    def __init__(self):
        self._client = None
        self._connection = None
        self._on_text: Optional[Callable[[str], None]] = None
        self._on_partial: Optional[Callable[[str], None]] = None
        self._running = False
        self._connected = False
        self._sample_rate = 16000

        # Language (set by app before load_model / start)
        self.language: str = "es"
        self.task: str = "transcribe"  # "transcribe" or "translate"

        # Buffer for accumulating final segments within an utterance
        self._accumulated = ""

    # ─── Model loading (validate API key + create client) ───

    def load_model(
        self,
        model_path: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        from deepgram import DeepgramClient

        log = on_log or (lambda s: None)
        status = on_status or (lambda s: None)
        cancel = cancel_event or threading.Event()

        status("Conectando con Deepgram...")
        log("Deepgram: inicializando cliente...")

        # Get API key from config
        api_key = get_api_key("Deepgram")
        if not api_key:
            # Also check config for a deepgram-specific field
            cfg = get_config()
            api_key = cfg.get("deepgram_api_key", "")

        if not api_key:
            raise RuntimeError(
                "API key de Deepgram no configurada. "
                "Use Config AI o agregue 'deepgram_api_key' en config.json."
            )

        if cancel.is_set():
            return

        self._client = DeepgramClient(api_key=api_key)
        log("Deepgram: cliente creado correctamente.")
        status("Deepgram listo.")

    # ─── Start streaming session ───

    def start(
        self,
        sample_rate: int,
        on_text: Callable[[str], None],
        on_partial: Callable[[str], None],
    ) -> None:
        from deepgram import LiveTranscriptionEvents, LiveOptions

        if self._client is None:
            raise RuntimeError("Deepgram client not initialized. Call load_model() first.")

        self._sample_rate = sample_rate
        self._on_text = on_text
        self._on_partial = on_partial
        self._running = True
        self._connected = False
        self._accumulated = ""

        # Create live WebSocket connection
        self._connection = self._client.listen.live.v("1")

        # Register event handlers
        self._connection.on(
            LiveTranscriptionEvents.Open, self._on_ws_open)
        self._connection.on(
            LiveTranscriptionEvents.Transcript, self._on_ws_transcript)
        self._connection.on(
            LiveTranscriptionEvents.UtteranceEnd, self._on_ws_utterance_end)
        self._connection.on(
            LiveTranscriptionEvents.Error, self._on_ws_error)
        self._connection.on(
            LiveTranscriptionEvents.Close, self._on_ws_close)

        # Map language code
        dg_lang = _LANG_MAP.get(self.language, self.language) or "es"

        # Configure options
        options = LiveOptions(
            model="nova-3",
            language=dg_lang,
            encoding="linear16",
            channels=1,
            sample_rate=sample_rate,
            interim_results=True,
            punctuate=True,
            smart_format=True,
            endpointing=300,
            utterance_end_ms="1500",
            vad_events=True,
        )

        # Start the connection (opens WebSocket in background thread)
        self._connection.start(options)

    # ─── Feed audio ───

    def feed_audio(self, audio_chunk: np.ndarray) -> None:
        """Convert float32 audio to int16 bytes and send to Deepgram."""
        if not self._running or self._connection is None:
            return

        try:
            # Convert float32 [-1.0, 1.0] → int16 bytes (little-endian)
            int16_data = (audio_chunk * 32767).astype(np.int16).tobytes()
            self._connection.send(int16_data)
        except Exception:
            pass  # Connection may have closed

    # ─── Stop ───

    def stop(self) -> None:
        """Close the WebSocket connection and flush remaining text."""
        self._running = False

        if self._connection is not None:
            try:
                self._connection.finish()
            except Exception:
                pass
            self._connection = None

        # Emit any accumulated text that wasn't flushed
        if self._accumulated.strip() and self._on_text:
            self._on_text(self._accumulated.strip() + " ")
            self._accumulated = ""

    # ─── WebSocket event handlers ───

    def _on_ws_open(self, _self, open_response, **kwargs) -> None:
        self._connected = True

    def _on_ws_transcript(self, _self, result, **kwargs) -> None:
        """Handle transcript results from Deepgram.

        result.is_final = True  → segment is finalized (won't change)
        result.speech_final = True → speaker paused (endpoint detected)
        """
        if not self._running:
            return

        try:
            transcript = result.channel.alternatives[0].transcript
        except (AttributeError, IndexError):
            return

        if not transcript:
            return

        if result.is_final:
            # Finalized segment
            self._accumulated += transcript + " "

            if result.speech_final:
                # Speaker paused → emit full utterance as final text
                if self._accumulated.strip() and self._on_text:
                    self._on_text(self._accumulated.strip() + " ")
                self._accumulated = ""
            else:
                # Show accumulated so far as partial (still talking)
                if self._on_partial:
                    self._on_partial(self._accumulated.strip())
        else:
            # Interim result → show as partial
            preview = self._accumulated + transcript
            if self._on_partial:
                self._on_partial(preview.strip())

    def _on_ws_utterance_end(self, _self, utterance_end, **kwargs) -> None:
        """Flush accumulated text when Deepgram detects utterance boundary."""
        if self._accumulated.strip() and self._on_text:
            self._on_text(self._accumulated.strip() + " ")
        self._accumulated = ""

    def _on_ws_error(self, _self, error, **kwargs) -> None:
        pass  # Silently ignore (errors logged in app via on_status)

    def _on_ws_close(self, _self, close_response, **kwargs) -> None:
        self._connected = False

    # ─── Availability ───

    @classmethod
    def is_available(cls) -> bool:
        try:
            import deepgram  # noqa: F401
            return True
        except ImportError:
            return False

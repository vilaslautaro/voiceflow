import json
import os
import threading
import urllib.request
import zipfile
from typing import Callable, Optional

import numpy as np

from engines.base import STTEngine

MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip"
MODEL_DIR_NAME = "vosk-model-small-es-0.42"


class VoskEngine(STTEngine):

    @property
    def name(self) -> str:
        return "Vosk (streaming)"

    def __init__(self):
        self._model = None
        self._recognizer = None
        self._on_text: Optional[Callable[[str], None]] = None
        self._on_partial: Optional[Callable[[str], None]] = None
        self._running = False

    def load_model(
        self,
        model_path: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        import vosk

        log = on_log or (lambda s: None)
        cancel = cancel_event or threading.Event()
        vosk.SetLogLevel(-1)

        models_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "vosk")
        path = model_path or os.path.join(models_dir, MODEL_DIR_NAME)

        log(f"Buscando modelo en: {path}")

        if not os.path.exists(path):
            log(f"Modelo no encontrado. Descargando desde: {MODEL_URL}")
            self._download_model(models_dir, on_status, log, cancel)
            if cancel.is_set():
                return

        if cancel.is_set():
            return

        if on_status:
            on_status("Cargando modelo Vosk en memoria...")
        log("Cargando modelo en memoria...")
        self._model = vosk.Model(path)
        log("Modelo Vosk cargado correctamente.")

    def _download_model(
        self, models_dir: str,
        on_status: Optional[Callable[[str], None]],
        log: Callable[[str], None],
        cancel: threading.Event,
    ) -> None:
        os.makedirs(models_dir, exist_ok=True)
        zip_path = os.path.join(models_dir, "model.zip")

        if on_status:
            on_status("Descargando modelo Vosk (~39 MB)...")
        log(f"Destino: {zip_path}")

        last_pct = [-1]

        def _progress(block_num, block_size, total_size):
            if cancel.is_set():
                raise InterruptedError("Descarga cancelada")
            if total_size > 0:
                downloaded = block_num * block_size
                pct = min(100, int(downloaded * 100 / total_size))
                mb_down = downloaded / (1024 * 1024)
                mb_total = total_size / (1024 * 1024)
                if on_status:
                    on_status(f"Descargando modelo Vosk... {pct}% ({mb_down:.1f}/{mb_total:.1f} MB)")
                if pct >= last_pct[0] + 10:
                    log(f"Descarga: {pct}% ({mb_down:.1f}/{mb_total:.1f} MB)")
                    last_pct[0] = pct

        try:
            urllib.request.urlretrieve(MODEL_URL, zip_path, reporthook=_progress)
        except InterruptedError:
            log("Descarga cancelada. Limpiando archivo parcial...")
            if os.path.exists(zip_path):
                os.remove(zip_path)
            return

        log("Descarga completa. Extrayendo archivo zip...")

        if on_status:
            on_status("Extrayendo modelo Vosk...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(models_dir)
        os.remove(zip_path)
        log("Modelo Vosk extraido correctamente.")

    def start(
        self,
        sample_rate: int,
        on_text: Callable[[str], None],
        on_partial: Callable[[str], None],
    ) -> None:
        import vosk

        self._recognizer = vosk.KaldiRecognizer(self._model, sample_rate)
        self._on_text = on_text
        self._on_partial = on_partial
        self._running = True

    def feed_audio(self, audio_chunk: np.ndarray) -> None:
        if not self._running or self._recognizer is None:
            return
        int16_data = (audio_chunk * 32767).astype(np.int16).tobytes()
        if self._recognizer.AcceptWaveform(int16_data):
            result = json.loads(self._recognizer.Result())
            text = result.get("text", "")
            if text and self._on_text:
                self._on_text(text + " ")
        else:
            partial = json.loads(self._recognizer.PartialResult())
            partial_text = partial.get("partial", "")
            if partial_text and self._on_partial:
                self._on_partial(partial_text)

    def stop(self) -> None:
        self._running = False
        if self._recognizer is not None:
            final = json.loads(self._recognizer.FinalResult())
            text = final.get("text", "")
            if text and self._on_text:
                self._on_text(text + " ")
            self._recognizer = None

    @classmethod
    def is_available(cls) -> bool:
        try:
            import vosk  # noqa: F401
            return True
        except ImportError:
            return False

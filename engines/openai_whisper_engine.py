import os
import threading
import time
from typing import Callable, Optional

import numpy as np

from engines.base import STTEngine


class OpenAIWhisperEngine(STTEngine):
    CHUNK_DURATION = 5.0  # Transcribe every N seconds (more = better quality)
    MAX_BUFFER_DURATION = 15.0  # Safety cap for buffer size

    AVAILABLE_MODELS = {
        "tiny": "Tiny (~75 MB)",
        "base": "Base (~150 MB)",
        "small": "Small (~500 MB)",
        "medium": "Medium (~1.5 GB)",
    }

    @property
    def name(self) -> str:
        return "OpenAI Whisper (oficial)"

    def __init__(self):
        self._model = None
        self._on_text: Optional[Callable[[str], None]] = None
        self._on_partial: Optional[Callable[[str], None]] = None
        self._on_log: Optional[Callable[[str], None]] = None
        self._audio_buffer = np.array([], dtype=np.float32)
        self._lock = threading.Lock()
        self._running = False
        self._process_thread: Optional[threading.Thread] = None
        self._sample_rate = 16000
        self._transcribe_count = 0
        self._prev_prompt = ""  # Context hint for next chunk
        self.model_size = "small"
        self.language: Optional[str] = "es"
        self.task: str = "transcribe"

    # Map model names to their approximate sizes for progress display
    MODEL_SIZES_MB = {"tiny": 75, "base": 150, "small": 500, "medium": 1500}

    def load_model(
        self,
        model_path: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        import whisper

        log = on_log or (lambda s: None)
        self._on_log = on_log
        cancel = cancel_event or threading.Event()
        model_size = model_path or self.model_size
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "openai-whisper")
        os.makedirs(cache_dir, exist_ok=True)

        expected_mb = self.MODEL_SIZES_MB.get(model_size, 0)
        log(f"Motor: OpenAI Whisper (oficial, PyTorch)")
        log(f"Modelo: {model_size} (~{expected_mb} MB)")
        log(f"Fuente: servidores de OpenAI (openaipublic.azureedge.net)")
        log(f"Cache local: {cache_dir}")

        # Check if model file already exists
        model_file = os.path.join(cache_dir, f"{model_size}.pt")
        if os.path.exists(model_file):
            file_mb = os.path.getsize(model_file) / (1024 * 1024)
            log(f"Modelo encontrado en cache: {model_file} ({file_mb:.1f} MB)")
            if on_status:
                on_status(f"Cargando modelo '{model_size}' desde cache...")
        else:
            log(f"Modelo no encontrado. Descargando {model_size}.pt...")
            if on_status:
                on_status(f"Descargando modelo '{model_size}' (~{expected_mb} MB) desde OpenAI...")

        # Monitor download progress
        download_done = threading.Event()

        def monitor():
            prev_size = 0
            while not download_done.is_set() and not cancel.is_set():
                if os.path.exists(model_file):
                    size = os.path.getsize(model_file)
                    if size != prev_size:
                        mb = size / (1024 * 1024)
                        pct = min(99, int(mb * 100 / expected_mb)) if expected_mb > 0 else 0
                        log(f"Descarga: {mb:.1f}/{expected_mb} MB ({pct}%)")
                        if on_status:
                            on_status(f"Descargando '{model_size}'... {pct}% ({mb:.1f}/{expected_mb} MB)")
                        prev_size = size
                # Also check for .download temp file
                temp_file = model_file + ".download"
                if os.path.exists(temp_file):
                    size = os.path.getsize(temp_file)
                    if size != prev_size:
                        mb = size / (1024 * 1024)
                        pct = min(99, int(mb * 100 / expected_mb)) if expected_mb > 0 else 0
                        log(f"Descarga: {mb:.1f}/{expected_mb} MB ({pct}%)")
                        if on_status:
                            on_status(f"Descargando '{model_size}'... {pct}% ({mb:.1f}/{expected_mb} MB)")
                        prev_size = size
                download_done.wait(timeout=2.0)

        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()

        # Auto-detect GPU or use CPU with optimized threads
        import torch
        if torch.cuda.is_available():
            device = "cuda"
            log("GPU CUDA detectada, usando aceleracion por GPU.")
        else:
            device = "cpu"
            # Optimize CPU thread count
            cpu_threads = os.cpu_count() or 4
            torch.set_num_threads(cpu_threads)
            log(f"CPU: {cpu_threads} threads configurados.")
        self._device_str = device

        try:
            log(f"Iniciando carga del modelo en {device}...")
            self._model = whisper.load_model(model_size, device=device, download_root=cache_dir)

            # Apply INT8 dynamic quantization on CPU for ~2.7x speedup
            # (skip for 'tiny' model where quantization overhead > benefit)
            if device == "cpu" and model_size != "tiny":
                log(f"Aplicando cuantizacion INT8 al modelo (optimizacion CPU)...")
                self._model = torch.quantization.quantize_dynamic(
                    self._model, {torch.nn.Linear}, dtype=torch.qint8,
                )
                log("Cuantizacion INT8 aplicada correctamente.")
        finally:
            download_done.set()
            monitor_thread.join(timeout=3.0)

        if cancel.is_set():
            self._model = None
            log("Carga cancelada.")
            return

        log(f"Modelo OpenAI Whisper cargado correctamente ({device}).")

    def start(
        self,
        sample_rate: int,
        on_text: Callable[[str], None],
        on_partial: Callable[[str], None],
    ) -> None:
        self._sample_rate = sample_rate
        self._on_text = on_text
        self._on_partial = on_partial
        with self._lock:
            self._audio_buffer = np.array([], dtype=np.float32)
        self._prev_prompt = ""
        self._running = True
        self._process_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._process_thread.start()

    def feed_audio(self, audio_chunk: np.ndarray) -> None:
        if not self._running:
            return
        with self._lock:
            self._audio_buffer = np.concatenate([self._audio_buffer, audio_chunk])
            max_samples = int(self.MAX_BUFFER_DURATION * self._sample_rate)
            if len(self._audio_buffer) > max_samples:
                self._audio_buffer = self._audio_buffer[-max_samples:]

    def _process_loop(self) -> None:
        while self._running:
            time.sleep(self.CHUNK_DURATION)
            if not self._running:
                break
            self._transcribe_buffer()

    def _log(self, msg: str) -> None:
        if self._on_log:
            try:
                self._on_log(msg)
            except Exception:
                pass

    def _transcribe_buffer(self) -> None:
        with self._lock:
            if len(self._audio_buffer) < self._sample_rate * 1.0:
                return
            audio = self._audio_buffer.copy()
            # Clear buffer after copying - each cycle transcribes only new audio
            self._audio_buffer = np.array([], dtype=np.float32)

        self._transcribe_count += 1
        if self._transcribe_count <= 3:
            self._log(f"[OAI-Whisper] Transcribiendo chunk #{self._transcribe_count} "
                       f"({len(audio)/self._sample_rate:.1f}s, lang={self.language}, task={self.task})")

        if self._model is None:
            self._log("[OAI-Whisper] ERROR: modelo es None, no se puede transcribir")
            return

        # Show processing indicator while Whisper works
        if self._on_partial:
            self._on_partial("...")

        try:
            import whisper

            # Whisper expects float32 numpy array, padded/trimmed to 30s
            audio_padded = whisper.pad_or_trim(audio)
            mel = whisper.log_mel_spectrogram(audio_padded).to(self._model.device)

            # initial_prompt gives Whisper context from previous chunk
            prompt = self._prev_prompt[-200:] if self._prev_prompt else None

            options = whisper.DecodingOptions(
                language=self.language,
                task=self.task,
                without_timestamps=True,
                fp16=(self._device_str == "cuda"),  # FP16 on GPU, FP32 on CPU
                prompt=prompt,
                temperature=0.0,
            )
            result = whisper.decode(self._model, mel, options)
            text = result.text.strip()

            if text:
                self._prev_prompt = text
                if self._on_text:
                    self._on_text(text + " ")
        except Exception as e:
            self._log(f"[OAI-Whisper] ERROR transcripcion: {type(e).__name__}: {e}")

    def stop(self) -> None:
        self._running = False
        if self._process_thread is not None and self._process_thread.is_alive():
            self._process_thread.join(timeout=5.0)
            self._process_thread = None
        self._transcribe_buffer()

    @classmethod
    def is_available(cls) -> bool:
        try:
            import whisper  # noqa: F401
            return True
        except ImportError:
            return False

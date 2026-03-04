import os
import threading
import time
from typing import Callable, Optional

import numpy as np

from engines.base import STTEngine


class WhisperEngine(STTEngine):
    CHUNK_DURATION = 5.0  # Transcribe every N seconds (more = better quality)
    MAX_BUFFER_DURATION = 15.0  # Safety cap for buffer size

    @property
    def name(self) -> str:
        return "Whisper (preciso)"

    AVAILABLE_MODELS = {
        "tiny": "Tiny (~75 MB, rapido, menor precision)",
        "base": "Base (~150 MB, buen balance)",
        "small": "Small (~500 MB, buena precision)",
        "medium": "Medium (~1.5 GB, alta precision)",
    }

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
        self._prev_prompt = ""  # Context hint for next chunk (not for diffing)
        self.model_size = "small"
        self.language: Optional[str] = "es"
        self.task: str = "transcribe"

    def load_model(
        self,
        model_path: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        # Increase HuggingFace Hub timeouts before importing faster_whisper
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "300")
        os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "60")

        from faster_whisper import WhisperModel

        log = on_log or (lambda s: None)
        self._on_log = on_log
        cancel = cancel_event or threading.Event()
        model_size = model_path or self.model_size
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "whisper")
        os.makedirs(cache_dir, exist_ok=True)

        # Clean up stale .incomplete files that block HuggingFace downloads
        self._cleanup_hf_cache(cache_dir, model_size, log)

        repo_id = f"Systran/faster-whisper-{model_size}"
        log(f"Motor: faster-whisper (CTranslate2)")
        log(f"Modelo: {model_size}")
        log(f"Fuente: HuggingFace ({repo_id})")
        log(f"Cache local: {cache_dir}")
        log(f"Timeouts HF: descarga={os.environ.get('HF_HUB_DOWNLOAD_TIMEOUT')}s, "
            f"etag={os.environ.get('HF_HUB_ETAG_TIMEOUT')}s")

        if on_status:
            on_status(f"Descargando/cargando modelo '{model_size}' desde HuggingFace...")

        # Monitor download progress in background (only for the specific model)
        model_cache_dir = os.path.join(
            cache_dir, f"models--Systran--faster-whisper-{model_size}"
        )
        download_done = threading.Event()
        baseline_size = self._dir_size(model_cache_dir) if os.path.isdir(model_cache_dir) else 0

        def monitor():
            prev_size = 0
            while not download_done.is_set() and not cancel.is_set():
                try:
                    if os.path.isdir(model_cache_dir):
                        total = self._dir_size(model_cache_dir)
                        new_bytes = total - baseline_size
                        if new_bytes > 0 and new_bytes != prev_size:
                            mb = new_bytes / (1024 * 1024)
                            log(f"Descarga en progreso... {mb:.1f} MB descargados")
                            if on_status:
                                on_status(f"Descargando modelo '{model_size}'... {mb:.1f} MB")
                            prev_size = new_bytes
                except Exception:
                    pass
                download_done.wait(timeout=3.0)

        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()

        # Auto-detect GPU (CUDA) or fall back to CPU with INT8 quantization
        device = "cpu"
        compute = "int8"  # INT8 is ~35% faster than float32 on CPU + less RAM
        try:
            import ctranslate2
            if "cuda" in ctranslate2.get_supported_compute_types("cuda"):
                device = "cuda"
                compute = "float16"  # FP16 is optimal on GPU
                log("GPU CUDA detectada, usando aceleracion por GPU.")
        except Exception:
            pass

        # Use physical CPU cores for optimal threading
        cpu_threads = os.cpu_count() or 4
        log(f"Dispositivo: {device} | Precision: {compute} | CPU threads: {cpu_threads}")

        try:
            log("Iniciando descarga/carga del modelo (esto puede tardar)...")
            self._model = WhisperModel(
                model_size,
                device=device,
                compute_type=compute,
                download_root=cache_dir,
                cpu_threads=cpu_threads,
            )
        finally:
            download_done.set()
            monitor_thread.join(timeout=3.0)

        if cancel.is_set():
            self._model = None
            log("Carga cancelada.")
            return

        log(f"Modelo faster-whisper cargado correctamente ({device}, {compute}).")

    @staticmethod
    def _cleanup_hf_cache(cache_dir: str, model_size: str, log) -> None:
        """Clean up corrupted/incomplete HuggingFace cache for a model.

        - Removes ALL .incomplete files (failed downloads)
        - Removes orphan .lock files
        - If the model dir has ONLY .incomplete blobs and no real files,
          deletes the entire model directory so HF can start fresh.
        """
        import shutil

        model_dir = os.path.join(
            cache_dir, f"models--Systran--faster-whisper-{model_size}"
        )
        blobs_dir = os.path.join(model_dir, "blobs")

        if os.path.isdir(blobs_dir):
            incomplete_count = 0
            real_count = 0
            for fname in os.listdir(blobs_dir):
                if fname.endswith(".incomplete"):
                    incomplete_count += 1
                    fpath = os.path.join(blobs_dir, fname)
                    try:
                        os.remove(fpath)
                        log(f"Eliminado descarga incompleta: {fname}")
                    except OSError:
                        pass
                else:
                    real_count += 1

            # If all blobs were .incomplete (no real files), the model is
            # corrupted — delete the entire model dir for a clean re-download
            if incomplete_count > 0 and real_count == 0:
                log(f"Modelo '{model_size}' corrupto (solo archivos incompletos). "
                    f"Eliminando cache para re-descarga limpia...")
                try:
                    shutil.rmtree(model_dir)
                    log(f"Cache del modelo '{model_size}' eliminada.")
                except OSError as e:
                    log(f"No se pudo eliminar cache: {e}")

        locks_dir = os.path.join(
            cache_dir, ".locks", f"models--Systran--faster-whisper-{model_size}"
        )
        if os.path.isdir(locks_dir):
            for fname in os.listdir(locks_dir):
                if fname.endswith(".lock"):
                    try:
                        os.remove(os.path.join(locks_dir, fname))
                        log(f"Eliminado lock huerfano: {fname}")
                    except OSError:
                        pass

    @staticmethod
    def _dir_size(path: str) -> int:
        total = 0
        for dirpath, _dirnames, filenames in os.walk(path):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
        return total

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
            self._log(f"[FW] Transcribiendo chunk #{self._transcribe_count} "
                       f"({len(audio)/self._sample_rate:.1f}s, lang={self.language}, task={self.task})")

        if self._model is None:
            self._log("[FW] ERROR: modelo es None, no se puede transcribir")
            return

        try:
            # initial_prompt gives Whisper context of what was said before,
            # improving accuracy across chunk boundaries without re-transcribing
            prompt = self._prev_prompt[-200:] if self._prev_prompt else None

            segments_gen, _ = self._model.transcribe(
                audio,
                language=self.language,
                task=self.task,
                beam_size=1,  # Greedy decoding: ~2-3x faster, minimal quality loss
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
                initial_prompt=prompt,
                condition_on_previous_text=False,  # We manage context via initial_prompt
                temperature=0.0,
                no_speech_threshold=0.6,
            )

            # Stream segments progressively: show each segment as partial (grey)
            # then emit final text all at once
            collected = []
            for seg in segments_gen:
                text = seg.text.strip()
                if text:
                    collected.append(text)
                    if self._on_partial:
                        # Show accumulated text so far as grey partial
                        self._on_partial(" ".join(collected))

            full_text = " ".join(collected).strip()

            if full_text:
                # Save as context hint for next chunk
                self._prev_prompt = full_text
                if self._on_text:
                    self._on_text(full_text + " ")
        except Exception as e:
            self._log(f"[FW] ERROR transcripcion: {type(e).__name__}: {e}")

    def stop(self) -> None:
        self._running = False
        if self._process_thread is not None and self._process_thread.is_alive():
            self._process_thread.join(timeout=5.0)
            self._process_thread = None
        # Final transcription
        self._transcribe_buffer()

    @classmethod
    def is_available(cls) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

from abc import ABC, abstractmethod
from typing import Callable, Optional

import numpy as np


class STTEngine(ABC):
    """Abstract base class for speech-to-text engines."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable engine name for the UI dropdown."""
        ...

    @abstractmethod
    def load_model(
        self,
        model_path: Optional[str] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Load or download the model. May take time on first run.

        Args:
            model_path: Override default model location.
            on_status: Callback to report progress messages.
        """
        ...

    @abstractmethod
    def start(
        self,
        sample_rate: int,
        on_text: Callable[[str], None],
        on_partial: Callable[[str], None],
    ) -> None:
        """Start a recognition session.

        Args:
            sample_rate: Audio sample rate (typically 16000).
            on_text: Called with finalized transcription text.
            on_partial: Called with interim/partial text.
        """
        ...

    @abstractmethod
    def feed_audio(self, audio_chunk: np.ndarray) -> None:
        """Feed a chunk of audio data. Called from the audio thread.

        Args:
            audio_chunk: 1D numpy float32 array, mono.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop recognition and flush pending audio."""
        ...

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """Check if the engine's dependencies are installed."""
        ...

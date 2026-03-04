from engines.base import STTEngine

ENGINE_CLASSES: list[type[STTEngine]] = []

try:
    from engines.vosk_engine import VoskEngine
    ENGINE_CLASSES.append(VoskEngine)
except ImportError:
    pass

try:
    from engines.whisper_engine import WhisperEngine
    ENGINE_CLASSES.append(WhisperEngine)
except ImportError:
    pass

try:
    from engines.openai_whisper_engine import OpenAIWhisperEngine
    ENGINE_CLASSES.append(OpenAIWhisperEngine)
except ImportError:
    pass

try:
    from engines.deepgram_engine import DeepgramEngine
    ENGINE_CLASSES.append(DeepgramEngine)
except ImportError:
    pass


def get_available_engines() -> dict[str, type[STTEngine]]:
    """Return dict of engine_name -> engine_class for installed engines."""
    result: dict[str, type[STTEngine]] = {}
    for cls in ENGINE_CLASSES:
        if cls.is_available():
            instance = cls()
            result[instance.name] = cls
    return result

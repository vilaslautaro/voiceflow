import json
import os
from typing import Optional

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

_DEFAULTS = {
    # AI provider / keys
    "ai_provider": "OpenAI",
    "openai_api_key": "",
    "anthropic_api_key": "",
    "deepgram_api_key": "",
    "ollama_model": "llama3",
    "ollama_url": "http://localhost:11434",
    # App settings (persisted across sessions)
    "engine": "",
    "whisper_model": "small",
    "language": "Espanol",
    "translate_to": "Ninguno",
    "direct_mode": True,
    "ai_edit_mode": False,
    "activation_mode": "Mantener (PTT)",
    "audio_source": "Microfono",
    "sound_enabled": True,
    "hotkey": "",  # empty = platform default (Alt+Z win, Ctrl+Cmd mac)
}


def get_config() -> dict:
    """Load configuration from config.json, merging with defaults."""
    config = dict(_DEFAULTS)
    if os.path.isfile(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                stored = json.load(f)
            config.update(stored)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_config(config: dict) -> None:
    """Persist configuration to config.json."""
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def get_api_key(provider: str) -> Optional[str]:
    """Return the API key for the given provider, or None if empty."""
    config = get_config()
    key_map = {
        "OpenAI": "openai_api_key",
        "Anthropic": "anthropic_api_key",
        "Deepgram": "deepgram_api_key",
    }
    field = key_map.get(provider)
    if field:
        val = config.get(field, "")
        return val if val else None
    return None


def save_api_key(provider: str, key: str) -> None:
    """Save an API key for the given provider."""
    config = get_config()
    key_map = {
        "OpenAI": "openai_api_key",
        "Anthropic": "anthropic_api_key",
        "Deepgram": "deepgram_api_key",
    }
    field = key_map.get(provider)
    if field:
        config[field] = key
        save_config(config)

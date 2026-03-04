from abc import ABC, abstractmethod
from typing import Callable, Optional
import json
import urllib.request
import urllib.error

from postprocessing.config import get_config, get_api_key

SYSTEM_PROMPT = (
    "Eres un editor de texto. Tu tarea es pulir texto dictado por voz.\n"
    "Reglas:\n"
    "1. Elimina muletillas: \"eh\", \"um\", \"este\", \"o sea\", \"bueno\", "
    "\"digamos\", \"basicamente\", \"como que\", \"pues\", \"a ver\", \"uh\", "
    "\"ah\", \"mmm\", \"entonces\", \"like\", \"you know\"\n"
    "2. Corrige gramatica, ortografia y puntuacion\n"
    "3. Mantiene el significado y tono original intactos\n"
    "4. NO agregues contenido nuevo ni cambies el mensaje\n"
    "5. Responde SOLO con el texto pulido, sin explicaciones ni comentarios"
)


class AIEditor(ABC):
    """Abstract base class for AI text editors."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def polish(
        self,
        raw_text: str,
        language: str = "es",
        on_log: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Polish dictated text. Returns original text on failure."""
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if this editor has valid configuration (API key, server, etc.)."""
        ...

    def test_connection(self, on_log: Optional[Callable[[str], None]] = None) -> bool:
        """Test if the editor can reach its backend. Returns True on success."""
        try:
            result = self.polish("esto eh es una prueba pues", "es", on_log)
            return result != "" and result != "esto eh es una prueba pues"
        except Exception:
            return False


class OpenAIEditor(AIEditor):
    """AI editor using OpenAI GPT-4o-mini."""

    @property
    def name(self) -> str:
        return "OpenAI"

    def is_configured(self) -> bool:
        return get_api_key("OpenAI") is not None

    def polish(
        self,
        raw_text: str,
        language: str = "es",
        on_log: Optional[Callable[[str], None]] = None,
    ) -> str:
        log = on_log or (lambda s: None)
        if not raw_text.strip():
            return raw_text

        api_key = get_api_key("OpenAI")
        if not api_key:
            log("[AI Edit] No hay API key de OpenAI configurada.")
            return raw_text

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=15.0)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": raw_text},
                ],
                temperature=0.3,
                max_tokens=len(raw_text) * 2,
            )
            polished = response.choices[0].message.content.strip()
            log(f"[AI Edit] OpenAI: {len(raw_text)} chars → {len(polished)} chars")
            return polished
        except Exception as e:
            log(f"[AI Edit] Error OpenAI: {type(e).__name__}: {e}")
            return raw_text


class AnthropicEditor(AIEditor):
    """AI editor using Anthropic Claude Haiku."""

    @property
    def name(self) -> str:
        return "Anthropic"

    def is_configured(self) -> bool:
        return get_api_key("Anthropic") is not None

    def polish(
        self,
        raw_text: str,
        language: str = "es",
        on_log: Optional[Callable[[str], None]] = None,
    ) -> str:
        log = on_log or (lambda s: None)
        if not raw_text.strip():
            return raw_text

        api_key = get_api_key("Anthropic")
        if not api_key:
            log("[AI Edit] No hay API key de Anthropic configurada.")
            return raw_text

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=len(raw_text) * 2,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": raw_text},
                ],
            )
            polished = response.content[0].text.strip()
            log(f"[AI Edit] Anthropic: {len(raw_text)} chars → {len(polished)} chars")
            return polished
        except Exception as e:
            log(f"[AI Edit] Error Anthropic: {type(e).__name__}: {e}")
            return raw_text


class OllamaEditor(AIEditor):
    """AI editor using local Ollama server."""

    @property
    def name(self) -> str:
        return "Ollama"

    def is_configured(self) -> bool:
        config = get_config()
        url = config.get("ollama_url", "http://localhost:11434")
        try:
            req = urllib.request.Request(f"{url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3):
                return True
        except Exception:
            return False

    def polish(
        self,
        raw_text: str,
        language: str = "es",
        on_log: Optional[Callable[[str], None]] = None,
    ) -> str:
        log = on_log or (lambda s: None)
        if not raw_text.strip():
            return raw_text

        config = get_config()
        url = config.get("ollama_url", "http://localhost:11434")
        model = config.get("ollama_model", "llama3")

        try:
            payload = json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": raw_text},
                ],
                "stream": False,
                "options": {"temperature": 0.3},
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            polished = data["message"]["content"].strip()
            log(f"[AI Edit] Ollama ({model}): {len(raw_text)} chars → {len(polished)} chars")
            return polished
        except Exception as e:
            log(f"[AI Edit] Error Ollama: {type(e).__name__}: {e}")
            return raw_text

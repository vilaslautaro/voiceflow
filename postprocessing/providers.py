from postprocessing.editor import AIEditor, OpenAIEditor, AnthropicEditor, OllamaEditor


def get_available_editors() -> dict[str, type[AIEditor]]:
    """Return editors whose dependencies are installed.

    OpenAI: available if `openai` package is importable.
    Anthropic: available if `anthropic` package is importable.
    Ollama: always available (uses stdlib urllib, no extra SDK).
    """
    editors: dict[str, type[AIEditor]] = {}

    try:
        import openai  # noqa: F401
        editors["OpenAI"] = OpenAIEditor
    except ImportError:
        pass

    try:
        import anthropic  # noqa: F401
        editors["Anthropic"] = AnthropicEditor
    except ImportError:
        pass

    # Ollama uses stdlib urllib — always available
    editors["Ollama"] = OllamaEditor

    return editors

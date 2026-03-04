# VoiceFlow

**Open-source real-time voice-to-text desktop app for Windows.** Dictate into any application with a global hotkey. Supports local and cloud STT engines, offline translation, and AI-powered text polishing.

---

## Features

- **Real-time speech-to-text** with multiple STT engines (local & cloud)
- **Global hotkey** (Ctrl+Win) — works from any application, push-to-talk or toggle mode
- **Direct typing** into the active window (like Wispr Flow)
- **Offline translation** between 10+ languages via ArgosTranslate
- **AI text polishing** — removes filler words, fixes grammar automatically
- **Floating overlay** with real-time audio waveform visualization
- **System audio capture** (loopback) in addition to microphone
- **OLED-optimized dark UI** built with CustomTkinter

## STT Engines

| Engine | Type | Description |
|---|---|---|
| **Vosk** | Local | Lightweight, real-time streaming, ideal for quick dictation |
| **Faster Whisper** | Local | High accuracy, models: tiny/base/small/medium, GPU accelerated |
| **OpenAI Whisper** | Local | Official OpenAI implementation |
| **Deepgram Nova-3** | Cloud | Professional-grade STT via WebSocket (requires API key) |

## AI Text Polishing

| Provider | Model | Type |
|---|---|---|
| **OpenAI** | GPT-4o-mini | Cloud (API key) |
| **Anthropic** | Claude Haiku | Cloud (API key) |
| **Ollama** | Any local model | Local (no API key) |

## Languages

Transcription and translation between: Spanish, English, French, Portuguese, German, Italian, Chinese, Japanese, Korean, Russian.

## Installation

```bash
git clone https://github.com/vilaslautaro/voiceflow.git
cd voiceflow

pip install -r requirements.txt
```

> STT models are downloaded automatically on first use.

### Requirements

- Python 3.10+
- Windows 10/11
- Microphone (for voice dictation)

## Usage

```bash
# From terminal
python main.py

# Or double-click "VoiceFlow.bat" (Windows, no console)
```

### Controls

| Action | How |
|---|---|
| Start/stop recording | **Ctrl + Win** (toggle mode) |
| Push-to-talk | **Hold Ctrl + Win**, release to stop |
| Floating overlay | Always visible, click to record |
| Audio source | Microphone or system audio (loopback) |

### Cloud Engines Setup

For **Deepgram Nova-3**: Get an API key at [console.deepgram.com](https://console.deepgram.com), then enter it in the app when selecting the Deepgram engine.

For **AI polishing** (OpenAI/Anthropic): Configure API keys via the "Config AI" button in the app.

## Project Structure

```
voiceflow/
├── main.py                 # Entry point
├── hotkey_hook.py          # Native Win32 keyboard hook (Ctrl+Win)
├── gui/app.py              # Desktop UI (CustomTkinter)
├── audio/capture.py        # Audio capture (mic + system loopback)
├── engines/                # STT engines
│   ├── vosk_engine.py      #   Vosk (local, streaming)
│   ├── whisper_engine.py   #   Faster Whisper (local, GPU)
│   ├── openai_whisper_engine.py  # OpenAI Whisper (local)
│   └── deepgram_engine.py  #   Deepgram Nova-3 (cloud, WebSocket)
├── translation/            # Offline translation (ArgosTranslate)
├── postprocessing/         # AI text polishing
│   ├── editor.py           #   OpenAI / Anthropic / Ollama editors
│   ├── providers.py        #   Provider registry
│   └── config.py           #   Configuration & API key management
├── requirements.txt
└── LICENSE
```

## How It Works

1. **Audio capture** — Records from microphone or system audio at 16kHz mono
2. **STT engine** — Streams audio chunks to the selected engine for real-time transcription
3. **Translation** (optional) — Translates text offline using ArgosTranslate
4. **AI polishing** (optional) — Sends transcription to an LLM to clean up filler words and fix grammar
5. **Output** — Displays text in the app and optionally types it directly into the active window

The native Win32 keyboard hook (`WH_KEYBOARD_LL`) intercepts the Ctrl+Win hotkey at the OS level, suppressing it before Windows can process it (preventing Start menu, Live Captions, etc.).

## License

CC BY-NC-SA 4.0 — Free to use and modify, no commercial use. See [LICENSE](LICENSE).

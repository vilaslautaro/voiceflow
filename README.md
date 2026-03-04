# VoiceFlow

**Open-source real-time voice-to-text desktop app for Windows and macOS.** Dictate into any application with a global hotkey. Supports local and cloud STT engines, offline translation, and AI-powered text polishing.

---

## Features

- **Real-time speech-to-text** with multiple STT engines (local & cloud)
- **Global hotkey** — works from any application, push-to-talk or toggle mode
  - Windows: **Ctrl + Win**
  - macOS: **Ctrl + Cmd**
- **Direct typing** into the active window (like Wispr Flow)
- **Offline translation** between 10+ languages via ArgosTranslate
- **AI text polishing** — removes filler words, fixes grammar automatically
- **Floating overlay** with real-time audio waveform visualization
- **System audio capture** (loopback) in addition to microphone
- **OLED-optimized dark UI** built with CustomTkinter
- **Cross-platform** — Windows 10/11 and macOS 12+

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
- **Windows** 10/11 or **macOS** 12+ (Monterey or later)
- Microphone (for voice dictation)

### macOS Setup

1. **Accessibility permissions** — Required for the global hotkey to work. Go to **System Settings > Privacy & Security > Accessibility** and add your terminal app (Terminal, iTerm2, etc.) or Python.

2. **System audio capture** (optional) — Loopback audio on macOS requires a virtual audio device such as [BlackHole](https://github.com/ExistentialAudio/BlackHole). Install it to use the "Audio PC" source.

## Usage

```bash
# From terminal
python main.py

# Windows: double-click "VoiceFlow.bat" (no console)
```

### Controls

| Action | Windows | macOS |
|---|---|---|
| Start/stop recording (toggle) | **Ctrl + Win** | **Ctrl + Cmd** |
| Push-to-talk (hold to talk) | **Hold Ctrl + Win** | **Hold Ctrl + Cmd** |
| Floating overlay | Always visible, click to record | Same |
| Audio source | Microphone or system audio (loopback) | Microphone (loopback requires BlackHole) |

### Cloud Engines Setup

For **Deepgram Nova-3**: Get an API key at [console.deepgram.com](https://console.deepgram.com), then enter it in the app when selecting the Deepgram engine.

For **AI polishing** (OpenAI/Anthropic): Configure API keys via the "Config AI" button in the app.

## Project Structure

```
voiceflow/
├── main.py                 # Entry point
├── hotkey_hook.py          # Cross-platform keyboard hook (Win32 / Quartz)
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

### Platform-specific details

- **Windows**: A native Win32 keyboard hook (`WH_KEYBOARD_LL`) intercepts the Ctrl+Win hotkey at the OS level, suppressing it before Windows can process it (preventing Start menu, Live Captions, etc.).
- **macOS**: A Quartz CGEventTap intercepts the Ctrl+Cmd hotkey at the session level. Requires Accessibility permissions to function.

## License

CC BY-NC-SA 4.0 — Free to use and modify, no commercial use. See [LICENSE](LICENSE).

import sys
import threading
import time
import tkinter as tk

import customtkinter as ctk

from pynput.keyboard import Controller as KbController

from audio.capture import AudioCapture, LoopbackCapture, list_input_devices
from engines import get_available_engines
from engines.base import STTEngine
from postprocessing.providers import get_available_editors
from datetime import datetime
from postprocessing.config import get_config, save_config, get_api_key, save_api_key
from postprocessing.history import (
    load_history, create_session, add_entry, update_entry_polished,
    finalize_session, save_session_to_history, delete_session,
    clear_history, get_session_preview,
)
from audio.sounds import play_sound
from hotkey_hook import HotkeyHook, format_hotkey_display, DEFAULT_HOTKEY, HOTKEY_PRESETS

# Cross-platform detection
_IS_MAC = sys.platform == "darwin"
_IS_WIN = sys.platform == "win32"

# Cross-platform font families
_UI = "Helvetica Neue" if _IS_MAC else "Segoe UI"
_UI_SEMI = "Helvetica Neue" if _IS_MAC else "Segoe UI Semibold"
_MONO = "Menlo" if _IS_MAC else "Cascadia Code"

LANGUAGE_MAP = {
    "Auto": None,
    "Espanol": "es",
    "Ingles": "en",
    "Frances": "fr",
    "Portugues": "pt",
    "Aleman": "de",
    "Italiano": "it",
    "Chino": "zh",
    "Japones": "ja",
    "Coreano": "ko",
    "Ruso": "ru",
}

# --- Premium Dark Design System ---
COLORS = {
    "bg":            "#0B0B0F",
    "sidebar":       "#12121A",
    "surface":       "#1A1A28",
    "surface_hover": "#222236",
    "input_bg":      "#16162A",
    "border":        "#2A2A42",
    "border_focus":  "#00D4AA",
    "text":          "#EAEAF0",
    "text_sec":      "#8B8BA8",
    "text_dim":      "#55557A",
    "accent":        "#00D4AA",
    "accent_hover":  "#00F0C0",
    "accent_muted":  "#003830",
    "success":       "#00E676",
    "warning":       "#FFB300",
    "danger":        "#FF5252",
    "danger_hover":  "#FF7B7B",
    "recording":     "#FF3D3D",
    "recording_bg":  "#3D1010",
    "section_header":"#9090B0",
}

FONT_TITLE = (_UI, 18, "bold")
FONT_SECTION = (_UI_SEMI, 10)
FONT_BODY = (_UI, 10)
FONT_SMALL = (_UI, 9)
FONT_MONO = (_MONO, 9)
FONT_TEXT = (_UI, 12)
FONT_LOG = (_MONO, 9)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")

        self.title("VoiceFlow")
        self.geometry("1080x640")
        self.minsize(900, 520)
        self.configure(fg_color=COLORS["bg"])

        self._engines = get_available_engines()
        self._engine: STTEngine | None = None
        self._audio_capture: AudioCapture | LoopbackCapture | None = None
        self._translator = None
        self._recording = False
        self._model_loaded = False
        self._loading = False
        self._cancel_event = threading.Event()
        self._partial_tag_start: str | None = None
        self._kb = KbController()
        self._direct_mode = tk.BooleanVar(value=True)
        self._log_visible = False
        self._blink_id: str | None = None
        self._has_placeholder = False
        self._mic_devices: list[dict] = []

        # AI Auto-Editing
        self._ai_edit_mode = tk.BooleanVar(value=False)
        self._ai_editors = get_available_editors()
        self._ai_editor = None
        self._session_raw_text = ""
        self._session_text_start = "1.0"

        # Sound feedback
        self._sound_enabled = tk.BooleanVar(value=True)

        # Session history
        self._history_data = load_history()
        self._current_session: dict | None = None
        self._current_entry: dict | None = None
        self._history_sidebar_visible = False
        self._viewing_history = False

        # Push-to-Talk state
        self._ptt_active = False
        self._last_hotkey_time = 0.0

        # Pre-compute hotkey display (needed by _set_placeholder)
        _cfg = get_config()
        self._hotkey_combo = _cfg.get("hotkey", "") or DEFAULT_HOTKEY
        self._hotkey_display = format_hotkey_display(self._hotkey_combo)

        self._build_ui()
        self._set_dark_titlebar()
        self._set_placeholder()

        # Restore persisted app settings
        self._restore_config()

        self._log("App iniciada.")
        self._log(f"Motores detectados: {', '.join(self._engines.keys()) or 'ninguno'}")
        self._log(f"Editores AI: {', '.join(self._ai_editors.keys()) or 'ninguno'}")

        # Create current session for history
        self._current_session = create_session(
            engine=self._engine_var.get(),
            language=self._language_var.get(),
        )

        if not self._engines:
            self._set_status("No hay motores instalados.", "red")

        # Floating overlay (always visible, multi-state)
        self._overlay: ctk.CTkToplevel | None = None
        self._overlay_recording = False
        self._ov_state = "idle"
        self._ov_anim_id = None
        self._ov_hover_leave_id = None
        self._ov_audio_levels = [0.0] * 20  # real-time audio RMS per band
        self.after(300, self._create_overlay)

        # Cross-platform keyboard hook (configurable combo)
        self._hotkey_hook = HotkeyHook(
            combo=self._hotkey_combo,
            on_hotkey_down=self._on_hotkey_down,
            on_hotkey_up=self._on_hotkey_up,
        )
        self._hotkey_hook.start()
        self._log(f"Hook de teclado instalado ({self._hotkey_display}).")

    # ──────────────────────────────────────────────
    #  Dark Titlebar (Windows only — macOS uses native dark mode)
    # ──────────────────────────────────────────────

    def _set_dark_titlebar(self, window=None) -> None:
        if not _IS_WIN:
            return
        try:
            import ctypes
            w = window or self
            w.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(w.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
        except Exception:
            pass

    # ──────────────────────────────────────────────
    #  Build UI
    # ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Main horizontal layout: sidebar + content
        self._main_container = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        self._main_container.pack(fill="both", expand=True)
        self._main_container.grid_columnconfigure(1, weight=1)
        self._main_container.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_panel()
        self._build_history_sidebar()

    # ── SIDEBAR ──────────────────────────────────

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(
            self._main_container, width=270, fg_color=COLORS["sidebar"],
            corner_radius=0, border_width=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        self._sidebar = sidebar

        # Status bar at very bottom of sidebar
        self._status_bar = ctk.CTkFrame(
            sidebar, height=3, fg_color=COLORS["success"], corner_radius=0)
        self._status_bar.pack(fill="x", side="bottom")

        # Content frame (no scroll - everything fits)
        content = ctk.CTkFrame(sidebar, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=14, pady=(12, 8))

        # ── Header: Title + Status ──
        ctk.CTkLabel(
            content, text="VoiceFlow",
            font=(_UI, 15, "bold"),
            text_color=COLORS["text"], anchor="w"
        ).pack(anchor="w")

        status_row = ctk.CTkFrame(content, fg_color="transparent")
        status_row.pack(fill="x", pady=(2, 0))

        self._status_dot = tk.Canvas(
            status_row, width=8, height=8,
            bg=COLORS["sidebar"], highlightthickness=0, bd=0)
        self._status_dot.pack(side="left", padx=(0, 5), pady=2)
        self._dot_item = self._status_dot.create_oval(
            0, 0, 8, 8, fill=COLORS["success"], outline="")

        self._lbl_status = ctk.CTkLabel(
            status_row, text="Listo", font=(_UI, 8),
            text_color=COLORS["success"], anchor="w")
        self._lbl_status.pack(side="left", fill="x", expand=True)

        # ── Separator ──
        ctk.CTkFrame(content, height=1, fg_color=COLORS["border"]).pack(
            fill="x", pady=(8, 6))

        # ── MOTOR ──
        self._build_section_label(content, "MOTOR")

        engine_names = list(self._engines.keys())
        self._engine_var = tk.StringVar(value=engine_names[0] if engine_names else "")
        self._combo_engine = ctk.CTkOptionMenu(
            content, variable=self._engine_var,
            values=engine_names or ["(ninguno)"],
            command=lambda _: self._on_engine_change(),
            fg_color=COLORS["input_bg"], button_color=COLORS["border"],
            button_hover_color=COLORS["surface_hover"],
            dropdown_fg_color=COLORS["surface"],
            dropdown_hover_color=COLORS["accent_muted"],
            dropdown_text_color=COLORS["text"],
            text_color=COLORS["text"], font=FONT_SMALL,
            dropdown_font=FONT_SMALL, corner_radius=6, height=28)
        self._combo_engine.pack(fill="x", pady=(0, 3))

        # Model selector (Whisper only) - inline row
        self._model_frame = ctk.CTkFrame(content, fg_color="transparent")
        self._model_frame.pack(fill="x", pady=(0, 3))

        ctk.CTkLabel(self._model_frame, text="Modelo", font=(_UI, 8),
                     text_color=COLORS["text_dim"]).pack(side="left", padx=(0, 6))

        self._whisper_model_var = tk.StringVar(value="small")
        self._combo_whisper_model = ctk.CTkOptionMenu(
            self._model_frame, variable=self._whisper_model_var,
            values=["tiny", "base", "small", "medium"],
            command=lambda _: self._on_whisper_model_change(),
            fg_color=COLORS["input_bg"], button_color=COLORS["border"],
            button_hover_color=COLORS["surface_hover"],
            dropdown_fg_color=COLORS["surface"],
            dropdown_hover_color=COLORS["accent_muted"],
            dropdown_text_color=COLORS["text"],
            text_color=COLORS["text"], font=FONT_SMALL,
            dropdown_font=FONT_SMALL, corner_radius=6, height=28)
        self._combo_whisper_model.pack(side="left", fill="x", expand=True)

        # Deepgram API key frame (hidden by default, shown when Deepgram selected)
        self._dg_frame = ctk.CTkFrame(content, fg_color="transparent")
        # Not packed by default — _update_controls_for_engine shows it

        ctk.CTkLabel(self._dg_frame, text="API Key", font=(_UI, 8),
                     text_color=COLORS["text_dim"]).pack(side="left", padx=(0, 6))

        self._dg_key_var = tk.StringVar(value="")
        self._dg_key_entry = ctk.CTkEntry(
            self._dg_frame, textvariable=self._dg_key_var,
            show="*", placeholder_text="Deepgram API key...",
            fg_color=COLORS["input_bg"], border_color=COLORS["border"],
            text_color=COLORS["text"], font=FONT_SMALL,
            corner_radius=6, height=28)
        self._dg_key_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._dg_save_btn = ctk.CTkButton(
            self._dg_frame, text="Guardar", width=60,
            command=self._save_deepgram_key,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color="#0B0B0F", font=(_UI, 8, "bold"),
            corner_radius=6, height=28)
        self._dg_save_btn.pack(side="left")

        # Load saved key if any
        from postprocessing.config import get_api_key as _get_key
        saved_dg = _get_key("Deepgram")
        if saved_dg:
            self._dg_key_var.set(saved_dg)

        # Load engine + Cancel in a row
        btn_row = ctk.CTkFrame(content, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 4))

        self._btn_record = ctk.CTkButton(
            btn_row, text="\u26A1  Cargar Motor", command=self._on_load_engine,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color="#0B0B0F", font=(_UI, 10, "bold"),
            corner_radius=8, height=34)
        self._btn_record.pack(side="left", fill="x", expand=True)
        self._btn_pulse_id: str | None = None

        self._btn_cancel = ctk.CTkButton(
            btn_row, text="Cancelar", command=self._on_cancel,
            fg_color=COLORS["danger"], hover_color=COLORS["danger_hover"],
            text_color="white", font=(_UI, 9, "bold"),
            corner_radius=8, height=34, width=80)
        # Not packed — shown only during loading

        # ── Separator ──
        ctk.CTkFrame(content, height=1, fg_color=COLORS["border"]).pack(
            fill="x", pady=(4, 6))

        # ── AUDIO ──
        self._build_section_label(content, "AUDIO")

        self._audio_source_var = tk.StringVar(value="Microfono")
        self._seg_audio = ctk.CTkSegmentedButton(
            content, values=["Microfono", "Audio PC"],
            variable=self._audio_source_var,
            command=self._on_audio_source_change_seg,
            selected_color=COLORS["accent"],
            selected_hover_color=COLORS["accent_hover"],
            unselected_color=COLORS["input_bg"],
            unselected_hover_color=COLORS["surface_hover"],
            text_color=COLORS["text"], font=(_UI, 8),
            corner_radius=6, height=26)
        self._seg_audio.pack(fill="x", pady=(0, 4))

        # Mic selector row
        self._mic_frame = ctk.CTkFrame(content, fg_color="transparent")
        self._mic_frame.pack(fill="x", pady=(0, 4))

        self._mic_var = tk.StringVar()
        self._combo_mic = ctk.CTkOptionMenu(
            self._mic_frame, variable=self._mic_var,
            values=["(cargando...)"],
            fg_color=COLORS["input_bg"], button_color=COLORS["border"],
            button_hover_color=COLORS["surface_hover"],
            dropdown_fg_color=COLORS["surface"],
            dropdown_hover_color=COLORS["accent_muted"],
            dropdown_text_color=COLORS["text"],
            text_color=COLORS["text"], font=(_UI, 8),
            dropdown_font=(_UI, 8), corner_radius=6,
            dynamic_resizing=False, height=28)
        self._combo_mic.pack(side="left", fill="x", expand=True)

        self._btn_refresh_mic = ctk.CTkButton(
            self._mic_frame, text="\u21bb", command=self._refresh_microphones,
            fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            text_color=COLORS["text_sec"], font=FONT_BODY,
            corner_radius=6, width=30, height=28)
        self._btn_refresh_mic.pack(side="left", padx=(4, 0))

        # ── Separator ──
        ctk.CTkFrame(content, height=1, fg_color=COLORS["border"]).pack(
            fill="x", pady=(4, 6))

        # ── IDIOMA — two dropdowns side by side ──
        self._build_section_label(content, "IDIOMA")

        lang_row = ctk.CTkFrame(content, fg_color="transparent")
        lang_row.pack(fill="x", pady=(0, 4))

        # Left: Idioma
        lang_left = ctk.CTkFrame(lang_row, fg_color="transparent")
        lang_left.pack(side="left", fill="x", expand=True, padx=(0, 3))
        ctk.CTkLabel(lang_left, text="Entrada", font=(_UI, 8),
                     text_color=COLORS["text_dim"]).pack(anchor="w")
        self._language_var = tk.StringVar(value="Espanol")
        self._combo_language = ctk.CTkOptionMenu(
            lang_left, variable=self._language_var,
            values=list(LANGUAGE_MAP.keys()),
            command=lambda _: self._persist_settings(),
            fg_color=COLORS["input_bg"], button_color=COLORS["border"],
            button_hover_color=COLORS["surface_hover"],
            dropdown_fg_color=COLORS["surface"],
            dropdown_hover_color=COLORS["accent_muted"],
            dropdown_text_color=COLORS["text"],
            text_color=COLORS["text"], font=FONT_SMALL,
            dropdown_font=FONT_SMALL, corner_radius=6, height=28)
        self._combo_language.pack(fill="x")

        # Right: Traducir a
        lang_right = ctk.CTkFrame(lang_row, fg_color="transparent")
        lang_right.pack(side="left", fill="x", expand=True, padx=(3, 0))
        ctk.CTkLabel(lang_right, text="Traducir", font=(_UI, 8),
                     text_color=COLORS["text_dim"]).pack(anchor="w")
        self._translate_var = tk.StringVar(value="Ninguno")
        self._combo_translate = ctk.CTkOptionMenu(
            lang_right, variable=self._translate_var,
            values=["Ninguno", "Espanol", "Ingles", "Frances", "Portugues"],
            command=lambda _: self._persist_settings(),
            fg_color=COLORS["input_bg"], button_color=COLORS["border"],
            button_hover_color=COLORS["surface_hover"],
            dropdown_fg_color=COLORS["surface"],
            dropdown_hover_color=COLORS["accent_muted"],
            dropdown_text_color=COLORS["text"],
            text_color=COLORS["text"], font=FONT_SMALL,
            dropdown_font=FONT_SMALL, corner_radius=6, height=28)
        self._combo_translate.pack(fill="x")

        # ── Separator ──
        ctk.CTkFrame(content, height=1, fg_color=COLORS["border"]).pack(
            fill="x", pady=(4, 6))

        # ── OPCIONES — compact layout ──
        self._build_section_label(content, "OPCIONES")

        # Switches in a row
        switches_row = ctk.CTkFrame(content, fg_color="transparent")
        switches_row.pack(fill="x", pady=(0, 5))

        self._switch_direct = ctk.CTkSwitch(
            switches_row, text="Directo",
            variable=self._direct_mode,
            command=self._persist_settings,
            font=(_UI, 9), text_color=COLORS["text_sec"],
            progress_color=COLORS["accent"],
            button_color=COLORS["text"],
            button_hover_color=COLORS["accent_hover"],
            fg_color=COLORS["border"], width=40, height=18)
        self._switch_direct.pack(side="left", padx=(0, 10))

        self._switch_ai = ctk.CTkSwitch(
            switches_row, text="AI Edit",
            variable=self._ai_edit_mode,
            command=self._persist_settings,
            font=(_UI, 9), text_color=COLORS["text_sec"],
            progress_color=COLORS["accent"],
            button_color=COLORS["text"],
            button_hover_color=COLORS["accent_hover"],
            fg_color=COLORS["border"], width=40, height=18)
        self._switch_ai.pack(side="left")

        # Second row: Sound switch
        switches_row2 = ctk.CTkFrame(content, fg_color="transparent")
        switches_row2.pack(fill="x", pady=(0, 5))

        self._switch_sound = ctk.CTkSwitch(
            switches_row2, text="Sonidos",
            variable=self._sound_enabled,
            command=self._persist_settings,
            font=(_UI, 9), text_color=COLORS["text_sec"],
            progress_color=COLORS["accent"],
            button_color=COLORS["text"],
            button_hover_color=COLORS["accent_hover"],
            fg_color=COLORS["border"], width=40, height=18)
        self._switch_sound.pack(side="left")

        # Activation + Config AI in a row
        opts_row = ctk.CTkFrame(content, fg_color="transparent")
        opts_row.pack(fill="x", pady=(0, 5))

        self._activation_var = tk.StringVar(value="Mantener (PTT)")
        self._combo_activation = ctk.CTkOptionMenu(
            opts_row, variable=self._activation_var,
            values=["Mantener (PTT)", "Alternar"],
            command=lambda _: self._persist_settings(),
            fg_color=COLORS["input_bg"], button_color=COLORS["border"],
            button_hover_color=COLORS["surface_hover"],
            dropdown_fg_color=COLORS["surface"],
            dropdown_hover_color=COLORS["accent_muted"],
            dropdown_text_color=COLORS["text"],
            text_color=COLORS["text"], font=(_UI, 8),
            dropdown_font=FONT_SMALL, corner_radius=6, height=28)
        self._combo_activation.pack(side="left", fill="x", expand=True, padx=(0, 4))

        ctk.CTkButton(
            opts_row, text="Config AI", command=self._open_ai_config,
            fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            text_color=COLORS["text_sec"], font=(_UI, 8),
            corner_radius=6, height=28, width=70
        ).pack(side="left")

        # Hotkey selector
        hotkey_frame = ctk.CTkFrame(content, fg_color="transparent")
        hotkey_frame.pack(fill="x", pady=(2, 0))

        ctk.CTkLabel(hotkey_frame, text="Hotkey", font=(_UI, 8),
                     text_color=COLORS["text_dim"]).pack(side="left", padx=(0, 4))

        self._hotkey_var = tk.StringVar(value=DEFAULT_HOTKEY)
        self._combo_hotkey = ctk.CTkOptionMenu(
            hotkey_frame, variable=self._hotkey_var,
            values=HOTKEY_PRESETS,
            command=self._on_hotkey_change,
            fg_color=COLORS["accent_muted"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            dropdown_fg_color=COLORS["surface"],
            dropdown_hover_color=COLORS["accent_muted"],
            dropdown_text_color=COLORS["text"],
            text_color=COLORS["accent"], font=(_MONO, 8),
            dropdown_font=(_MONO, 8), corner_radius=6, height=26)
        self._combo_hotkey.pack(side="left", fill="x", expand=True)

        # Initialize controls
        self._refresh_microphones()
        self._update_controls_for_engine()

    # ── MAIN PANEL ───────────────────────────────

    def _build_main_panel(self) -> None:
        main = ctk.CTkFrame(
            self._main_container, fg_color=COLORS["bg"], corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        # Row 0: action bar (fixed), Row 1: text area (expands)
        main.grid_rowconfigure(0, weight=0)
        main.grid_rowconfigure(1, weight=1)

        # ── Action Bar (top) ──
        action_bar = ctk.CTkFrame(main, fg_color="transparent")
        action_bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))

        ctk.CTkLabel(
            action_bar, text="Transcripcion",
            font=(_UI_SEMI, 12),
            text_color=COLORS["text_sec"]
        ).pack(side="left")

        right_actions = ctk.CTkFrame(action_bar, fg_color="transparent")
        right_actions.pack(side="right")

        ctk.CTkButton(
            right_actions, text="Copiar", command=self._on_copy,
            fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            text_color=COLORS["text_sec"], font=(_UI, 9),
            corner_radius=6, width=65, height=28
        ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            right_actions, text="Limpiar", command=self._on_clear,
            fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            text_color=COLORS["text_sec"], font=(_UI, 9),
            corner_radius=6, width=65, height=28
        ).pack(side="left", padx=(0, 4))

        self._btn_log = ctk.CTkButton(
            right_actions, text="Log", command=self._toggle_log,
            fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            text_color=COLORS["text_sec"], font=(_UI, 9),
            corner_radius=6, width=45, height=28)
        self._btn_log.pack(side="left", padx=(0, 4))

        self._btn_history = ctk.CTkButton(
            right_actions, text="Historial", command=self._toggle_history_sidebar,
            fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            text_color=COLORS["text_sec"], font=(_UI, 9),
            corner_radius=6, width=70, height=28)
        self._btn_history.pack(side="left")

        # ── Text Area ──
        self._text_area = ctk.CTkTextbox(
            main, font=FONT_TEXT, corner_radius=12,
            fg_color=COLORS["surface"], text_color=COLORS["text"],
            border_width=1, border_color=COLORS["border"],
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["text_dim"],
            wrap="word")
        self._text_area.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 10))

        # Configure tags on the internal text widget
        self._text_area._textbox.tag_configure(
            "partial", foreground=COLORS["text_dim"])
        self._text_area._textbox.tag_configure(
            "placeholder", foreground=COLORS["text_dim"])

        self._text_area._textbox.bind(
            "<FocusIn>", lambda e: self._clear_placeholder())
        self._text_area._textbox.bind(
            "<FocusOut>", lambda e: self._set_placeholder())

        # ── Log Panel (hidden) ──
        self._log_frame = ctk.CTkFrame(
            main, fg_color=COLORS["surface"], corner_radius=10,
            border_width=1, border_color=COLORS["border"])
        # Not gridded — toggled

        log_header = ctk.CTkFrame(self._log_frame, fg_color="transparent")
        log_header.pack(fill="x", padx=10, pady=(6, 2))
        ctk.CTkLabel(log_header, text="LOG", font=(_UI_SEMI, 9),
                     text_color=COLORS["section_header"]).pack(side="left")

        self._log_text = ctk.CTkTextbox(
            self._log_frame, font=FONT_LOG, corner_radius=6,
            fg_color=COLORS["bg"], text_color=COLORS["text_dim"],
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["text_dim"],
            wrap="word", height=120, activate_scrollbars=True)
        self._log_text.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self._log_text.configure(state="disabled")

    # ── HISTORY SIDEBAR ────────────────────────────

    def _build_history_sidebar(self) -> None:
        """Build the collapsible session history sidebar (right side)."""
        self._history_panel = ctk.CTkFrame(
            self._main_container, width=280, fg_color=COLORS["sidebar"],
            corner_radius=0, border_width=0)
        # NOT gridded initially — sidebar starts hidden
        self._history_panel.grid_propagate(False)

        content = ctk.CTkFrame(self._history_panel, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=14, pady=(12, 8))

        # Header row
        header_row = ctk.CTkFrame(content, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            header_row, text="HISTORIAL", font=(_UI_SEMI, 9),
            text_color=COLORS["section_header"], anchor="w"
        ).pack(side="left")

        self._lbl_session_count = ctk.CTkLabel(
            header_row, text="0 sesiones", font=(_UI, 8),
            text_color=COLORS["text_dim"], anchor="e")
        self._lbl_session_count.pack(side="right")

        ctk.CTkFrame(content, height=1, fg_color=COLORS["border"]).pack(
            fill="x", pady=(2, 6))

        # Current session card
        self._current_session_card = ctk.CTkFrame(
            content, fg_color=COLORS["accent_muted"], corner_radius=8,
            border_width=1, border_color=COLORS["accent"])
        self._current_session_card.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            self._current_session_card, text="\u25CF SESION ACTUAL",
            font=(_UI_SEMI, 8), text_color=COLORS["accent"], anchor="w"
        ).pack(anchor="w", padx=10, pady=(6, 2))

        self._lbl_current_info = ctk.CTkLabel(
            self._current_session_card, text="Sin entradas",
            font=(_UI, 8), text_color=COLORS["text_sec"],
            anchor="w", wraplength=240)
        self._lbl_current_info.pack(anchor="w", padx=10, pady=(0, 6))

        ctk.CTkFrame(content, height=1, fg_color=COLORS["border"]).pack(
            fill="x", pady=(2, 6))

        # Scrollable session list
        self._history_scroll = ctk.CTkScrollableFrame(
            content, fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["text_dim"])
        self._history_scroll.pack(fill="both", expand=True, pady=(0, 6))

        # Bottom actions
        bottom_row = ctk.CTkFrame(content, fg_color="transparent")
        bottom_row.pack(fill="x", pady=(4, 0))

        self._btn_back_to_live = ctk.CTkButton(
            bottom_row, text="\u25C0 En vivo", command=self._exit_history_view,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color="#0B0B0F", font=(_UI, 8, "bold"),
            corner_radius=6, height=26)
        # Not packed — shown only when viewing a past session

        ctk.CTkButton(
            bottom_row, text="Limpiar todo", command=self._on_clear_history,
            fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            text_color=COLORS["text_dim"], font=(_UI, 8),
            corner_radius=6, height=26
        ).pack(side="right")

        self._refresh_history_list()

    def _toggle_history_sidebar(self) -> None:
        if self._history_sidebar_visible:
            self._history_panel.grid_forget()
            self._history_sidebar_visible = False
            self._btn_history.configure(fg_color=COLORS["surface"])
        else:
            self._history_panel.grid(row=0, column=2, sticky="nsew")
            self._history_sidebar_visible = True
            self._btn_history.configure(fg_color=COLORS["accent_muted"])
            self._refresh_history_list()

    def _refresh_history_list(self) -> None:
        for child in self._history_scroll.winfo_children():
            child.destroy()
        sessions = self._history_data.get("sessions", [])
        self._lbl_session_count.configure(text=f"{len(sessions)} sesiones")
        for session in sessions:
            self._create_session_card(self._history_scroll, session)

    def _create_session_card(self, parent, session: dict) -> None:
        card = ctk.CTkFrame(
            parent, fg_color=COLORS["surface"], corner_radius=8,
            border_width=1, border_color=COLORS["border"],
            cursor="hand2")
        card.pack(fill="x", pady=(0, 4))

        # Top row: date + delete button
        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.pack(fill="x", padx=8, pady=(6, 0))

        try:
            dt = datetime.fromisoformat(session["started_at"])
            date_str = dt.strftime("%d/%m/%Y  %H:%M")
        except Exception:
            date_str = session.get("started_at", "")

        ctk.CTkLabel(
            top_row, text=date_str, font=(_MONO, 8),
            text_color=COLORS["text_dim"], anchor="w"
        ).pack(side="left")

        sid = session["id"]
        btn_del = ctk.CTkButton(
            top_row, text="\u2715", width=20, height=18,
            fg_color="transparent", hover_color=COLORS["danger"],
            text_color=COLORS["text_dim"], font=(_UI, 9),
            corner_radius=4,
            command=lambda s=sid: self._on_delete_session(s))
        btn_del.pack(side="right")

        # Engine + Language
        meta = f"{session.get('engine', '?')} \u00b7 {session.get('language', '?')}"
        ctk.CTkLabel(
            card, text=meta, font=(_UI, 8),
            text_color=COLORS["text_sec"], anchor="w"
        ).pack(anchor="w", padx=8, pady=(1, 0))

        # Text preview
        preview = get_session_preview(session)
        if preview:
            ctk.CTkLabel(
                card, text=preview, font=(_UI, 9),
                text_color=COLORS["text"], anchor="w", wraplength=240
            ).pack(anchor="w", padx=8, pady=(3, 6))
        else:
            ctk.CTkLabel(
                card, text="(vacia)", font=(_UI, 9),
                text_color=COLORS["text_dim"], anchor="w"
            ).pack(anchor="w", padx=8, pady=(3, 6))

        # Click to view session
        def on_click(e, s=session):
            self._view_session(s)

        card.bind("<ButtonRelease-1>", on_click)
        for child in card.winfo_children():
            child.bind("<ButtonRelease-1>", on_click)
            for sub in child.winfo_children():
                if not isinstance(sub, ctk.CTkButton):
                    sub.bind("<ButtonRelease-1>", on_click)

        # Hover effect
        def on_enter(e, c=card):
            c.configure(fg_color=COLORS["surface_hover"],
                        border_color=COLORS["accent"])

        def on_leave(e, c=card):
            c.configure(fg_color=COLORS["surface"],
                        border_color=COLORS["border"])

        card.bind("<Enter>", on_enter)
        card.bind("<Leave>", on_leave)
        for child in card.winfo_children():
            child.bind("<Enter>", on_enter)
            child.bind("<Leave>", on_leave)

    def _view_session(self, session: dict) -> None:
        self._viewing_history = True
        self._text_area._textbox.delete("1.0", tk.END)

        for entry in session.get("entries", []):
            text = entry.get("polished_text") or entry.get("raw_text", "")
            if text:
                self._text_area._textbox.insert(tk.END, text)

        self._text_area.configure(state="disabled")
        self._text_area.see("1.0")
        self._btn_back_to_live.pack(side="left", padx=(0, 6))

        try:
            dt = datetime.fromisoformat(session["started_at"])
            label = dt.strftime("%d/%m %H:%M")
        except Exception:
            label = "Sesion"
        self._set_status(f"Viendo: {label}", "orange")

    def _exit_history_view(self) -> None:
        self._viewing_history = False
        self._text_area.configure(state="normal")
        self._text_area._textbox.delete("1.0", tk.END)
        self._btn_back_to_live.pack_forget()
        self._set_placeholder()
        self._set_status("Listo", "green")

    def _on_delete_session(self, session_id: str) -> None:
        delete_session(session_id)
        self._history_data = load_history()
        self._refresh_history_list()

    def _on_clear_history(self) -> None:
        clear_history()
        self._history_data = load_history()
        self._refresh_history_list()
        self._log("Historial limpiado.")

    def _update_current_session_card(self) -> None:
        if self._current_session is None:
            return
        entry_count = len(self._current_session.get("entries", []))
        preview = get_session_preview(self._current_session)
        if entry_count:
            info = f"{entry_count} entradas"
            if preview:
                info += f" \u00b7 {preview[:60]}"
            self._lbl_current_info.configure(text=info)

    # ── CONFIG PERSISTENCE ────────────────────────

    def _restore_config(self) -> None:
        """Restore persisted app settings from config.json."""
        cfg = get_config()

        # Engine
        saved_engine = cfg.get("engine", "")
        if saved_engine and saved_engine in self._engines:
            self._engine_var.set(saved_engine)
            self._update_controls_for_engine()

        # Whisper model
        self._whisper_model_var.set(cfg.get("whisper_model", "small"))

        # Language
        saved_lang = cfg.get("language", "Espanol")
        self._language_var.set(saved_lang)

        # Translation
        self._translate_var.set(cfg.get("translate_to", "Ninguno"))

        # Switches
        self._direct_mode.set(cfg.get("direct_mode", True))
        self._ai_edit_mode.set(cfg.get("ai_edit_mode", False))
        self._sound_enabled.set(cfg.get("sound_enabled", True))

        # Activation mode
        self._activation_var.set(
            cfg.get("activation_mode", "Mantener (PTT)"))

        # Audio source
        saved_audio = cfg.get("audio_source", "Microfono")
        self._audio_source_var.set(saved_audio)
        self._on_audio_source_change_seg(saved_audio)

        # Hotkey
        saved_hotkey = cfg.get("hotkey", "") or DEFAULT_HOTKEY
        self._hotkey_var.set(saved_hotkey)

    def _persist_settings(self, *_args) -> None:
        """Save current app settings to config.json (called on any change)."""
        cfg = get_config()
        cfg["engine"] = self._engine_var.get()
        cfg["whisper_model"] = self._whisper_model_var.get()
        cfg["language"] = self._language_var.get()
        cfg["translate_to"] = self._translate_var.get()
        cfg["direct_mode"] = self._direct_mode.get()
        cfg["ai_edit_mode"] = self._ai_edit_mode.get()
        cfg["sound_enabled"] = self._sound_enabled.get()
        cfg["activation_mode"] = self._activation_var.get()
        cfg["audio_source"] = self._audio_source_var.get()
        cfg["hotkey"] = self._hotkey_var.get()
        save_config(cfg)

    def _on_hotkey_change(self, new_combo: str) -> None:
        """Restart the keyboard hook with a new hotkey combo."""
        try:
            self._hotkey_hook.stop()
        except Exception:
            pass
        self._hotkey_combo = new_combo
        self._hotkey_display = format_hotkey_display(new_combo)
        self._hotkey_hook = HotkeyHook(
            combo=new_combo,
            on_hotkey_down=self._on_hotkey_down,
            on_hotkey_up=self._on_hotkey_up,
        )
        self._hotkey_hook.start()
        # Update overlay hover label
        if hasattr(self, "_ov_hover_label") and self._ov_hover_label:
            self._ov_hover_label.configure(text=self._hotkey_display)
        self._log(f"Hotkey cambiado: {self._hotkey_display}")
        self._persist_settings()

    # ── UI Helpers ───────────────────────────────

    def _build_section_label(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent, text=text, font=(_UI_SEMI, 9),
            text_color=COLORS["section_header"], anchor="w"
        ).pack(anchor="w", pady=(0, 3))

    # ──────────────────────────────────────────────
    #  Placeholder
    # ──────────────────────────────────────────────

    def _set_placeholder(self) -> None:
        content = self._text_area.get("0.0", "end-1c")
        if not content.strip():
            self._text_area._textbox.delete("1.0", tk.END)
            self._text_area._textbox.insert(
                "1.0", f"El texto transcrito aparecera aqui...\n\nPresiona {self._hotkey_display} para comenzar a dictar.",
                "placeholder")
            self._has_placeholder = True

    def _clear_placeholder(self) -> None:
        if self._has_placeholder:
            self._text_area._textbox.delete("1.0", tk.END)
            self._has_placeholder = False

    # ──────────────────────────────────────────────
    #  Microphone
    # ──────────────────────────────────────────────

    def _refresh_microphones(self) -> None:
        try:
            self._mic_devices = list_input_devices()
        except Exception as e:
            self._log(f"Error listando microfonos: {e}")
            self._mic_devices = []

        display_names = []
        default_idx = 0
        for i, dev in enumerate(self._mic_devices):
            prefix = "\u2605 " if dev["is_default"] else "  "
            display_names.append(f"{prefix}{dev['name']}")
            if dev["is_default"]:
                default_idx = i

        if display_names:
            self._combo_mic.configure(values=display_names)
            self._mic_var.set(display_names[default_idx])
            self._log(f"Microfonos detectados: {len(self._mic_devices)}")
        else:
            self._combo_mic.configure(values=["(sin microfonos)"])
            self._mic_var.set("(sin microfonos)")

    def _get_selected_mic_index(self) -> int | None:
        try:
            current_val = self._mic_var.get()
            for i, dev in enumerate(self._mic_devices):
                prefix = "\u2605 " if dev["is_default"] else "  "
                if f"{prefix}{dev['name']}" == current_val:
                    return None if dev["is_default"] else dev["index"]
        except Exception:
            pass
        return None

    def _on_audio_source_change_seg(self, value: str) -> None:
        is_mic = value == "Microfono"
        if is_mic:
            self._mic_frame.pack(fill="x")
        else:
            self._mic_frame.pack_forget()
        self._persist_settings()

    # ──────────────────────────────────────────────
    #  Status Dot & Blink
    # ──────────────────────────────────────────────

    def _update_status_dot(self, color: str) -> None:
        self._status_dot.itemconfig(self._dot_item, fill=color)

    def _blink_recording_dot(self) -> None:
        if not self._recording:
            return
        current = self._status_dot.itemcget(self._dot_item, "fill")
        nxt = COLORS["sidebar"] if current == COLORS["recording"] else COLORS["recording"]
        self._status_dot.itemconfig(self._dot_item, fill=nxt)
        self._blink_id = self.after(500, self._blink_recording_dot)

    def _stop_blink(self) -> None:
        if self._blink_id is not None:
            self.after_cancel(self._blink_id)
            self._blink_id = None

    # ──────────────────────────────────────────────
    #  Record Button Pulse Animation
    # ──────────────────────────────────────────────

    _PULSE_BRIGHT = "#FF4D4D"
    _PULSE_DIM = "#CC2020"

    def _start_btn_pulse(self) -> None:
        self._stop_btn_pulse()
        self._do_btn_pulse()

    def _do_btn_pulse(self) -> None:
        if not self._recording:
            return
        try:
            current = self._btn_record.cget("fg_color")
            nxt = self._PULSE_DIM if current == self._PULSE_BRIGHT else self._PULSE_BRIGHT
            self._btn_record.configure(fg_color=nxt, hover_color=nxt)
            self._btn_pulse_id = self.after(600, self._do_btn_pulse)
        except Exception:
            pass

    def _stop_btn_pulse(self) -> None:
        if self._btn_pulse_id is not None:
            self.after_cancel(self._btn_pulse_id)
            self._btn_pulse_id = None

    # ──────────────────────────────────────────────
    #  Log Panel
    # ──────────────────────────────────────────────

    def _toggle_log(self) -> None:
        if self._log_visible:
            self._log_frame.grid_forget()
            self._log_visible = False
            self._btn_log.configure(
                text="Log", fg_color=COLORS["surface"])
        else:
            self._log_frame.grid(
                row=2, column=0, sticky="ew", padx=14, pady=(0, 10))
            self._log_visible = True
            self._btn_log.configure(
                text="Ocultar", fg_color=COLORS["accent_muted"])

    def _log(self, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"

        def _do():
            self._log_text.configure(state="normal")
            self._log_text.insert("end", line)
            self._log_text.see("end")
            self._log_text.configure(state="disabled")
        try:
            self.after(0, _do)
        except Exception:
            pass

    # ──────────────────────────────────────────────
    #  Status & Cancel
    # ──────────────────────────────────────────────

    def _set_status(self, text: str, color: str = "green") -> None:
        cmap = {
            "green": COLORS["success"],
            "red": COLORS["danger"],
            "orange": COLORS["warning"],
        }
        c = cmap.get(color, color)
        self._lbl_status.configure(text=text, text_color=c)
        self._update_status_dot(c)
        self._status_bar.configure(fg_color=c)
        self._log(f"Estado: {text}")

    def _show_cancel_btn(self) -> None:
        self._btn_cancel.pack(side="left", padx=(4, 0))

    def _hide_cancel_btn(self) -> None:
        self._btn_cancel.pack_forget()

    def _on_cancel(self) -> None:
        self._log("Cancelacion solicitada.")
        self._cancel_event.set()
        self._set_status("Cancelando...", "orange")

    # ──────────────────────────────────────────────
    #  Push-to-Talk / Hotkey  (cross-platform hook)
    # ──────────────────────────────────────────────

    def _on_hotkey_down(self) -> None:
        """Called from the hook thread when hotkey is pressed.

        Windows: Ctrl+Win, macOS: Ctrl+Cmd.
        The hook suppresses the combo from reaching the OS.
        We dispatch to the Tk main thread via after().
        """
        now = time.time()
        if now - self._last_hotkey_time < 0.15:
            return
        self._last_hotkey_time = now

        mode = self._activation_var.get() if hasattr(self, "_activation_var") else "Alternar"
        if "Mantener" in mode:
            if not self._ptt_active and not self._loading:
                self._ptt_active = True
                self.after(0, self._on_hotkey_start_stop)
        else:
            self.after(0, self._on_hotkey_start_stop)

    def _on_hotkey_up(self) -> None:
        """Called from the hook thread when hotkey is released."""
        if self._ptt_active:
            mode = self._activation_var.get() if hasattr(self, "_activation_var") else "Alternar"
            if "Mantener" in mode:
                self._ptt_active = False
                if self._recording:
                    self.after(0, self._stop_recording)

    # ──────────────────────────────────────────────
    #  Engine Controls
    # ──────────────────────────────────────────────

    def _save_deepgram_key(self) -> None:
        """Save the Deepgram API key from the UI entry."""
        key = self._dg_key_var.get().strip()
        if key:
            save_api_key("Deepgram", key)
            self._log("Deepgram API key guardada.")
            self._set_status("API key guardada.", "green")
        else:
            self._set_status("Ingrese una API key.", "orange")

    def _update_controls_for_engine(self) -> None:
        name = self._engine_var.get()
        is_w = "Whisper" in name
        is_v = "Vosk" in name
        is_dg = "Deepgram" in name

        # Show/hide model frame based on engine type
        if is_w:
            self._model_frame.pack(fill="x", pady=(0, 6))
        else:
            self._model_frame.pack_forget()

        # Show/hide Deepgram API key frame
        if hasattr(self, "_dg_frame"):
            if is_dg:
                self._dg_frame.pack(fill="x", pady=(0, 6))
            else:
                self._dg_frame.pack_forget()

        # Enable/disable language controls
        state = "disabled" if is_v else "normal"
        self._combo_language.configure(state=state)
        self._combo_translate.configure(state=state)
        if is_v:
            self._language_var.set("Espanol")
            self._translate_var.set("Ninguno")

        # Deepgram doesn't support translate task (handled server-side)
        if is_dg:
            self._combo_translate.configure(state="disabled")
            self._translate_var.set("Ninguno")

    def _on_whisper_model_change(self) -> None:
        if self._recording:
            self._stop_recording()
        self._engine = None
        self._model_loaded = False
        self._stop_btn_pulse()
        self._btn_record.configure(
            text="\u26A1  Cargar Motor", state="normal",
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color="#0B0B0F")
        model = self._whisper_model_var.get()
        self._set_status(f"Modelo: {model}. Cargue motor o use hotkey.", "orange")
        self._persist_settings()

    def _on_engine_change(self) -> None:
        if self._recording:
            self._stop_recording()
        self._engine = None
        self._model_loaded = False
        self._stop_btn_pulse()
        self._btn_record.configure(
            text="\u26A1  Cargar Motor", state="normal",
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color="#0B0B0F")
        self._update_controls_for_engine()
        self._set_status("Motor cambiado. Cargue motor o use hotkey.", "orange")
        self._persist_settings()

    def _on_load_engine(self) -> None:
        if self._loading or self._model_loaded:
            return
        engine_name = self._engine_var.get()
        if not engine_name or engine_name not in self._engines:
            self._set_status("Seleccione un motor.", "red")
            return
        self._loading = True
        self._cancel_event.clear()
        self._btn_record.configure(state="disabled")
        self._combo_engine.configure(state="disabled")
        self.after(0, self._show_cancel_btn)
        self._engine = self._engines[engine_name]()
        if hasattr(self._engine, "model_size"):
            self._engine.model_size = self._whisper_model_var.get()
        self._configure_engine_language()
        self._log(f"Cargando motor: {engine_name}")
        threading.Thread(target=self._load_engine_only, daemon=True).start()

    def _load_engine_only(self) -> None:
        try:
            self._engine.load_model(
                on_status=lambda s: self.after(0, self._set_status, s, "orange"),
                on_log=self._log, cancel_event=self._cancel_event)
            if self._cancel_event.is_set():
                self._log("Carga cancelada.")
                self.after(0, self._reset_after_cancel)
                return
            self._preload_translation()
            self._model_loaded = True
            self._loading = False
            self.after(0, self._hide_cancel_btn)
            self.after(0, lambda: self._btn_record.configure(
                text="\u2713  Motor Listo", state="disabled",
                fg_color="#1A3D2A", hover_color="#1A3D2A",
                text_color=COLORS["success"]))
            self.after(0, self._set_status,
                       "Motor cargado. Use hotkey para dictar.", "green")
            self.after(0, self._set_overlay_engine_loaded)
        except CancelledError:
            self._log("Carga cancelada.")
            self.after(0, self._reset_after_cancel)
        except TypeError:
            try:
                self._engine.load_model(
                    on_status=lambda s: self.after(0, self._set_status, s, "orange"),
                    on_log=self._log)
                self._model_loaded = True
                self._loading = False
                self.after(0, self._hide_cancel_btn)
                self.after(0, lambda: self._btn_record.configure(
                    text="Motor Listo", state="disabled",
                    fg_color=COLORS["success"], hover_color=COLORS["success"],
                    text_color="white"))
                self.after(0, self._set_status,
                           "Motor cargado. Use hotkey para dictar.", "green")
                self.after(0, self._set_overlay_engine_loaded)
            except Exception as e:
                self._log(f"ERROR: {e}")
                self.after(0, self._reset_after_error, str(e))
        except Exception as e:
            self._log(f"ERROR: {e}")
            self.after(0, self._reset_after_error, str(e))

    def _on_hotkey_start_stop(self) -> None:
        if self._loading:
            return
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        if self._viewing_history:
            self._exit_history_view()
        engine_name = self._engine_var.get()
        if not engine_name or engine_name not in self._engines:
            self._set_status("Seleccione un motor.", "red")
            return

        if not self._model_loaded:
            self._loading = True
            self._cancel_event.clear()
            self._btn_record.configure(state="disabled")
            self._combo_engine.configure(state="disabled")
            self._engine = self._engines[engine_name]()
            if hasattr(self._engine, "model_size"):
                self._engine.model_size = self._whisper_model_var.get()
            self._configure_engine_language()
            self._log(f"Cargando motor (hotkey): {engine_name}")
            threading.Thread(target=self._load_and_start, daemon=True).start()
        else:
            self._begin_capture()

    def _load_and_start(self) -> None:
        try:
            self._engine.load_model(
                on_status=lambda s: self.after(0, self._set_status, s, "orange"),
                on_log=self._log, cancel_event=self._cancel_event)
            if self._cancel_event.is_set():
                self.after(0, self._reset_after_cancel)
                return
            self._preload_translation()
            self._model_loaded = True
            self._loading = False
            self.after(0, self._begin_capture)
        except CancelledError:
            self.after(0, self._reset_after_cancel)
        except TypeError:
            try:
                self._engine.load_model(
                    on_status=lambda s: self.after(0, self._set_status, s, "orange"),
                    on_log=self._log)
                self._model_loaded = True
                self._loading = False
                self.after(0, self._begin_capture)
            except Exception as e:
                self._log(f"ERROR: {e}")
                self.after(0, self._reset_after_error, str(e))
        except Exception as e:
            self._log(f"ERROR: {e}")
            self.after(0, self._reset_after_error, str(e))

    def _preload_translation(self) -> None:
        translate_to = LANGUAGE_MAP.get(self._translate_var.get())
        if not translate_to or translate_to == "en":
            return
        source_lang = LANGUAGE_MAP.get(self._language_var.get()) or "es"
        if source_lang == translate_to:
            return
        try:
            from translation.translator import ArgosTranslator
            if self._translator is None:
                self._translator = ArgosTranslator()
            self._translator.ensure_package(
                source_lang, translate_to,
                on_status=lambda s: self.after(0, self._set_status, s, "orange"),
                on_log=self._log)
        except Exception as e:
            self._log(f"Traduccion: {e}")

    def _reset_after_cancel(self) -> None:
        self._loading = False
        self._engine = None
        self._model_loaded = False
        self._hide_cancel_btn()
        self._stop_btn_pulse()
        self._btn_record.configure(
            text="\u26A1  Cargar Motor", state="normal",
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color="#0B0B0F")
        self._combo_engine.configure(state="normal")
        self._set_status("Cancelado.", "green")

    def _reset_after_error(self, msg: str) -> None:
        self._loading = False
        self._hide_cancel_btn()
        self._stop_btn_pulse()
        self._btn_record.configure(
            text="\u26A1  Cargar Motor", state="normal",
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color="#0B0B0F")
        self._combo_engine.configure(state="normal")
        self._set_status(f"Error: {msg}", "red")

    def _configure_engine_language(self) -> None:
        if self._engine is None:
            return
        lang = LANGUAGE_MAP.get(self._language_var.get(), "es")
        translate_to = LANGUAGE_MAP.get(self._translate_var.get())
        if hasattr(self._engine, "language"):
            self._engine.language = lang
        if hasattr(self._engine, "task"):
            self._engine.task = "translate" if (
                translate_to == "en" and lang != "en") else "transcribe"

    # ──────────────────────────────────────────────
    #  AI Config Dialog
    # ──────────────────────────────────────────────

    def _open_ai_config(self) -> None:
        dlg = ctk.CTkToplevel(self)
        dlg.title("Configuracion AI")
        dlg.geometry("420x380")
        dlg.configure(fg_color=COLORS["bg"])
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        dlg.after(100, lambda: self._set_dark_titlebar(dlg))

        config = get_config()
        available = list(self._ai_editors.keys())

        # Header
        ctk.CTkLabel(
            dlg, text="Configuracion AI", font=(_UI, 16, "bold"),
            text_color=COLORS["text"]
        ).pack(anchor="w", padx=20, pady=(20, 16))

        # Provider
        ctk.CTkLabel(dlg, text="Proveedor", font=FONT_SMALL,
                     text_color=COLORS["text_sec"]).pack(
            anchor="w", padx=20, pady=(0, 4))

        provider_var = tk.StringVar(value=config.get("ai_provider", "OpenAI"))
        provider_menu = ctk.CTkOptionMenu(
            dlg, variable=provider_var, values=available or ["(ninguno)"],
            fg_color=COLORS["input_bg"], button_color=COLORS["border"],
            button_hover_color=COLORS["surface_hover"],
            dropdown_fg_color=COLORS["surface"],
            dropdown_hover_color=COLORS["accent_muted"],
            dropdown_text_color=COLORS["text"],
            text_color=COLORS["text"], font=FONT_BODY,
            dropdown_font=FONT_BODY, corner_radius=8)
        provider_menu.pack(fill="x", padx=20, pady=(0, 10))

        # API Key
        key_label = ctk.CTkLabel(dlg, text="API Key", font=FONT_SMALL,
                                 text_color=COLORS["text_sec"])
        key_label.pack(anchor="w", padx=20, pady=(0, 4))

        key_var = tk.StringVar()
        key_entry = ctk.CTkEntry(
            dlg, textvariable=key_var, show="*",
            fg_color=COLORS["input_bg"], border_color=COLORS["border"],
            text_color=COLORS["text"], font=FONT_BODY,
            corner_radius=8, height=34)
        key_entry.pack(fill="x", padx=20, pady=(0, 10))

        # Ollama fields
        ollama_frame = ctk.CTkFrame(dlg, fg_color="transparent")

        ctk.CTkLabel(ollama_frame, text="Modelo", font=FONT_SMALL,
                     text_color=COLORS["text_sec"]).pack(anchor="w")
        ollama_model_var = tk.StringVar(value=config.get("ollama_model", "llama3"))
        ctk.CTkEntry(
            ollama_frame, textvariable=ollama_model_var,
            fg_color=COLORS["input_bg"], border_color=COLORS["border"],
            text_color=COLORS["text"], font=FONT_BODY,
            corner_radius=8, height=34
        ).pack(fill="x", pady=(4, 8))

        ctk.CTkLabel(ollama_frame, text="URL", font=FONT_SMALL,
                     text_color=COLORS["text_sec"]).pack(anchor="w")
        ollama_url_var = tk.StringVar(
            value=config.get("ollama_url", "http://localhost:11434"))
        ctk.CTkEntry(
            ollama_frame, textvariable=ollama_url_var,
            fg_color=COLORS["input_bg"], border_color=COLORS["border"],
            text_color=COLORS["text"], font=FONT_BODY,
            corner_radius=8, height=34
        ).pack(fill="x")

        # Status
        status_var = tk.StringVar(value="")
        status_lbl = ctk.CTkLabel(dlg, textvariable=status_var,
                                  font=FONT_SMALL, text_color=COLORS["text_dim"])
        status_lbl.pack(anchor="w", padx=20, pady=(6, 0))

        def update_fields(*_):
            prov = provider_var.get()
            if prov == "Ollama":
                key_label.pack_forget()
                key_entry.pack_forget()
                ollama_frame.pack(fill="x", padx=20, pady=(0, 10),
                                  before=status_lbl)
            else:
                ollama_frame.pack_forget()
                key_label.pack(anchor="w", padx=20, pady=(0, 4),
                               before=status_lbl)
                key_entry.pack(fill="x", padx=20, before=status_lbl)
                key_var.set(get_api_key(prov) or "")

        provider_menu.configure(command=lambda _: update_fields())
        update_fields()

        # Buttons
        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(12, 20), side="bottom")

        def on_test():
            prov = provider_var.get()
            status_var.set("Probando...")
            dlg.update()
            on_save(close=False)
            if prov in self._ai_editors:
                ok = self._ai_editors[prov]().test_connection(on_log=self._log)
                status_var.set("Conexion OK" if ok else "Error. Ver log.")
                status_lbl.configure(
                    text_color=COLORS["success"] if ok else COLORS["danger"])
            else:
                status_var.set("No disponible.")

        def on_save(close=True):
            prov = provider_var.get()
            cfg = get_config()
            cfg["ai_provider"] = prov
            if prov == "Ollama":
                cfg["ollama_model"] = ollama_model_var.get()
                cfg["ollama_url"] = ollama_url_var.get()
            else:
                save_api_key(prov, key_var.get())
                cfg = get_config()
                cfg["ai_provider"] = prov
            save_config(cfg)
            self._init_ai_editor()
            self._log(f"AI config: {prov}")
            if close:
                dlg.destroy()

        ctk.CTkButton(
            btn_row, text="Probar", command=on_test,
            fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            text_color=COLORS["text_sec"], font=FONT_SMALL,
            corner_radius=8, width=80, height=34
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            btn_row, text="Guardar", command=on_save,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color="#0B0B0F", font=(_UI, 10, "bold"),
            corner_radius=8, width=100, height=34
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            btn_row, text="Cancelar", command=dlg.destroy,
            fg_color=COLORS["surface"], hover_color=COLORS["surface_hover"],
            text_color=COLORS["text_sec"], font=FONT_SMALL,
            corner_radius=8, width=80, height=34
        ).pack(side="left")

    def _init_ai_editor(self) -> None:
        config = get_config()
        prov = config.get("ai_provider", "OpenAI")
        self._ai_editor = self._ai_editors[prov]() if prov in self._ai_editors else None

    # ──────────────────────────────────────────────
    #  Recording Flow
    # ──────────────────────────────────────────────

    def _begin_capture(self) -> None:
        self._configure_engine_language()
        self._engine.start(
            sample_rate=AudioCapture.SAMPLE_RATE,
            on_text=lambda t: self.after(0, self._append_final_text, t),
            on_partial=lambda t: self.after(0, self._show_partial_text, t))

        self._session_raw_text = ""
        self._session_text_start = self._text_area._textbox.index(tk.END)

        # Update current session metadata
        if self._current_session is not None:
            self._current_session["engine"] = self._engine_var.get()
            self._current_session["language"] = self._language_var.get()

        # Sound feedback
        if self._sound_enabled.get():
            play_sound("start")

        import numpy as _np

        def _audio_with_levels(chunk):
            """Wrap engine feed to also extract RMS levels for overlay."""
            self._engine.feed_audio(chunk)
            try:
                n = len(self._ov_audio_levels)
                seg_len = max(1, len(chunk) // n)
                for i in range(n):
                    seg = chunk[i * seg_len:(i + 1) * seg_len]
                    if len(seg) > 0:
                        rms = float(_np.sqrt(_np.mean(seg ** 2)))
                    else:
                        rms = 0.0
                    # Smooth: blend with previous value
                    self._ov_audio_levels[i] = self._ov_audio_levels[i] * 0.3 + rms * 0.7
            except Exception:
                pass

        if self._audio_source_var.get() == "Audio PC":
            self._audio_capture = LoopbackCapture()
            self._log("Captura: audio del sistema.")
            self._audio_capture.start(callback=_audio_with_levels)
        else:
            self._audio_capture = AudioCapture()
            mic_idx = self._get_selected_mic_index()
            self._log(f"Captura: microfono (device={mic_idx})")
            self._audio_capture.start(
                callback=_audio_with_levels, device=mic_idx)

        self._recording = True
        self._combo_engine.configure(state="disabled")
        self._seg_audio.configure(state="disabled")
        self._combo_mic.configure(state="disabled")
        self._btn_refresh_mic.configure(state="disabled")
        self._combo_language.configure(state="disabled")
        self._combo_translate.configure(state="disabled")

        # Update record button to recording state (with pulse animation)
        self._btn_record.configure(
            text="\u25CF  Grabando...", state="disabled",
            fg_color=self._PULSE_BRIGHT, hover_color=self._PULSE_BRIGHT,
            text_color="white")
        self._start_btn_pulse()

        is_ptt = "Mantener" in self._activation_var.get()
        self._set_status(
            "Hablando... (suelte para procesar)" if is_ptt else "Grabando...",
            "red")
        self._blink_recording_dot()
        self._set_overlay_recording(True)

    def _stop_recording(self) -> None:
        self._recording = False
        if self._sound_enabled.get():
            play_sound("stop")
        self._stop_blink()
        self._ov_audio_levels = [0.0] * len(self._ov_audio_levels)
        self._set_overlay_recording(False)
        if self._audio_capture is not None:
            self._audio_capture.stop()
        if self._engine is not None:
            self._engine.stop()
        self._clear_partial()

        self._stop_btn_pulse()
        self._btn_record.configure(
            text="\u2713  Motor Listo", state="disabled",
            fg_color="#1A3D2A", hover_color="#1A3D2A",
            text_color=COLORS["success"])

        self._combo_engine.configure(state="normal")
        self._seg_audio.configure(state="normal")
        self._on_audio_source_change_seg(self._audio_source_var.get())
        self._update_controls_for_engine()

        raw = self._session_raw_text.strip()
        if self._ai_edit_mode.get() and raw:
            self._run_ai_editing(raw)
        else:
            self._set_status("Listo", "green")
            self._set_overlay_idle()

    # ──────────────────────────────────────────────
    #  AI Editing
    # ──────────────────────────────────────────────

    def _run_ai_editing(self, raw_text: str) -> None:
        if self._ai_editor is None:
            self._init_ai_editor()
        if self._ai_editor is None or not self._ai_editor.is_configured():
            self._log("[AI] No configurado. Use 'Config AI'.")
            self._set_status("AI no configurado.", "orange")
            return

        self._set_status("Puliendo con AI...", "orange")

        def do_edit():
            lang = LANGUAGE_MAP.get(self._language_var.get()) or "es"
            polished = self._ai_editor.polish(raw_text, lang, on_log=self._log)
            self.after(0, self._apply_polished_text, raw_text, polished)

        threading.Thread(target=do_edit, daemon=True).start()

    def _apply_polished_text(self, raw: str, polished: str) -> None:
        if polished and polished != raw:
            try:
                self._text_area._textbox.delete(self._session_text_start, tk.END)
                self._text_area._textbox.insert(
                    self._session_text_start, polished + " ")
                self._text_area.see("end")
            except Exception as e:
                self._log(f"[AI] Error: {e}")

            if self._direct_mode.get():
                threading.Thread(
                    target=self._type_in_active_app, args=(polished,),
                    daemon=True).start()

            # Update history entry with polished version
            if self._current_entry is not None:
                update_entry_polished(self._current_entry, polished)

            self._set_status("Texto pulido", "green")
            self._log(f"[AI] '{raw[:40]}...' -> '{polished[:40]}...'")
        else:
            self._set_status("Listo", "green")
        self._set_overlay_idle()

    # ──────────────────────────────────────────────
    #  Floating Overlay (Always-visible Pill)
    # ──────────────────────────────────────────────

    # ── Overlay Color Constants ──
    _OV_TRANSPARENT = "#010101"
    _OV_BG          = "#0A0A0F"      # Near-black, modern
    _OV_BORDER      = "#1A1A25"      # Very subtle border
    _OV_BORDER_REC  = "#331515"      # Subtle red hint
    _OV_WAVE_IDLE   = "#2A2A3A"      # Dim dots
    _OV_WAVE_REC    = "#D0D0E0"      # Light gray-white bars (recording)
    _OV_WAVE_LOAD   = "#606080"      # Dim base for loading
    _OV_WAVE_PULSE  = "#C0C0D8"      # Bright pulse traveling across
    _OV_BTN_BG      = "#181820"      # Button circle bg
    _OV_BTN_X       = "#707088"      # X icon color
    _OV_BTN_STOP    = "#FF3D3D"      # Stop square red
    _OV_TEXT         = "#808098"      # Hover text

    _OV_BAR_COUNT   = 20
    _OV_BAR_W       = 2
    _OV_BAR_GAP     = 3
    _OV_WAVE_H      = 22
    _OV_BTN_SIZE    = 22

    def _create_overlay(self) -> None:
        """Create the multi-state floating overlay."""
        if self._overlay is not None:
            return

        self._ov_state = "idle"  # idle | idle_expanded | recording | processing | no_engine
        self._ov_anim_id = None
        self._ov_hover_leave_id = None
        self._ov_user_positioned = False

        ov = ctk.CTkToplevel(self)
        ov.overrideredirect(True)
        ov.attributes("-topmost", True)
        ov.attributes("-alpha", 0.95)
        if _IS_WIN:
            ov.configure(fg_color=self._OV_TRANSPARENT)
            ov.attributes("-transparentcolor", self._OV_TRANSPARENT)
        else:
            # macOS: no transparentcolor support, match pill background
            ov.configure(fg_color=self._OV_BG)
        ov.lift()
        ov.focus_set = lambda: None

        # Main pill
        self._ov_pill = ctk.CTkFrame(
            ov, fg_color=self._OV_BG,
            border_color=self._OV_BORDER, border_width=1,
            corner_radius=16)
        self._ov_pill.pack(padx=4, pady=4)

        # --- Inner container (horizontal layout) ---
        self._ov_inner = ctk.CTkFrame(self._ov_pill, fg_color="transparent")
        self._ov_inner.pack(padx=8, pady=5)

        # --- X button (left, hidden by default) ---
        self._ov_btn_x = tk.Canvas(
            self._ov_inner, width=self._OV_BTN_SIZE, height=self._OV_BTN_SIZE,
            highlightthickness=0, bd=0, bg=self._OV_BG, cursor="hand2")
        self._ov_btn_x_items = self._draw_x_button(self._ov_btn_x)
        self._ov_btn_x.bind("<ButtonRelease-1>", lambda e: self._ov_on_cancel())

        # --- Waveform canvas (center, always present) ---
        wave_w = self._OV_BAR_COUNT * (self._OV_BAR_W + self._OV_BAR_GAP) - self._OV_BAR_GAP
        self._ov_wave = tk.Canvas(
            self._ov_inner, width=wave_w, height=self._OV_WAVE_H,
            highlightthickness=0, bd=0, bg=self._OV_BG)
        self._ov_wave.pack(side="left")

        # Create waveform bars (centered vertically)
        self._ov_wave_bars = []
        cy = self._OV_WAVE_H // 2  # vertical center
        for i in range(self._OV_BAR_COUNT):
            x0 = i * (self._OV_BAR_W + self._OV_BAR_GAP)
            bar = self._ov_wave.create_rectangle(
                x0, cy - 1, x0 + self._OV_BAR_W, cy + 1,
                fill=self._OV_WAVE_IDLE, outline="")
            self._ov_wave_bars.append(bar)

        # --- Stop button (right, hidden by default) ---
        self._ov_btn_stop = tk.Canvas(
            self._ov_inner, width=self._OV_BTN_SIZE, height=self._OV_BTN_SIZE,
            highlightthickness=0, bd=0, bg=self._OV_BG, cursor="hand2")
        self._ov_btn_stop_items = self._draw_stop_button(self._ov_btn_stop)
        self._ov_btn_stop.bind("<ButtonRelease-1>", lambda e: self._ov_on_stop())

        # --- Hover label (shown above waveform on hover) ---
        self._ov_hover_label = ctk.CTkLabel(
            self._ov_pill, text=self._hotkey_display,
            fg_color="transparent", text_color=self._OV_TEXT,
            font=(_MONO, 9))
        # Not packed by default — shown on hover

        # --- No-engine label ---
        self._ov_engine_label = ctk.CTkLabel(
            self._ov_inner, text="\u26A1 Cargar Motor",
            fg_color="transparent", text_color=self._OV_TEXT,
            font=(_UI, 9), cursor="hand2")
        self._ov_engine_label.bind("<ButtonRelease-1>",
                                    lambda e: self._ov_on_load_engine())

        self._overlay = ov
        self._overlay_recording = False

        # Set initial state based on engine status
        if self._model_loaded:
            self._ov_set_state("idle")
        else:
            self._ov_set_state("no_engine")

        # Hover bindings for idle state
        self._ov_pill.bind("<Enter>", self._ov_on_enter)
        self._ov_pill.bind("<Leave>", self._ov_on_leave)

        # Click on waveform/pill to start recording (in idle states)
        self._ov_wave.bind("<ButtonRelease-1>", self._ov_on_click)

        # Drag bindings on pill frame
        for w in (self._ov_pill, self._ov_inner, self._ov_wave, self._ov_hover_label):
            w.bind("<ButtonPress-1>", self._ov_drag_start)
            w.bind("<B1-Motion>", self._ov_drag_move)

        self._reposition_overlay()

    def _draw_x_button(self, canvas) -> list:
        """Draw X icon on a canvas."""
        s = self._OV_BTN_SIZE
        items = []
        items.append(canvas.create_oval(1, 1, s-1, s-1,
                     fill=self._OV_BTN_BG, outline=""))
        m = 6
        items.append(canvas.create_line(m, m, s-m, s-m,
                     fill=self._OV_BTN_X, width=1.5))
        items.append(canvas.create_line(s-m, m, m, s-m,
                     fill=self._OV_BTN_X, width=1.5))
        return items

    def _draw_stop_button(self, canvas) -> list:
        """Draw stop square icon on a canvas."""
        s = self._OV_BTN_SIZE
        items = []
        items.append(canvas.create_oval(1, 1, s-1, s-1,
                     fill=self._OV_BTN_BG, outline=""))
        m = 7
        items.append(canvas.create_rectangle(m, m, s-m, s-m,
                     fill=self._OV_BTN_STOP, outline=""))
        return items

    # ── Overlay State Machine ──

    def _ov_set_state(self, state: str) -> None:
        """Transition overlay to a new state."""
        if self._overlay is None:
            return
        old = self._ov_state
        self._ov_state = state

        # Cancel any running animation
        if self._ov_anim_id is not None:
            try:
                self._overlay.after_cancel(self._ov_anim_id)
            except Exception:
                pass
            self._ov_anim_id = None

        # Hide everything first
        self._ov_btn_x.pack_forget()
        self._ov_btn_stop.pack_forget()
        self._ov_hover_label.pack_forget()
        self._ov_engine_label.pack_forget()
        self._ov_wave.pack_forget()
        self._ov_inner.pack_forget()

        # Repack inner
        self._ov_inner.pack(padx=8, pady=5)

        if state == "no_engine":
            self._ov_pill.configure(border_color=self._OV_BORDER)
            self._ov_engine_label.pack(side="left")
            self._ov_wave_set_idle_dots()

        elif state == "idle":
            self._ov_pill.configure(border_color=self._OV_BORDER)
            self._ov_wave.pack(side="left")
            self._ov_wave_set_idle_dots()
            self._ov_wave.configure(bg=self._OV_BG)

        elif state == "idle_expanded":
            self._ov_pill.configure(border_color=self._OV_BORDER)
            self._ov_hover_label.pack(before=self._ov_inner, pady=(4, 0))
            self._ov_wave.pack(side="left")
            self._ov_wave_set_idle_dots()
            self._ov_wave.configure(bg=self._OV_BG)

        elif state == "recording":
            self._ov_pill.configure(border_color=self._OV_BORDER_REC)
            self._ov_btn_x.pack(side="left", padx=(0, 6))
            self._ov_wave.pack(side="left")
            self._ov_btn_stop.pack(side="left", padx=(6, 0))
            self._ov_wave.configure(bg=self._OV_BG)
            self._ov_animate_wave()

        elif state == "processing":
            self._ov_pill.configure(border_color=self._OV_BORDER)
            self._ov_wave.pack(side="left")
            self._ov_wave.configure(bg=self._OV_BG)
            self._ov_animate_loading()

        # Update canvas bg colors for buttons
        self._ov_btn_x.configure(bg=self._OV_BG)
        self._ov_btn_stop.configure(bg=self._OV_BG)

        # Reposition after geometry change (unless user positioned)
        self._overlay.update_idletasks()
        self._reposition_overlay()

    def _ov_wave_set_idle_dots(self) -> None:
        """Set waveform bars to small centered dots."""
        cy = self._OV_WAVE_H // 2
        for i, bar in enumerate(self._ov_wave_bars):
            x0 = i * (self._OV_BAR_W + self._OV_BAR_GAP)
            self._ov_wave.coords(bar, x0, cy - 1, x0 + self._OV_BAR_W, cy + 1)
            self._ov_wave.itemconfig(bar, fill=self._OV_WAVE_IDLE)

    def _ov_animate_wave(self) -> None:
        """Animate waveform bars using real audio levels."""
        if self._ov_state != "recording" or self._overlay is None:
            return
        try:
            cy = self._OV_WAVE_H // 2
            max_half = (self._OV_WAVE_H - 4) // 2
            for i, bar in enumerate(self._ov_wave_bars):
                # RMS for speech is typically 0.005-0.1
                level = min(self._ov_audio_levels[i] * 40.0, 1.0)
                h = max(1, int(level * max_half))
                x0 = i * (self._OV_BAR_W + self._OV_BAR_GAP)
                self._ov_wave.coords(bar, x0, cy - h,
                                     x0 + self._OV_BAR_W, cy + h)
                self._ov_wave.itemconfig(bar, fill=self._OV_WAVE_REC)
            self._ov_anim_id = self._overlay.after(60, self._ov_animate_wave)
        except Exception:
            pass

    def _ov_animate_loading(self) -> None:
        """Smooth traveling pulse across bars during processing."""
        if self._ov_state != "processing" or self._overlay is None:
            return
        try:
            import math
            cy = self._OV_WAVE_H // 2
            t = time.time()
            n = self._OV_BAR_COUNT
            # Pulse position sweeps left-to-right continuously
            pulse_pos = (t * 8) % (n + 4)  # speed=8 bars/sec, with overshoot
            for i, bar in enumerate(self._ov_wave_bars):
                # Distance from pulse center
                dist = abs(i - pulse_pos)
                if dist < 3:
                    # Near pulse: bar grows and brightens
                    strength = 1.0 - dist / 3.0
                    h = int(2 + 6 * strength)
                    color = self._OV_WAVE_PULSE
                else:
                    h = 1
                    color = self._OV_WAVE_LOAD
                x0 = i * (self._OV_BAR_W + self._OV_BAR_GAP)
                self._ov_wave.coords(bar, x0, cy - h,
                                     x0 + self._OV_BAR_W, cy + h)
                self._ov_wave.itemconfig(bar, fill=color)
            self._ov_anim_id = self._overlay.after(33, self._ov_animate_loading)
        except Exception:
            pass

    # ── Overlay Hover ──

    def _ov_on_enter(self, event=None) -> None:
        if self._ov_hover_leave_id is not None:
            self._overlay.after_cancel(self._ov_hover_leave_id)
            self._ov_hover_leave_id = None
        if self._ov_state == "idle":
            self._ov_set_state("idle_expanded")

    def _ov_on_leave(self, event=None) -> None:
        if self._ov_state == "idle_expanded":
            self._ov_hover_leave_id = self._overlay.after(
                250, lambda: self._ov_set_state("idle")
                if self._ov_state == "idle_expanded" else None)

    # ── Overlay Click Actions ──

    def _ov_on_click(self, event=None) -> None:
        """Click on waveform area — start recording if idle."""
        if self._ov_state in ("idle", "idle_expanded"):
            if self._model_loaded and not self._loading:
                self.after(0, self._start_recording)

    def _ov_on_cancel(self) -> None:
        """X button — cancel recording or processing."""
        if self._ov_state == "recording":
            self._recording = False
            self._stop_blink()
            if self._audio_capture is not None:
                self._audio_capture.stop()
            if self._engine is not None:
                self._engine.stop()
            self._clear_partial()
            self._stop_btn_pulse()
            self._btn_record.configure(
                text="\u2713  Motor Listo", state="disabled",
                fg_color="#1A3D2A", hover_color="#1A3D2A",
                text_color=COLORS["success"])
            self._combo_engine.configure(state="normal")
            self._seg_audio.configure(state="normal")
            self._on_audio_source_change_seg(self._audio_source_var.get())
            self._update_controls_for_engine()
            self._set_status("Cancelado", "orange")
            self._ov_set_state("idle")
        elif self._ov_state == "processing":
            self._ov_set_state("idle")
            self._set_status("Cancelado", "orange")

    def _ov_on_stop(self) -> None:
        """Stop button — finish recording and process."""
        if self._ov_state == "recording" and self._recording:
            self.after(0, self._stop_recording)

    def _ov_on_load_engine(self) -> None:
        """Click on no-engine label — trigger engine load."""
        if not self._loading:
            self.after(0, self._on_load_engine)

    # ── Overlay Integration (called from recording logic) ──

    def _set_overlay_recording(self, recording: bool) -> None:
        """Switch overlay to recording or processing state."""
        if self._overlay is None:
            return
        self._overlay_recording = recording
        try:
            if recording:
                self._ov_set_state("recording")
            else:
                self._ov_set_state("processing")
        except Exception:
            pass

    def _set_overlay_idle(self) -> None:
        """Return overlay to idle state (after text processed)."""
        if self._overlay is None:
            return
        try:
            if self._model_loaded:
                self._ov_set_state("idle")
            else:
                self._ov_set_state("no_engine")
        except Exception:
            pass

    def _set_overlay_engine_loaded(self) -> None:
        """Transition overlay from no_engine to idle."""
        if self._overlay is None:
            return
        try:
            self._ov_set_state("idle")
        except Exception:
            pass

    # ── Overlay Drag ──

    def _ov_drag_start(self, event) -> None:
        """Record offset when user starts dragging the overlay."""
        self._ov_drag_x = event.x_root - self._overlay.winfo_x()
        self._ov_drag_y = event.y_root - self._overlay.winfo_y()

    def _ov_drag_move(self, event) -> None:
        """Move overlay to follow the mouse."""
        nx = event.x_root - self._ov_drag_x
        ny = event.y_root - self._ov_drag_y
        self._overlay.geometry(f"+{nx}+{ny}")
        self._ov_user_positioned = True

    def _reposition_overlay(self) -> None:
        """Center overlay at bottom of screen work area (above taskbar)."""
        if self._overlay is None or self._ov_user_positioned:
            return
        self._overlay.update_idletasks()
        w = self._overlay.winfo_reqwidth()
        h = self._overlay.winfo_reqheight()
        try:
            import ctypes
            class _RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                             ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            rect = _RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                        ctypes.byref(rect), 0)
            sx = rect.right - rect.left
            sy_bottom = rect.bottom
            x = rect.left + (sx - w) // 2
            y = sy_bottom - h - 55
        except Exception:
            sx = self._overlay.winfo_screenwidth()
            sy = self._overlay.winfo_screenheight()
            x = (sx - w) // 2
            y = sy - h - 60
        self._overlay.geometry(f"+{x}+{y}")

    def _blink_overlay(self) -> None:
        """Legacy — no longer used, kept for compatibility."""
        pass

    # ──────────────────────────────────────────────
    #  Text Output
    # ──────────────────────────────────────────────

    def _type_in_active_app(self, text: str) -> None:
        for ch in text:
            self._kb.type(ch)

    def _append_final_text(self, text: str) -> None:
        self._clear_partial()
        if self._has_placeholder:
            self._clear_placeholder()

        # Translation
        translate_to = LANGUAGE_MAP.get(self._translate_var.get())
        if translate_to and translate_to != "en":
            try:
                if self._translator is None:
                    from translation.translator import ArgosTranslator
                    self._translator = ArgosTranslator()
                src = getattr(self._engine, "language", "es") or "es"
                if getattr(self._engine, "task", "") == "translate":
                    src = "en"
                if src != translate_to:
                    self._translator.ensure_package(
                        src, translate_to, on_log=self._log)
                    text = self._translator.translate(
                        text, src, translate_to) + " "
            except Exception as e:
                self._log(f"Traduccion: {e}")

        self._text_area._textbox.insert(tk.END, text)
        self._text_area.see("end")
        self._session_raw_text += text

        # Track in session history
        if not self._viewing_history and self._current_session is not None:
            self._current_entry = add_entry(self._current_session, text)
            self._update_current_session_card()

        if self._direct_mode.get() and not self._ai_edit_mode.get():
            threading.Thread(
                target=self._type_in_active_app, args=(text,),
                daemon=True).start()

    def _show_partial_text(self, text: str) -> None:
        self._clear_partial()
        if self._has_placeholder:
            self._clear_placeholder()
        self._partial_tag_start = self._text_area._textbox.index(tk.END)
        self._text_area._textbox.insert(tk.END, text, "partial")
        self._text_area.see("end")

    def _clear_partial(self) -> None:
        ranges = self._text_area._textbox.tag_ranges("partial")
        if ranges:
            for i in range(0, len(ranges), 2):
                self._text_area._textbox.delete(ranges[i], ranges[i + 1])
        self._partial_tag_start = None

    def _on_copy(self) -> None:
        if self._has_placeholder:
            return
        text = self._text_area.get("0.0", "end").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._set_status("Copiado", "green")

    def _on_clear(self) -> None:
        self._text_area._textbox.delete("1.0", tk.END)
        self._set_placeholder()
        self._set_status("Limpiado", "green")

    # ──────────────────────────────────────────────
    #  Cleanup
    # ──────────────────────────────────────────────

    def destroy(self) -> None:
        # Save current session to history if it has entries
        try:
            if self._current_session and self._current_session.get("entries"):
                finalize_session(self._current_session)
                save_session_to_history(self._current_session)
        except Exception:
            pass

        self._cancel_event.set()
        self._stop_blink()
        if self._ov_anim_id is not None:
            try:
                self.after_cancel(self._ov_anim_id)
            except Exception:
                pass
        if self._overlay is not None:
            try:
                self._overlay.destroy()
            except Exception:
                pass
            self._overlay = None
        if self._recording:
            if self._audio_capture is not None:
                self._audio_capture.stop()
            if self._engine is not None:
                self._engine.stop()
        try:
            if self._hotkey_hook is not None:
                self._hotkey_hook.stop()
        except Exception:
            pass
        super().destroy()


class CancelledError(Exception):
    pass

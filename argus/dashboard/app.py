"""Visible, local, on-demand Argus Control Center."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Event, Thread
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Any

from argus.config import ConfigError, DashboardConfig, load_config
from argus.dashboard.session import (
    DashboardError,
    DashboardSession,
    DashboardSnapshot,
    VoiceTurn,
    create_dashboard_session,
)
from argus.tools import ToolDefinition


BG = "#050507"
PANEL = "#0d090b"
PANEL_ALT = "#151013"
INPUT_BG = "#090709"
RED = "#ff3048"
RED_BRIGHT = "#ff6678"
RED_DIM = "#8f1d2c"
RED_DARK = "#3b0b13"
TEXT = "#f4e9eb"
MUTED = "#ad8f94"
WARNING = "#ff9a3c"


@dataclass(frozen=True, slots=True)
class _Result:
    name: str
    value: object | None = None
    error: str | None = None


@dataclass(slots=True)
class _ConfirmationRequest:
    definition: ToolDefinition
    arguments: dict[str, Any]
    completed: Event = field(default_factory=Event)
    approved: bool = False


class _ArgusCore(tk.Canvas):
    """Small code-drawn animated core; no image assets or network access."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(
            parent,
            width=184,
            height=184,
            background=PANEL,
            highlightthickness=0,
            borderwidth=0,
        )
        self._phase = 0
        self._active = True
        self._draw()

    def _draw(self) -> None:
        if not self._active:
            return
        self.delete("all")
        center = 92
        pulse = 3 * math.sin(math.radians(self._phase * 3))
        self.create_line(8, center, 176, center, fill=RED_DARK, width=1)
        self.create_line(center, 8, center, 176, fill=RED_DARK, width=1)
        for radius, color, width in (
            (78, RED_DARK, 1),
            (64, RED_DIM, 1),
            (48 + pulse, RED, 2),
            (25, RED_BRIGHT, 2),
        ):
            self.create_oval(
                center - radius,
                center - radius,
                center + radius,
                center + radius,
                outline=color,
                width=width,
            )
        self.create_arc(
            20,
            20,
            164,
            164,
            start=self._phase,
            extent=78,
            outline=RED_BRIGHT,
            width=3,
            style="arc",
        )
        self.create_arc(
            36,
            36,
            148,
            148,
            start=210 - self._phase * 1.4,
            extent=105,
            outline=RED_DIM,
            width=3,
            style="arc",
        )
        for angle in range(0, 360, 45):
            radians = math.radians(angle + self._phase / 2)
            x = center + 71 * math.cos(radians)
            y = center + 71 * math.sin(radians)
            self.create_oval(x - 2, y - 2, x + 2, y + 2, fill=RED, outline="")
        self.create_text(
            center,
            center - 6,
            text="A",
            fill=RED_BRIGHT,
            font=("Consolas", 27, "bold"),
        )
        self.create_text(
            center,
            center + 19,
            text="CORE",
            fill=MUTED,
            font=("Consolas", 8),
        )
        self._phase = (self._phase + 4) % 360
        self.after(80, self._draw)

    def stop(self) -> None:
        self._active = False


class _VoiceVisualizer(tk.Canvas):
    """Animated voice-state display drawn entirely with Tk."""

    def __init__(self, parent: tk.Misc, *, height: int = 150) -> None:
        super().__init__(
            parent,
            height=height,
            background=INPUT_BG,
            highlightthickness=1,
            highlightbackground=RED_DARK,
            borderwidth=0,
        )
        self._phase = 0
        self._state = "STANDBY"
        self._active = True
        self.after(80, self._draw)

    def set_state(self, state: str) -> None:
        self._state = state.upper()

    def _draw(self) -> None:
        if not self._active:
            return
        self.delete("all")
        width = max(self.winfo_width(), 300)
        height = max(self.winfo_height(), 120)
        center_x = width / 2
        center_y = height / 2 - 6
        moving = self._state in {"LISTENING", "PROCESSING", "SPEAKING", "LOCKED"}
        for index in range(29):
            offset = index - 14
            amplitude = 8
            if moving:
                amplitude += 22 * abs(
                    math.sin(math.radians(self._phase * 4 + index * 23))
                )
            bar_height = amplitude * (1 - min(abs(offset) / 19, 0.72))
            x = center_x + offset * 10
            self.create_line(
                x,
                center_y - bar_height,
                x,
                center_y + bar_height,
                fill=RED if moving else RED_DIM,
                width=3 if abs(offset) < 8 else 2,
            )
        self.create_text(
            center_x,
            height - 18,
            text=self._state,
            fill=RED_BRIGHT if moving else MUTED,
            font=("Consolas", 10, "bold"),
        )
        self._phase = (self._phase + 4) % 360
        self.after(80, self._draw)

    def stop(self) -> None:
        self._active = False


class DashboardWindow:
    """Render local state while all blocking work runs off the Tk thread."""

    def __init__(
        self,
        root: tk.Tk,
        session: DashboardSession,
        config: DashboardConfig,
        *,
        assistant_name: str,
    ) -> None:
        self._root = root
        self._session = session
        self._config = config
        self._assistant_name = assistant_name
        self._closed = False
        self._locked = False
        self._voice_mode = False
        self._resume_voice_after_wake = False
        self._idle_timer: str | None = None
        self._busy: set[str] = set()
        self._tasks: Queue[tuple[str, Callable[[], object]] | None] = Queue()
        self._results: Queue[_Result] = Queue()
        self._confirmations: Queue[_ConfirmationRequest] = Queue()
        self._active_confirmation: _ConfirmationRequest | None = None
        self._confirmation_dialog: tk.Toplevel | None = None
        self._shown_warnings: set[str] = set()

        self._configure_window()
        self._build_header()
        self._build_tabs()
        self._build_footer()

        self._worker = Thread(
            target=self._worker_loop,
            name="argus-dashboard-local-worker",
            daemon=True,
        )
        self._worker.start()
        self._root.protocol("WM_DELETE_WINDOW", self.close)
        self._root.after(100, self._drain_results)
        self.refresh()
        self._update_clock()
        self._root.bind_all("<Any-KeyPress>", self._record_activity, add="+")
        self._root.bind_all("<Any-Button>", self._record_activity, add="+")
        self._schedule_idle_lock()

    def _configure_window(self) -> None:
        self._root.title(f"{self._assistant_name} Control Center")
        self._root.geometry(
            f"{self._config.window_width}x{self._config.window_height}"
        )
        self._root.minsize(800, 600)
        self._root.configure(background=BG)
        self._root.option_add("*Font", ("Segoe UI", 10))
        style = ttk.Style(self._root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, bordercolor=RED_DARK)
        style.configure("Hud.TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL, relief="flat")
        style.configure(
            "Title.TLabel",
            background=BG,
            foreground=RED_BRIGHT,
            font=("Consolas", 23, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=BG,
            foreground=MUTED,
            font=("Consolas", 9),
        )
        style.configure(
            "Panel.TLabel", background=PANEL, foreground=TEXT
        )
        style.configure(
            "PanelMuted.TLabel",
            background=PANEL,
            foreground=MUTED,
            font=("Consolas", 8),
        )
        style.configure(
            "Section.TLabel",
            background=PANEL,
            foreground=RED_BRIGHT,
            font=("Consolas", 10, "bold"),
        )
        style.configure(
            "Safe.TLabel",
            background=PANEL,
            foreground=RED_BRIGHT,
            font=("Consolas", 9, "bold"),
        )
        style.configure(
            "Link.TLabel",
            background=BG,
            foreground=RED_BRIGHT,
            font=("Consolas", 10, "bold"),
        )
        style.configure(
            "Hud.TButton",
            background=RED_DARK,
            foreground=TEXT,
            bordercolor=RED_DIM,
            lightcolor=RED_DARK,
            darkcolor=RED_DARK,
            padding=(16, 9),
            font=("Consolas", 9, "bold"),
        )
        style.map(
            "Hud.TButton",
            background=[("active", RED_DIM), ("pressed", RED)],
            foreground=[("disabled", MUTED), ("active", "#ffffff")],
            bordercolor=[("focus", RED_BRIGHT), ("active", RED)],
        )
        style.configure(
            "Ghost.TButton",
            background=PANEL_ALT,
            foreground=MUTED,
            bordercolor=RED_DARK,
            padding=(14, 9),
            font=("Consolas", 9),
        )
        style.map(
            "Ghost.TButton",
            background=[("active", RED_DARK)],
            foreground=[("active", TEXT)],
        )
        style.configure(
            "Hud.TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0)
        )
        style.configure(
            "Hud.TNotebook.Tab",
            background=PANEL_ALT,
            foreground=MUTED,
            borderwidth=0,
            padding=(18, 10),
            font=("Consolas", 9, "bold"),
        )
        style.map(
            "Hud.TNotebook.Tab",
            background=[("selected", RED_DARK), ("active", "#211217")],
            foreground=[("selected", RED_BRIGHT), ("active", TEXT)],
        )
        style.configure(
            "Treeview",
            background=INPUT_BG,
            fieldbackground=INPUT_BG,
            foreground=TEXT,
            bordercolor=RED_DARK,
            rowheight=29,
            font=("Segoe UI", 9),
        )
        style.map(
            "Treeview",
            background=[("selected", RED_DARK)],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Treeview.Heading",
            background=PANEL_ALT,
            foreground=RED_BRIGHT,
            bordercolor=RED_DARK,
            font=("Consolas", 9, "bold"),
            padding=(7, 8),
        )
        style.map("Treeview.Heading", background=[("active", RED_DARK)])
        style.configure(
            "Vertical.TScrollbar",
            background=RED_DARK,
            troughcolor=INPUT_BG,
            bordercolor=INPUT_BG,
            arrowcolor=RED_BRIGHT,
        )
        self._window_icon = tk.PhotoImage(width=32, height=32)
        self._window_icon.put(BG, to=(0, 0, 32, 32))
        self._window_icon.put(RED_DARK, to=(4, 4, 28, 28))
        self._window_icon.put(RED, to=(8, 8, 24, 24))
        self._window_icon.put(BG, to=(12, 12, 20, 20))
        self._root.iconphoto(True, self._window_icon)

    def _build_header(self) -> None:
        header = ttk.Frame(
            self._root, padding=(24, 18, 24, 12), style="Hud.TFrame"
        )
        header.pack(fill="x")
        title_area = ttk.Frame(header, style="Hud.TFrame")
        title_area.pack(side="left", fill="x", expand=True)
        ttk.Label(
            title_area,
            text=f"{self._assistant_name.upper()} // CONTROL CENTER",
            style="Title.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            title_area,
            text="PERSONAL INTELLIGENCE SYSTEM  //  LOCAL ON-DEMAND CORE",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        safety = ttk.Frame(header, style="Hud.TFrame")
        safety.pack(side="right")
        self._clock = tk.StringVar(value="--:--:--")
        ttk.Label(safety, textvariable=self._clock, style="Subtitle.TLabel").pack(
            anchor="e"
        )
        self._connection_label = ttk.Label(
            safety, text="●  INITIALIZING LOCAL CORE", style="Link.TLabel"
        )
        self._connection_label.pack(anchor="e")
        ttk.Label(
            safety,
            text="NO REMOTE ENDPOINT  //  NO BACKGROUND AUTOMATION",
            style="Subtitle.TLabel",
        ).pack(anchor="e", pady=(3, 0))
        accent = tk.Canvas(
            self._root,
            height=2,
            background=BG,
            highlightthickness=0,
            borderwidth=0,
        )
        accent.pack(fill="x", padx=24)
        accent.create_rectangle(0, 0, 5000, 2, fill=RED_DIM, outline="")

    def _build_tabs(self) -> None:
        body = ttk.Frame(self._root, padding=(24, 14, 24, 8), style="Hud.TFrame")
        body.pack(fill="both", expand=True)
        self._build_core_rail(body)
        self._notebook = ttk.Notebook(body, style="Hud.TNotebook")
        self._notebook.pack(side="right", fill="both", expand=True, padx=(14, 0))
        self._build_chat_tab()
        self._build_notifications_tab()
        self._build_devices_tab()
        self._build_status_tab()

    def _build_core_rail(self, parent: ttk.Frame) -> None:
        rail = ttk.Frame(parent, width=220, padding=14, style="Panel.TFrame")
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        ttk.Label(rail, text="SYSTEM CORE", style="Section.TLabel").pack(anchor="w")
        self._core = _ArgusCore(rail)
        self._core.pack(pady=(8, 10))

        self._rail_values: dict[str, tk.StringVar] = {}
        for label, key in (
            ("CORE LINK", "uplink"),
            ("PROFILE", "profile"),
            ("ACCESS", "role"),
            ("MODEL", "model"),
        ):
            ttk.Label(rail, text=label, style="PanelMuted.TLabel").pack(
                anchor="w", pady=(8, 0)
            )
            variable = tk.StringVar(value="SCANNING…")
            self._rail_values[key] = variable
            ttk.Label(
                rail,
                textvariable=variable,
                style="Panel.TLabel",
                wraplength=184,
            ).pack(anchor="w", pady=(1, 0))
        ttk.Separator(rail, orient="horizontal").pack(fill="x", pady=16)
        ttk.Label(
            rail,
            text="LOCAL TOOLS: CONFIRMATION-GATED\nEXECUTION: USER-INITIATED ONLY",
            style="Safe.TLabel",
            justify="left",
        ).pack(anchor="w")

    def _build_chat_tab(self) -> None:
        frame = ttk.Frame(self._notebook, padding=16, style="Panel.TFrame")
        self._notebook.add(frame, text="01  CONSOLE")
        ttk.Label(
            frame, text="LOCAL COMMAND + CONVERSATION CHANNEL", style="Section.TLabel"
        ).pack(anchor="w", pady=(0, 10))

        self._console_live = ttk.Frame(frame, style="Panel.TFrame")
        self._console_live.pack(fill="both", expand=True)
        mode_bar = ttk.Frame(self._console_live, style="Panel.TFrame")
        mode_bar.pack(fill="x", pady=(0, 9))
        self._mode_status = tk.StringVar(value="TEXT MODE  //  LOCAL TOOLS ARMED")
        ttk.Label(
            mode_bar, textvariable=self._mode_status, style="PanelMuted.TLabel"
        ).pack(side="left")
        self._voice_mode_button = ttk.Button(
            mode_bar,
            text="VOICE MODE",
            command=lambda: self.set_voice_mode(True),
            style="Ghost.TButton",
        )
        self._voice_mode_button.pack(side="right")
        self._text_mode_button = ttk.Button(
            mode_bar,
            text="TEXT MODE",
            command=lambda: self.set_voice_mode(False),
            style="Hud.TButton",
        )
        self._text_mode_button.pack(side="right", padx=(0, 8))
        self._text_mode_button.state(["disabled"])
        if not self._session.voice_available:
            self._voice_mode_button.state(["disabled"])

        self._chat = scrolledtext.ScrolledText(
            self._console_live,
            wrap="word",
            state="disabled",
            padx=12,
            pady=12,
            font=("Segoe UI", 10),
            background=INPUT_BG,
            foreground=TEXT,
            insertbackground=RED_BRIGHT,
            selectbackground=RED_DARK,
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=RED_DARK,
            highlightcolor=RED,
        )
        self._chat.pack(fill="both", expand=True)
        self._chat.tag_configure(
            "user", foreground=RED_BRIGHT, spacing1=12, font=("Segoe UI Semibold", 10)
        )
        self._chat.tag_configure("argus", foreground=TEXT, spacing1=12)
        self._chat.tag_configure("system", foreground=WARNING, spacing1=12)

        self._composer = ttk.Frame(
            self._console_live, padding=(0, 12, 0, 0), style="Panel.TFrame"
        )
        self._composer.pack(fill="x")
        self._message = tk.Text(
            self._composer,
            height=3,
            wrap="word",
            padx=10,
            pady=9,
            background=INPUT_BG,
            foreground=TEXT,
            insertbackground=RED_BRIGHT,
            selectbackground=RED_DARK,
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=RED_DARK,
            highlightcolor=RED,
        )
        self._message.pack(side="left", fill="x", expand=True)
        self._message.bind("<Control-Return>", self._send_shortcut)
        self._send_button = ttk.Button(
            self._composer,
            text="TRANSMIT",
            command=self.send_message,
            width=12,
            style="Hud.TButton",
        )
        self._send_button.pack(side="right", padx=(10, 0), fill="y")
        self._console_hint = ttk.Label(
            self._console_live,
            text="CTRL+ENTER TO TRANSMIT  //  APPROVAL RULES ENFORCED",
            style="PanelMuted.TLabel",
        )
        self._console_hint.pack(anchor="w", pady=(6, 0))

        self._voice_panel = ttk.Frame(
            self._console_live, padding=(0, 12, 0, 0), style="Panel.TFrame"
        )
        self._voice_visualizer = _VoiceVisualizer(self._voice_panel, height=155)
        self._voice_visualizer.pack(fill="both", expand=True)
        telemetry = ttk.Frame(
            self._voice_panel, padding=(0, 10, 0, 0), style="Panel.TFrame"
        )
        telemetry.pack(fill="x")
        for column in range(4):
            telemetry.columnconfigure(column, weight=1)
        for column, (label, value) in enumerate(
            (
                ("MIC CHANNEL", "AUTOMATIC"),
                ("COMMAND PATH", "LOCAL"),
                ("AUDIO POLICY", "DISCARDED"),
                ("AUTONOMY", "DISABLED"),
            )
        ):
            card = tk.Frame(
                telemetry,
                background=RED_DARK,
                padx=1,
                pady=1,
            )
            card.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 4, 0),
            )
            inside = tk.Frame(card, background=PANEL_ALT, padx=10, pady=8)
            inside.pack(fill="both", expand=True)
            tk.Label(
                inside,
                text=label,
                background=PANEL_ALT,
                foreground=MUTED,
                font=("Consolas", 7),
            ).pack(anchor="w")
            tk.Label(
                inside,
                text=value,
                background=PANEL_ALT,
                foreground=RED_BRIGHT,
                font=("Consolas", 9, "bold"),
            ).pack(anchor="w", pady=(2, 0))

        voice_status = ttk.Frame(
            self._voice_panel, padding=(0, 12, 0, 0), style="Panel.TFrame"
        )
        voice_status.pack(fill="x")
        self._voice_phase = tk.StringVar(
            value="VOICE CHANNEL STANDBY"
        )
        ttk.Label(
            voice_status,
            textvariable=self._voice_phase,
            style="Safe.TLabel",
        ).pack(anchor="w")
        self._voice_transcript = tk.StringVar(
            value="Enter Voice Mode to begin automatic command capture."
        )
        ttk.Label(
            voice_status,
            textvariable=self._voice_transcript,
            style="Panel.TLabel",
            wraplength=700,
        ).pack(anchor="w", pady=(5, 0))
        ttk.Label(
            voice_status,
            text=(
                "VISIBLE HANDS-FREE LOOP  //  LISTEN → TRANSCRIBE → ACT → SPEAK → LISTEN"
            ),
            style="PanelMuted.TLabel",
        ).pack(anchor="w", pady=(7, 0))

        self._sleep_panel = ttk.Frame(frame, padding=36, style="Panel.TFrame")
        self._sleep_visualizer = _VoiceVisualizer(self._sleep_panel, height=230)
        self._sleep_visualizer.set_state("LOCKED")
        self._sleep_visualizer.pack(fill="both", expand=True)
        ttk.Label(
            self._sleep_panel,
            text="ARGUS IDLE LOCK ENGAGED",
            style="Title.TLabel",
        ).pack(pady=(20, 8))
        ttk.Label(
            self._sleep_panel,
            text=(
                "All command processing is disconnected. To return, say:\n\n"
                f'“{self._config.wake_phrase.title()}”'
            ),
            style="Panel.TLabel",
            justify="center",
        ).pack()
        self._sleep_detail = tk.StringVar(
            value="LOCAL MICROPHONE LISTENING ONLY FOR THE RETURN PHRASE"
        )
        ttk.Label(
            self._sleep_panel,
            textvariable=self._sleep_detail,
            style="Safe.TLabel",
        ).pack(pady=(18, 0))

    def _build_notifications_tab(self) -> None:
        frame = ttk.Frame(self._notebook, padding=16, style="Panel.TFrame")
        self._notebook.add(frame, text="02  ALERTS")
        ttk.Label(
            frame, text="DELIVERED NOTIFICATION ARCHIVE", style="Section.TLabel"
        ).pack(anchor="w", pady=(0, 10))
        table = ttk.Frame(frame, style="Panel.TFrame")
        table.pack(fill="both", expand=True)
        columns = ("priority", "category", "content", "delivered")
        self._notifications = ttk.Treeview(
            table, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "priority": ("Priority", 90),
            "category": ("Category", 110),
            "content": ("Delivered notification", 520),
            "delivered": ("Delivered at", 180),
        }
        for name, (label, width) in headings.items():
            self._notifications.heading(name, text=label)
            self._notifications.column(name, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(
            table, orient="vertical", command=self._notifications.yview
        )
        self._notifications.configure(yscrollcommand=scrollbar.set)
        self._notifications.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _build_devices_tab(self) -> None:
        frame = ttk.Frame(self._notebook, padding=16, style="Panel.TFrame")
        self._notebook.add(frame, text="03  DEVICES")
        ttk.Label(
            frame, text="CONNECTED HARDWARE MANIFEST", style="Section.TLabel"
        ).pack(anchor="w", pady=(0, 10))
        columns = ("id", "name", "transport", "local", "policy")
        self._devices = ttk.Treeview(
            frame, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "id": ("Device ID", 150),
            "name": ("Name", 260),
            "transport": ("Transport", 120),
            "local": ("Local actuators", 120),
            "policy": ("Execution policy", 140),
        }
        for name, (label, width) in headings.items():
            self._devices.heading(name, text=label)
            self._devices.column(name, width=width, anchor="w")
        self._devices.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="DEVICE COMMANDS RUN LOCALLY  //  ACTUATION REQUIRES FRESH APPROVAL",
            style="Safe.TLabel",
        ).pack(anchor="w", pady=(8, 0))

    def _build_status_tab(self) -> None:
        frame = ttk.Frame(self._notebook, padding=24, style="Panel.TFrame")
        self._notebook.add(frame, text="04  SYSTEM")
        ttk.Label(
            frame, text="LOCAL CORE TELEMETRY", style="Section.TLabel"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        self._status_values: dict[str, tk.StringVar] = {}
        labels = (
            ("Client", "client"),
            ("Profile", "profile"),
            ("Role", "role"),
            ("Provider", "provider"),
            ("Model", "model"),
            ("Core uptime", "uptime"),
            ("Background automation", "proactive"),
            ("Local actions", "actions"),
            ("Available tools", "tools"),
            ("Voice input", "voice"),
        )
        for row, (label, key) in enumerate(labels, start=1):
            ttk.Label(
                frame, text=label.upper(), style="PanelMuted.TLabel"
            ).grid(
                row=row, column=0, sticky="w", padx=(0, 30), pady=7
            )
            variable = tk.StringVar(value="—")
            self._status_values[key] = variable
            ttk.Label(
                frame, textvariable=variable, style="Panel.TLabel"
            ).grid(
                row=row, column=1, sticky="w", pady=7
            )
        self._status_values["actions"].set("Initializing")

    def _build_footer(self) -> None:
        footer = ttk.Frame(
            self._root, padding=(24, 6, 24, 16), style="Hud.TFrame"
        )
        footer.pack(fill="x")
        self._activity = tk.StringVar(value="INITIALIZING SYSTEM CORE…")
        ttk.Label(
            footer, textvariable=self._activity, style="Subtitle.TLabel"
        ).pack(side="left")
        self._refresh_button = ttk.Button(
            footer,
            text="REFRESH LOCAL STATUS",
            command=self.refresh,
            style="Hud.TButton",
        )
        self._refresh_button.pack(side="right")
        ttk.Button(
            footer,
            text="EXIT ARGUS",
            command=self.close,
            style="Ghost.TButton",
        ).pack(side="right", padx=(0, 10))

    def request_confirmation(
        self, definition: ToolDefinition, arguments: Mapping[str, Any]
    ) -> bool:
        """Block the local worker until the visible UI grants one action."""

        if self._closed or self._locked:
            return False
        request = _ConfirmationRequest(definition, dict(arguments))
        self._confirmations.put(request)
        while not request.completed.wait(0.1):
            if self._closed or self._locked:
                return False
        return request.approved

    def _drain_confirmations(self) -> None:
        if self._active_confirmation is not None or self._closed:
            return
        try:
            request = self._confirmations.get_nowait()
        except Empty:
            return
        if self._locked:
            request.completed.set()
            return
        self._active_confirmation = request
        dialog = tk.Toplevel(self._root)
        self._confirmation_dialog = dialog
        dialog.title("Argus action confirmation")
        dialog.configure(background=BG)
        dialog.resizable(False, False)
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._finish_confirmation(False))

        panel = ttk.Frame(dialog, padding=24, style="Panel.TFrame")
        panel.pack(fill="both", expand=True, padx=2, pady=2)
        ttk.Label(
            panel,
            text="CONFIRM ONE LOCAL ACTION",
            style="Section.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            panel,
            text=request.definition.name.upper(),
            style="Title.TLabel",
        ).pack(anchor="w", pady=(8, 12))
        ttk.Label(
            panel,
            text="EXACT ARGUMENTS",
            style="PanelMuted.TLabel",
        ).pack(anchor="w")
        details = json.dumps(request.arguments, ensure_ascii=False, indent=2)
        detail_box = tk.Text(
            panel,
            width=66,
            height=min(12, max(4, details.count("\n") + 2)),
            padx=10,
            pady=10,
            background=INPUT_BG,
            foreground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=RED_DARK,
        )
        detail_box.insert("1.0", details)
        detail_box.configure(state="disabled")
        detail_box.pack(fill="x", pady=(4, 12))
        ttk.Label(
            panel,
            text=(
                "Approval applies only to this exact action. Denying it performs "
                "nothing and does not authorize a substitute."
            ),
            style="Safe.TLabel",
            wraplength=540,
        ).pack(anchor="w")
        buttons = ttk.Frame(panel, style="Panel.TFrame")
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(
            buttons,
            text="DENY",
            command=lambda: self._finish_confirmation(False),
            style="Ghost.TButton",
        ).pack(side="right")
        ttk.Button(
            buttons,
            text="ALLOW ONCE",
            command=lambda: self._finish_confirmation(True),
            style="Hud.TButton",
        ).pack(side="right", padx=(0, 10))
        dialog.update_idletasks()
        x = self._root.winfo_rootx() + max(
            0, (self._root.winfo_width() - dialog.winfo_width()) // 2
        )
        y = self._root.winfo_rooty() + max(
            0, (self._root.winfo_height() - dialog.winfo_height()) // 2
        )
        dialog.geometry(f"+{x}+{y}")

    def _finish_confirmation(self, approved: bool) -> None:
        request = self._active_confirmation
        if request is None:
            return
        request.approved = approved
        request.completed.set()
        self._active_confirmation = None
        if self._confirmation_dialog is not None:
            try:
                self._confirmation_dialog.grab_release()
                self._confirmation_dialog.destroy()
            except tk.TclError:
                pass
        self._confirmation_dialog = None
        self._record_activity()

    def _deny_pending_confirmations(self) -> None:
        if self._active_confirmation is not None:
            self._active_confirmation.completed.set()
            self._active_confirmation = None
        while True:
            try:
                self._confirmations.get_nowait().completed.set()
            except Empty:
                break

    def _submit(self, name: str, operation: Callable[[], object]) -> None:
        if self._closed or name in self._busy:
            return
        self._busy.add(name)
        self._tasks.put((name, operation))

    def _worker_loop(self) -> None:
        while True:
            task = self._tasks.get()
            if task is None:
                return
            name, operation = task
            try:
                self._results.put(_Result(name=name, value=operation()))
            except Exception as error:
                detail = " ".join(str(error).split())
                self._results.put(
                    _Result(
                        name=name,
                        error=detail[:1_000] or type(error).__name__,
                    )
                )

    def _drain_results(self) -> None:
        if self._closed:
            return
        self._drain_confirmations()
        while True:
            try:
                result = self._results.get_nowait()
            except Empty:
                break
            self._busy.discard(result.name)
            if result.name == "chat":
                self._send_button.state(["!disabled"])
            elif result.name == "voice":
                self._voice_visualizer.set_state("STANDBY")
            elif result.name == "refresh":
                self._refresh_button.state(["!disabled"])
            if result.error is not None:
                if result.name == "voice" and not self._voice_mode:
                    self._activity.set("TEXT MODE RESTORED")
                    continue
                self._activity.set(f"OPERATION FAILED  //  {result.error}")
                if result.name == "chat":
                    self._append_chat("SYSTEM", result.error, "system")
                if result.name == "voice":
                    if self._voice_mode:
                        self._voice_phase.set("VOICE CHANNEL RECALIBRATING")
                        self._voice_transcript.set(
                            f"No command captured: {result.error}"
                        )
                        self._root.after(900, self._start_voice_cycle)
                if result.name == "wake" and self._locked:
                    self._sleep_detail.set(f"WAKE LISTENER ERROR  //  {result.error}")
                    self._root.after(5_000, self._start_wake_listener)
                continue
            if result.name == "refresh" and isinstance(
                result.value, DashboardSnapshot
            ):
                self._apply_snapshot(result.value)
            elif result.name == "chat" and isinstance(result.value, str):
                self._append_chat(self._assistant_name, result.value, "argus")
                self._activity.set("RESPONSE RECEIVED")
                self._record_activity()
            elif result.name == "voice" and isinstance(result.value, VoiceTurn):
                turn = result.value
                self._append_chat("You (voice)", turn.transcript, "user")
                self._append_chat(self._assistant_name, turn.reply, "argus")
                self._voice_transcript.set(f'LAST COMMAND  //  “{turn.transcript}”')
                if turn.speech_warning:
                    self._append_chat("SYSTEM", turn.speech_warning, "system")
                self._activity.set("VOICE COMMAND COMPLETE")
                self._voice_phase.set("COMMAND COMPLETE  //  RESUMING LISTENING")
                self._record_activity()
                if self._voice_mode:
                    self._root.after(350, self._start_voice_cycle)
            elif result.name == "wake" and self._locked:
                self._unlock_from_wake()
        self._root.after(100, self._drain_results)

    def _apply_snapshot(self, snapshot: DashboardSnapshot) -> None:
        self._connection_label.configure(
            text="●  LOCAL CORE ONLINE", foreground=RED_BRIGHT
        )
        self._activity.set("LOCAL STATUS READY")
        self._status_values["client"].set(snapshot.client_id)
        self._status_values["profile"].set(snapshot.profile_id)
        self._status_values["role"].set(snapshot.role)
        self._status_values["provider"].set(snapshot.provider)
        self._status_values["model"].set(snapshot.model)
        hours, remainder = divmod(snapshot.uptime_seconds, 3_600)
        minutes, seconds = divmod(remainder, 60)
        self._status_values["uptime"].set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        self._status_values["proactive"].set(
            "Enabled" if snapshot.proactive_enabled else "Disabled — on demand only"
        )
        self._status_values["actions"].set(
            "Enabled — local only" if snapshot.local_actions_enabled else "Disabled"
        )
        self._status_values["tools"].set(str(snapshot.tool_count))
        self._status_values["voice"].set(
            "Ready" if snapshot.voice_enabled else "Disabled"
        )
        self._rail_values["uplink"].set("LOCAL ONLY")
        self._rail_values["profile"].set(snapshot.profile_id.upper())
        self._rail_values["role"].set(snapshot.role.upper())
        self._rail_values["model"].set(snapshot.model)
        for warning in snapshot.warnings:
            if warning not in self._shown_warnings:
                self._shown_warnings.add(warning)
                self._append_chat("SYSTEM", warning, "system")

        device_rows = self._devices.get_children()
        if device_rows:
            self._devices.delete(*device_rows)
        for device in snapshot.devices:
            self._devices.insert(
                "",
                "end",
                values=(
                    device.device_id,
                    device.name,
                    device.transport,
                    "Enabled" if device.local_actuators else "Disabled",
                    "Local / on demand",
                ),
            )
        notification_rows = self._notifications.get_children()
        if notification_rows:
            self._notifications.delete(*notification_rows)
        for notification in snapshot.notifications:
            self._notifications.insert(
                "",
                "end",
                values=(
                    notification.priority,
                    notification.category,
                    notification.content,
                    notification.delivered_at,
                ),
            )

    def _append_chat(self, speaker: str, text: str, tag: str) -> None:
        self._chat.configure(state="normal")
        self._chat.insert("end", f"{speaker}: ", tag)
        self._chat.insert("end", f"{text}\n", tag)
        self._chat.configure(state="disabled")
        self._chat.see("end")

    def refresh(self) -> None:
        if self._locked:
            return
        self._activity.set("READING LOCAL STATUS…")
        self._refresh_button.state(["disabled"])
        self._submit("refresh", self._session.refresh)

    def _update_clock(self) -> None:
        if self._closed:
            return
        self._clock.set(datetime.now().astimezone().strftime("%Y-%m-%d  %H:%M:%S"))
        self._root.after(1_000, self._update_clock)

    def _send_shortcut(self, event: tk.Event[Any]) -> str:
        self.send_message()
        return "break"

    def set_voice_mode(self, enabled: bool, *, record_activity: bool = True) -> None:
        if self._closed or self._locked or enabled == self._voice_mode:
            return
        if enabled and not self._session.voice_available:
            self._append_chat(
                "SYSTEM", "Voice mode is disabled or unavailable.", "system"
            )
            return
        self._voice_mode = enabled
        if enabled:
            self._session.begin_voice_mode()
            self._chat.pack_forget()
            self._composer.pack_forget()
            self._console_hint.pack_forget()
            self._voice_panel.pack(fill="both", expand=True)
            self._text_mode_button.state(["!disabled"])
            self._voice_mode_button.state(["disabled"])
            self._mode_status.set(
                "VOICE MODE  //  AUTOMATIC LISTENING IS VISIBLY ACTIVE"
            )
            self._voice_phase.set("INITIALIZING AUTOMATIC VOICE CHANNEL")
            self._voice_transcript.set("Speak naturally. Pause when the command is complete.")
            self._voice_visualizer.set_state("LISTENING")
            self._root.after(150, self._start_voice_cycle)
        else:
            self._session.end_voice_mode()
            if record_activity:
                self._resume_voice_after_wake = False
            self._voice_panel.pack_forget()
            self._chat.pack(fill="both", expand=True)
            self._composer.pack(fill="x")
            self._console_hint.pack(anchor="w", pady=(6, 0))
            self._text_mode_button.state(["disabled"])
            self._voice_mode_button.state(["!disabled"])
            self._mode_status.set("TEXT MODE  //  LOCAL TOOLS ARMED")
            self._message.focus_set()
        if record_activity:
            self._record_activity()

    def _start_voice_cycle(self) -> None:
        if self._closed or self._locked or not self._voice_mode:
            return
        if "voice" in self._busy or "chat" in self._busy:
            return
        self._voice_visualizer.set_state("LISTENING")
        self._voice_phase.set("VOICE CHANNEL ACTIVE  //  LISTENING")
        self._voice_transcript.set("LISTENING  //  SPEAK NOW, THEN PAUSE")
        self._activity.set("AUTOMATIC VOICE CHANNEL ACTIVE…")
        self._submit("voice", self._session.voice_turn)

    def _record_activity(self, event: tk.Event[Any] | None = None) -> None:
        del event
        if self._closed or self._locked:
            return
        self._schedule_idle_lock()

    def _schedule_idle_lock(self, delay_ms: int | None = None) -> None:
        if self._closed or self._locked:
            return
        if self._idle_timer is not None:
            try:
                self._root.after_cancel(self._idle_timer)
            except tk.TclError:
                pass
        delay = delay_ms or self._config.idle_timeout_seconds * 1_000
        self._idle_timer = self._root.after(delay, self._enter_idle_lock)

    def _enter_idle_lock(self) -> None:
        self._idle_timer = None
        if self._closed or self._locked:
            return
        if self._voice_mode:
            self._resume_voice_after_wake = True
            self.set_voice_mode(False, record_activity=False)
        if "voice" in self._busy:
            self._root.after(250, self._enter_idle_lock)
            return
        if self._busy or self._active_confirmation is not None:
            self._schedule_idle_lock(10_000)
            return
        self._locked = True
        self._console_live.pack_forget()
        self._sleep_panel.pack(fill="both", expand=True)
        self._connection_label.configure(
            text="●  IDLE LOCK — COMMAND CORE DISCONNECTED", foreground=WARNING
        )
        self._rail_values["uplink"].set("SLEEP LOCK")
        self._activity.set("WAITING FOR LOCAL RETURN PHRASE…")
        self._refresh_button.state(["disabled"])
        self._sleep_detail.set(
            "LOCAL MICROPHONE LISTENING ONLY FOR THE RETURN PHRASE"
        )
        self._start_wake_listener()

    def _start_wake_listener(self) -> None:
        if self._closed or not self._locked or "wake" in self._busy:
            return
        if not self._session.wake_available:
            self._sleep_detail.set(
                "WAKE LISTENER UNAVAILABLE  //  EXIT AND CHECK VOICE CONFIGURATION"
            )
            return
        self._sleep_visualizer.set_state("LOCKED")
        self._submit("wake", self._session.wait_for_return_phrase)

    def _unlock_from_wake(self) -> None:
        resume_voice = self._resume_voice_after_wake
        self._resume_voice_after_wake = False
        self._locked = False
        self._sleep_panel.pack_forget()
        self._console_live.pack(fill="both", expand=True)
        self._connection_label.configure(
            text="●  LOCAL CORE ONLINE", foreground=RED_BRIGHT
        )
        self._rail_values["uplink"].set("LOCAL ONLY")
        self._activity.set("WELCOME BACK  //  COMMAND CORE RESTORED")
        self._refresh_button.state(["!disabled"])
        self._append_chat(
            "SYSTEM", "Return phrase accepted. Local command core restored.", "system"
        )
        self._schedule_idle_lock()
        if resume_voice:
            self._root.after(150, lambda: self.set_voice_mode(True))

    def send_message(self) -> None:
        if getattr(self, "_locked", False):
            return
        message = self._message.get("1.0", "end-1c").strip()
        if not message:
            return
        if message.casefold() in {"/exit", "/quit"}:
            self.close()
            return
        if message.casefold() in {"disconnect", "/disconnect", "/sleep"}:
            self._message.delete("1.0", "end")
            self._enter_idle_lock()
            return
        if len(message) > 4_000:
            self._activity.set("INPUT REJECTED  //  4000 CHARACTER LIMIT")
            return
        self._message.delete("1.0", "end")
        self._append_chat("You", message, "user")
        self._activity.set("ARGUS IS PROCESSING…")
        self._send_button.state(["disabled"])
        self._submit("chat", lambda: self._session.send_message(message))
        self._record_activity()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._idle_timer is not None:
            try:
                self._root.after_cancel(self._idle_timer)
            except tk.TclError:
                pass
        self._core.stop()
        self._voice_visualizer.stop()
        self._sleep_visualizer.stop()
        self._deny_pending_confirmations()
        self._tasks.put(None)
        self._session.close()
        self._root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the local Argus Control Center."
    )
    parser.add_argument("--config", type=Path, help="Alternate Argus JSON configuration.")
    parser.add_argument(
        "--check", action="store_true", help="Verify the local runtime without a GUI."
    )
    arguments = parser.parse_args(argv)

    session: DashboardSession | None = None
    try:
        config = load_config(arguments.config)
        if not config.dashboard.enabled:
            raise DashboardError("Dashboard mode is disabled in the configuration.")

        window_reference: dict[str, DashboardWindow] = {}

        def confirmer(
            definition: ToolDefinition, arguments: Mapping[str, Any]
        ) -> bool:
            window = window_reference.get("window")
            if window is None:
                return False
            return window.request_confirmation(definition, arguments)

        session = create_dashboard_session(config, confirmer=confirmer)
        if arguments.check:
            snapshot = session.refresh()
            print(
                "Argus local Control Center is ready: "
                f"{snapshot.tool_count} tools; proactive automation disabled; "
                "no remote endpoint."
            )
            session.close()
            return 0

        root = tk.Tk()
        window = DashboardWindow(
            root,
            session,
            config.dashboard,
            assistant_name=config.assistant.name,
        )
        window_reference["window"] = window
        root.mainloop()
    except (ConfigError, DashboardError, tk.TclError) as error:
        if session is not None:
            session.close()
        print(f"Argus Control Center could not start: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

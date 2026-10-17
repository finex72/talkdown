"""Spotify PTT Ducker

Senkt die Lautstärke von Spotify, solange die Push-to-Talk-Taste (Joystick-Knopf oder
Tastaturtaste) gedrückt ist oder ein überwachtes Programm (z.B. vPilot) Ton ausgibt,
und blendet sie danach wieder sanft ein.

Beim ersten Start wird die PTT-Taste automatisch abgefragt. Einstellungen werden unter
%APPDATA%\\SpotifyPTTDucker\\config.json gespeichert.

Kein offizielles Produkt von Spotify oder vPilot.
"""
import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk
import winreg
from tkinter import ttk

os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import comtypes
import keyboard
import pygame
from pycaw.api.endpointvolume import IAudioMeterInformation
from pycaw.pycaw import AudioUtilities

APP_NAME = "Spotify PTT Ducker"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SpotifyPTTDucker")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

DEFAULTS = {
    "binding": None,            # {"type": "joystick", "name": str, "button": int} | {"type": "keyboard", "key": str}
    "duck_level": 15.0,         # Prozent der normalen Lautstärke während Ducking
    "fade_out": 0.15,           # Sekunden bis abgesenkt
    "fade_in": 1.5,             # Sekunden für das Wiederaufblenden
    "watch_enabled": True,      # Ton anderer Programme überwachen
    "watch_processes": "vpilot.exe",  # kommagetrennt
    "watch_threshold": 0.01,    # Pegel (0..1), ab dem "Ton wird ausgegeben" gilt
    "watch_hold": 0.8,          # Nachlaufzeit in Sekunden
    "duck_process": "spotify.exe",
    "windows_autostart": False,
    "auto_activate": True,      # beim Programmstart sofort aktivieren
}


# ---------------------------------------------------------------- Konfiguration
def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def set_windows_autostart(enabled):
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}" --minimized'
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        cmd = f'"{pyw}" "{os.path.abspath(__file__)}" --minimized'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        pass


def describe_binding(b):
    if not b:
        return "nicht festgelegt"
    if b["type"] == "joystick":
        return f"Joystick „{b['name']}“, Knopf {b['button']}"
    return f"Taste „{b['key']}“"


# ---------------------------------------------------------------- Knopf-Erkennung
def capture_binding(cancel):
    """Wartet auf den ersten Joystick-Knopf oder die erste Taste. Gibt Binding oder None zurück."""
    pygame.init()
    pygame.joystick.init()
    sticks = {}
    for i in range(pygame.joystick.get_count()):
        j = pygame.joystick.Joystick(i)
        sticks[j.get_instance_id()] = j
    result = []

    def on_key(e):
        if e.event_type == "down" and not result and e.name:
            result.append({"type": "keyboard", "key": e.name.lower()})

    hook = keyboard.hook(on_key)
    try:
        while not cancel.is_set() and not result:
            for e in pygame.event.get():
                if e.type == pygame.JOYBUTTONDOWN and e.instance_id in sticks and not result:
                    result.append({"type": "joystick",
                                   "name": sticks[e.instance_id].get_name(),
                                   "button": e.button})
            time.sleep(0.01)
    finally:
        keyboard.unhook(hook)
        pygame.quit()
    return result[0] if result else None


# ---------------------------------------------------------------- Audio-Engine
class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.stop_event = threading.Event()
        self.threads = []
        self.ptt = False
        self.watch = False
        self.info = ""
        self.running = False

    # -- Lebenszyklus
    def start(self):
        if self.running or not self.cfg["binding"]:
            return
        self.stop_event.clear()
        self.threads = [threading.Thread(target=self._worker, daemon=True),
                        threading.Thread(target=self._input, daemon=True),
                        threading.Thread(target=self._watcher, daemon=True)]
        for t in self.threads:
            t.start()
        self.running = True

    def stop(self):
        if not self.running:
            return
        self.stop_event.set()
        for t in self.threads:
            t.join(timeout=3)
        self.threads = []
        self.ptt = self.watch = False
        self.running = False
        self.info = ""

    # -- Hilfen
    def _sessions(self, names):
        return [s for s in AudioUtilities.GetAllSessions()
                if s.Process and s.Process.name().lower() in names]

    def _spotify_volumes(self):
        return [s.SimpleAudioVolume for s in self._sessions({self.cfg["duck_process"].lower()})]

    # -- Lautstärke-Regelung
    def _worker(self):
        comtypes.CoInitialize()
        factor, base, last = 1.0, {}, time.perf_counter()
        try:
            while not self.stop_event.wait(0.02):
                c = self.cfg
                now = time.perf_counter()
                dt, last = now - last, now
                level = min(1.0, max(0.0, c["duck_level"] / 100.0))
                target = level if (self.ptt or self.watch) else 1.0
                if factor == target:
                    continue
                try:
                    vols = self._spotify_volumes()
                    if factor >= 1.0:  # Beginn des Duckings: aktuelle Lautstärke merken
                        base = {i: v.GetMasterVolume() for i, v in enumerate(vols)}
                    span = max(0.01, 1.0 - level)
                    if target < factor:
                        factor = max(target, factor - span * dt / max(0.01, c["fade_out"]))
                    else:
                        factor = min(target, factor + span * dt / max(0.01, c["fade_in"]))
                    for i, v in enumerate(vols):
                        v.SetMasterVolume(min(1.0, base.get(i, 1.0) * factor), None)
                except Exception:  # Spotify evtl. geschlossen/neu gestartet
                    factor = target
        finally:
            if factor < 1.0:  # beim Beenden Lautstärke wiederherstellen
                try:
                    for i, v in enumerate(self._spotify_volumes()):
                        v.SetMasterVolume(min(1.0, base.get(i, 1.0)), None)
                except Exception:
                    pass
            comtypes.CoUninitialize()

    # -- Überwachung anderer Programme (z.B. vPilot)
    def _watcher(self):
        comtypes.CoInitialize()
        meters, refreshed, last_loud = [], 0.0, 0.0
        try:
            while not self.stop_event.wait(0.05):
                c = self.cfg
                if not c["watch_enabled"]:
                    self.watch = False
                    continue
                now = time.perf_counter()
                try:
                    if now - refreshed > 2.0:  # Sessions gelegentlich neu einlesen
                        names = {n.strip().lower() for n in c["watch_processes"].split(",") if n.strip()}
                        meters = [s._ctl.QueryInterface(IAudioMeterInformation) for s in self._sessions(names)]
                        refreshed = now
                    if any(m.GetPeakValue() > c["watch_threshold"] for m in meters):
                        last_loud = now
                except Exception:
                    refreshed, meters = 0.0, []
                self.watch = now - last_loud < c["watch_hold"]
        finally:
            self.watch = False
            comtypes.CoUninitialize()

    # -- PTT-Eingabe
    def _input(self):
        b = self.cfg["binding"]
        try:
            if b["type"] == "joystick":
                self._input_joystick(b)
            else:
                self._input_keyboard(b["key"])
        finally:
            self.ptt = False

    def _input_keyboard(self, key):
        def cb(e):
            if (e.name or "").lower() == key:
                self.ptt = e.event_type == "down"
        hook = keyboard.hook(cb)
        self.info = f"Taste „{key}“ aktiv"
        self.stop_event.wait()
        keyboard.unhook(hook)

    def _input_joystick(self, b):
        pygame.init()
        joy = None
        try:
            while not self.stop_event.is_set():
                if joy is None:
                    pygame.joystick.quit()
                    pygame.joystick.init()
                    for i in range(pygame.joystick.get_count()):
                        j = pygame.joystick.Joystick(i)
                        if j.get_name().lower() == b["name"].lower():
                            joy = j
                            break
                    if joy is None:
                        self.info = f"Suche Joystick „{b['name']}“ …"
                        self.stop_event.wait(2.0)
                        continue
                    self.info = f"Joystick verbunden: {b['name']}"
                try:
                    pygame.event.pump()
                    self.ptt = bool(joy.get_button(b["button"]))
                except Exception:  # Joystick abgezogen
                    joy, self.ptt = None, False
                    continue
                self.stop_event.wait(0.01)
        finally:
            pygame.quit()


# ---------------------------------------------------------------- Oberfläche
class App:
    def __init__(self, root, cfg, minimized):
        self.root, self.cfg = root, cfg
        self.engine = Engine(cfg)
        self.save_job = None
        root.title(APP_NAME)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.quit)

        main = ttk.Frame(root, padding=14)
        main.grid()

        # Status + Start/Stop
        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")
        self.status = ttk.Label(top, text="", font=("Segoe UI", 11, "bold"))
        self.status.grid(row=0, column=0, sticky="w")
        self.toggle_btn = ttk.Button(top, text="Starten", width=12, command=self.toggle)
        self.toggle_btn.grid(row=0, column=1, padx=(20, 0))
        top.columnconfigure(0, weight=1)
        self.detail = ttk.Label(main, text="", foreground="#666")
        self.detail.grid(row=1, column=0, sticky="w", pady=(2, 10))

        # PTT-Taste
        box = ttk.LabelFrame(main, text="Push-to-Talk-Taste", padding=10)
        box.grid(row=2, column=0, sticky="ew")
        self.bind_label = ttk.Label(box, text="")
        self.bind_label.grid(row=0, column=0, sticky="w")
        ttk.Button(box, text="Neu belegen …", command=self.rebind).grid(row=0, column=1, padx=(20, 0))
        box.columnconfigure(0, weight=1)

        # Einstellungen
        s = ttk.LabelFrame(main, text="Einstellungen", padding=10)
        s.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        s.columnconfigure(1, weight=1)
        self.slider(s, 0, "Lautstärke beim Sprechen", "duck_level", 0, 100, "{:.0f} %")
        self.slider(s, 1, "Absenken dauert", "fade_out", 0.05, 2.0, "{:.2f} s")
        self.slider(s, 2, "Fade-In dauert", "fade_in", 0.2, 5.0, "{:.1f} s")

        self.check(s, 3, "Ton anderer Programme überwachen (z. B. vPilot)", "watch_enabled")
        ttk.Label(s, text="Programme:").grid(row=4, column=0, sticky="w", padx=(18, 0))
        self.proc_var = tk.StringVar(value=cfg["watch_processes"])
        self.proc_var.trace_add("write", lambda *_: self.set("watch_processes", self.proc_var.get()))
        ttk.Entry(s, textvariable=self.proc_var, width=28).grid(row=4, column=1, columnspan=2, sticky="ew", pady=2)
        self.slider(s, 5, "Empfindlichkeit (Schwelle)", "watch_threshold", 0.001, 0.2, "{:.3f}", pad=18)
        self.slider(s, 6, "Nachlaufzeit", "watch_hold", 0.0, 3.0, "{:.1f} s", pad=18)

        ttk.Separator(s).grid(row=7, column=0, columnspan=3, sticky="ew", pady=8)
        self.check(s, 8, "Beim Programmstart sofort aktivieren", "auto_activate")
        self.check(s, 9, "Mit Windows starten", "windows_autostart",
                   extra=lambda: set_windows_autostart(self.cfg["windows_autostart"]))

        self.refresh_binding()
        self.tick()

        if minimized:
            root.iconify()
        if not cfg["binding"]:
            root.after(300, lambda: self.rebind(then_start=True))  # Ersteinrichtung
        elif cfg["auto_activate"]:
            self.start()

    # -- Bausteine
    def slider(self, parent, row, text, key, lo, hi, fmt, pad=0):
        ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w", padx=(pad, 10))
        val = ttk.Label(parent, text=fmt.format(self.cfg[key]), width=8, anchor="e")
        val.grid(row=row, column=2)

        def changed(v):
            v = float(v)
            val.config(text=fmt.format(v))
            self.set(key, round(v, 3))

        ttk.Scale(parent, from_=lo, to=hi, value=self.cfg[key], command=changed, length=170) \
            .grid(row=row, column=1, sticky="ew", padx=6)

    def check(self, parent, row, text, key, extra=None):
        var = tk.BooleanVar(value=self.cfg[key])

        def changed():
            self.set(key, var.get())
            if extra:
                extra()

        ttk.Checkbutton(parent, text=text, variable=var, command=changed) \
            .grid(row=row, column=0, columnspan=3, sticky="w", pady=1)

    def set(self, key, value):
        self.cfg[key] = value
        if self.save_job:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(400, lambda: save_config(self.cfg))

    def refresh_binding(self):
        self.bind_label.config(text=describe_binding(self.cfg["binding"]))

    # -- Steuerung
    def start(self):
        if not self.cfg["binding"]:
            self.rebind(then_start=True)
            return
        self.engine.start()

    def toggle(self):
        if self.engine.running:
            self.engine.stop()
        else:
            self.start()

    def rebind(self, then_start=False):
        was_running = self.engine.running
        self.engine.stop()
        dlg = tk.Toplevel(self.root)
        dlg.title("Push-to-Talk festlegen")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        f = ttk.Frame(dlg, padding=20)
        f.grid()
        ttk.Label(f, text="Drücke jetzt den Joystick-Knopf (oder die Taste),\n"
                          "die du als Push-to-Talk nutzen möchtest …",
                  justify="center", font=("Segoe UI", 11)).grid(pady=(0, 14))
        cancel = threading.Event()
        result = []
        threading.Thread(target=lambda: result.append(capture_binding(cancel)), daemon=True).start()

        def finish(b):
            dlg.destroy()
            if b:
                self.cfg["binding"] = b
                save_config(self.cfg)
                self.refresh_binding()
            if (was_running or then_start) and self.cfg["binding"]:
                self.engine.start()

        def abort():
            cancel.set()

        def poll():
            if result:
                finish(result[0])
            else:
                self.root.after(50, poll)

        ttk.Button(f, text="Abbrechen", command=abort).grid()
        dlg.protocol("WM_DELETE_WINDOW", abort)
        dlg.grab_set()
        poll()

    def tick(self):
        e = self.engine
        if e.running:
            ducking = e.ptt or e.watch
            self.status.config(text="● Spotify wird gesenkt" if ducking else "● Aktiv",
                               foreground="#c47a00" if ducking else "#1a8f3c")
            self.toggle_btn.config(text="Stoppen")
            self.detail.config(text=e.info)
        else:
            self.status.config(text="○ Gestoppt", foreground="#888")
            self.toggle_btn.config(text="Starten")
            self.detail.config(text="")
        self.root.after(100, self.tick)

    def quit(self):
        self.engine.stop()
        save_config(self.cfg)
        self.root.destroy()


# ---------------------------------------------------------------- Start
def selftest(outfile):
    """Prüft für den gebauten .exe-Test, ob alle Teile laden (kein Fenster)."""
    lines = []
    try:
        comtypes.CoInitialize()
        eng = Engine(dict(DEFAULTS))
        lines.append(f"spotify sessions: {len(eng._spotify_volumes())}")
        lines.append(f"vpilot meters: {[s._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue() for s in eng._sessions({'vpilot.exe'})]}")
        pygame.init()
        pygame.joystick.init()
        lines.append(f"joysticks: {[pygame.joystick.Joystick(i).get_name() for i in range(pygame.joystick.get_count())]}")
        pygame.quit()
        lines.append("OK")
    except Exception as e:  # noqa: BLE001
        lines.append(f"FEHLER: {e!r}")
    with open(outfile, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    if "--selftest" in sys.argv:
        selftest(sys.argv[sys.argv.index("--selftest") + 1])
        return
    # Nur eine Instanz erlaubt (sonst würde doppelt gesenkt)
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "SpotifyPTTDucker_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(0, "Spotify PTT Ducker läuft bereits.", APP_NAME, 0x40)
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root, load_config(), minimized="--minimized" in sys.argv)
    root.mainloop()
    del mutex


if __name__ == "__main__":
    main()

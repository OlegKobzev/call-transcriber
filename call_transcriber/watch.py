"""Start and stop recording automatically when a call is running.

Signals, checked every few seconds:
- a call app (Zoom) has its meeting window open, or is playing audio;
- a browser window shows a meeting tab (title matches watch_title_pattern, by default the Zoom web
  client) while the browser is playing audio.
A recording starts on a strong signal (meeting window/tab + audio) and continues while any weak signal remains
(meeting tab or browser audio), so briefly switching tabs does not stop it.
"""
import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import re
import shutil
import subprocess
import sys
import time

import psutil

from . import cli
from . import config as config_module

POLL_SEC = 5
STARTUP_DIR = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
STARTUP_SCRIPT = os.path.join(STARTUP_DIR, "CallTranscriberWatch.vbs")
# Zoom desktop: the in-meeting window classes (classic and Zoom Workplace)
ZOOM_MEETING_CLASSES = {"ZPContentViewWndClass", "ConfMultiTabContentWndClass"}


def windows_by_exe(names):
    """{exe: [(class, title, visible), ...]} for top-level windows of the given executables."""
    names = {n.lower() for n in names}
    pid_exe = {p.pid: p.info["name"].lower() for p in psutil.process_iter(["name"])
               if (p.info["name"] or "").lower() in names}
    found = {}
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = pid_exe.get(pid.value)
        if exe:
            cls = ctypes.create_unicode_buffer(256)
            title = ctypes.create_unicode_buffer(512)
            user32.GetClassNameW(hwnd, cls, 256)
            user32.GetWindowTextW(hwnd, title, 512)
            found.setdefault(exe, []).append((cls.value, title.value, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(callback, 0)
    return found


def apps_playing_audio():
    """Lower-case executable names with an active audio session."""
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities
        comtypes.CoInitialize()
        return {s.Process.name().lower() for s in AudioUtilities.GetAllSessions() if s.Process and s.State == 1}
    except Exception:
        return set()


def call_signals(config):
    """(strong, weak): strong starts a recording, weak keeps it going."""
    apps = [a.lower() for a in config.watch_apps]
    browsers = [b.lower() for b in config.watch_browsers]
    windows = windows_by_exe(apps + browsers)
    audio = apps_playing_audio()
    pattern = re.compile(config.watch_title_pattern, re.IGNORECASE)

    # Zoom keeps hidden copies of these windows after a meeting, so only visible (incl. minimized) ones count
    app_meeting = any(visible and cls in ZOOM_MEETING_CLASSES for a in apps for cls, _, visible in windows.get(a, []))
    app_audio = any(a in audio for a in apps)
    tab = any(visible and pattern.search(title) for b in browsers for _, title, visible in windows.get(b, []))
    browser_audio = any(b in audio for b in browsers)

    strong = app_meeting or (tab and browser_audio)
    weak = strong or app_audio or tab or browser_audio
    return strong, weak


def notify(title, text):
    """Windows toast notification; silently ignored if it cannot be shown."""
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$n = $t.GetElementsByTagName('text');"
        f"$n.Item(0).AppendChild($t.CreateTextNode('{title}')) > $null;"
        f"$n.Item(1).AppendChild($t.CreateTextNode('{text}')) > $null;"
        "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show("
        "[Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError:
        pass


def log(message, config):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    os.makedirs(config.sessions_dir, exist_ok=True)
    with open(os.path.join(config.sessions_dir, "watch.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def free_gb():
    return psutil.virtual_memory().available / 2**30


def start_recording(config):
    live = free_gb() >= config.live_min_free_gb
    args = argparse.Namespace(quiet_call=False, mode="listen", no_mic=False, no_transcribe=not live)
    cli.cmd_start(args, config)
    state = cli.read_state(config)
    if state:
        state["started_by_watch"] = True
        state["live"] = live
        with open(cli.state_path(config), "w", encoding="utf-8") as f:
            json.dump(state, f)
    return state


def stop_recording(state, config):
    cli.cmd_stop(argparse.Namespace(), config)
    if not state.get("live"):
        # Transcribe in the background so a following call can be recorded right away
        cli.spawn("cli", ["retranscribe", os.path.basename(state["session"])],
                  os.path.join(state["session"], "retranscribe.log"))


def skip_file(config):
    return os.path.join(config.sessions_dir, "SKIP_CALL")


def run(config):
    log(f"watching {', '.join(config.watch_apps + config.watch_browsers)}", config)
    recording, skipped, last_seen = None, False, 0.0
    while True:
        now = time.time()
        strong, weak = call_signals(config)
        calling = strong or (recording is not None and weak)
        if calling:
            last_seen = now
        state = cli.read_state(config)
        active = state if state and cli.alive(state.get("recorder_pid")) else None

        if recording and os.path.exists(skip_file(config)):
            os.remove(skip_file(config))
            log(f"skip requested: discarding {recording['session']}", config)
            cli.cmd_stop(argparse.Namespace(), config)
            shutil.rmtree(recording["session"], ignore_errors=True)
            notify("Call Transcriber", "Recording of this call discarded")
            recording, skipped = None, True
        elif calling and not recording and not skipped and not active:
            log("call started: recording", config)
            recording = start_recording(config)
            mode = "with live transcript" if recording and recording.get("live") else "transcript after the call"
            notify("Call Transcriber", f"Recording started ({mode})")
        elif not calling and now - last_seen > config.watch_end_grace_sec:
            if recording:
                log(f"call ended: stopping {recording['session']}", config)
                stop_recording(recording, config)
                notify("Call Transcriber", "Recording stopped, transcript is being prepared")
                recording = None
            skipped = False
            if os.path.exists(skip_file(config)):
                os.remove(skip_file(config))
        time.sleep(POLL_SEC)


def install():
    root = config_module.PROJECT_ROOT
    pythonw = os.path.join(root, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(pythonw):
        sys.exit("Run install.cmd first")
    with open(STARTUP_SCRIPT, "w", encoding="utf-8") as f:
        f.write('Set sh = CreateObject("WScript.Shell")\n'
                f'sh.CurrentDirectory = "{root}"\n'
                f'sh.Run """{pythonw}"" -m call_transcriber watch", 0, False\n')
    subprocess.Popen(["wscript", STARTUP_SCRIPT])
    print(f"Installed: starts at Windows logon ({STARTUP_SCRIPT}); started now in the background")


def uninstall():
    if os.path.exists(STARTUP_SCRIPT):
        os.remove(STARTUP_SCRIPT)
    stopped = 0
    for p in psutil.process_iter(["name", "cmdline"]):
        args = p.info["cmdline"] or []
        # Only the running watcher ("-m call_transcriber watch"), not this --uninstall call or its launcher
        if args[-2:] == ["call_transcriber", "watch"] and p.pid != os.getpid():
            p.terminate()
            stopped += 1
    print(f"Removed from startup; stopped {stopped} running watcher(s)")

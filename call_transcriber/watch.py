"""Start and stop recording automatically when a call app is in a call.

Windows records which desktop apps are using the microphone (privacy "capability access" store).
A call app such as Zoom keeps the microphone open for the whole meeting, even when muted, so
"Zoom.exe is using the microphone" is a reliable "in a meeting" signal.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import winreg

import psutil

from . import cli
from . import config as config_module

MIC_STORE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged"
POLL_SEC = 5
STARTUP_DIR = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
STARTUP_SCRIPT = os.path.join(STARTUP_DIR, "CallTranscriberWatch.vbs")


def apps_using_mic():
    """Lower-case executable names that hold the microphone right now."""
    in_use = set()
    try:
        store = winreg.OpenKey(winreg.HKEY_CURRENT_USER, MIC_STORE)
    except OSError:
        return in_use
    with store:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(store, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(store, name) as app:
                    stop, _ = winreg.QueryValueEx(app, "LastUsedTimeStop")
            except OSError:
                continue
            if stop == 0:
                in_use.add(name.split("#")[-1].lower())
    return in_use


def in_call(config):
    return bool(apps_using_mic() & {a.lower() for a in config.watch_apps})


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
    log(f"watching {', '.join(config.watch_apps)}", config)
    recording, skipped, last_seen = None, False, 0.0
    while True:
        now = time.time()
        calling = in_call(config)
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
        cmd = " ".join(p.info["cmdline"] or [])
        if "call_transcriber" in cmd and " watch" in cmd and p.pid != os.getpid():
            p.terminate()
            stopped += 1
    print(f"Removed from startup; stopped {stopped} running watcher(s)")

"""`ct` command: start / stop / listen / silent / status / devices / retranscribe / open."""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time

import psutil

from . import config as config_module

# No DETACHED_PROCESS: the venv python.exe is a launcher that starts the real interpreter as a child,
# and a detached launcher makes that child open its own console window. A hidden console is inherited instead.
BACKGROUND = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW


def state_path(config):
    return os.path.join(config.sessions_dir, ".current.json")


def read_state(config):
    try:
        with open(state_path(config), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def alive(pid):
    return bool(pid) and psutil.pid_exists(pid)


def spawn(module, args, log_path):
    log = open(log_path, "a", encoding="utf-8")
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    return subprocess.Popen([sys.executable, "-m", f"call_transcriber.{module}", *args],
                            cwd=config_module.PROJECT_ROOT, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=BACKGROUND, env=env).pid


def tail(path, lines=5):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-lines:]
    except OSError:
        return []


def auto_cleanup(config):
    if config.keep_audio_days <= 0:
        return
    from . import cleanup
    deleted, kept, freed = cleanup.run(config.sessions_dir, config.keep_audio_days)
    if deleted:
        print(f"Deleted audio older than {config.keep_audio_days} days: {len(deleted)} session(s), "
              f"{freed / 2**20:.0f} MB freed (transcripts kept)")
    for name, reason in kept:
        print(f"Kept audio of {name}: {reason}")


def cmd_cleanup(args, config):
    from . import cleanup
    days = config.keep_audio_days if args.days is None else args.days
    if days <= 0:
        sys.exit("keep_audio_days is 0: audio is kept forever")
    state = read_state(config)
    active = state["session"] if state and alive(state.get("recorder_pid")) else None
    deleted, kept, freed = cleanup.run(config.sessions_dir, days, active, dry_run=args.dry_run)
    verb = "Would delete" if args.dry_run else "Deleted"
    for name, files in deleted:
        print(f"{verb}: {name} ({'whole archived session' if files is None else ', '.join(files)})")
    for name, reason in kept:
        print(f"Kept: {name} - {reason}")
    print(f"{verb} {freed / 2**20:.0f} MB of audio older than {days} days")


def cmd_archive(args, config):
    from . import cleanup
    session = resolve_session(args.session, config)
    state = read_state(config)
    if state and alive(state.get("recorder_pid")) and os.path.abspath(state["session"]) == os.path.abspath(session):
        sys.exit("This session is still recording")
    removed = cleanup.archive(session)
    print(f"Archived {os.path.basename(session)}: removed {', '.join(removed) or 'no text files'}; "
          f"audio is deleted after {config.keep_audio_days} days")


def cmd_start(args, config):
    state = read_state(config)
    if state and alive(state.get("recorder_pid")):
        sys.exit(f"Already recording: {state['session']}  (ct stop first)")
    auto_cleanup(config)
    os.makedirs(config.sessions_dir, exist_ok=True)
    session = os.path.join(config.sessions_dir, datetime.datetime.now().strftime("%Y-%m-%d %H%M%S"))
    os.makedirs(session)
    record_args = ["--session", session]
    if args.no_mic:
        record_args.append("--no-mic")
    if args.quiet_call:
        record_args += ["--quiet-call", "--mode", args.mode]
    record_log = os.path.join(session, "record.log")
    recorder = spawn("record", record_args, record_log)

    # Wait until the recorder has opened the devices (or failed)
    for _ in range(40):
        time.sleep(0.25)
        lines = tail(record_log, 20)
        if any(line.startswith("me   <-") for line in lines) or not alive(recorder):
            break
    print("\n".join(tail(record_log, 20)))
    if not alive(recorder):
        sys.exit("Recorder failed to start, see the lines above")

    transcriber = None
    if not args.no_transcribe:
        transcriber = spawn("live", ["--session", session], os.path.join(session, "live.log"))
    with open(state_path(config), "w", encoding="utf-8") as f:
        json.dump({"session": session, "recorder_pid": recorder, "transcriber_pid": transcriber,
                   "quiet_call": args.quiet_call, "started": time.time()}, f)
    if transcriber:
        print("\nRecording. Transcript (updated every minute):")
        print(f"  {os.path.join(session, 'transcript.md')}")
    else:
        print("\nRecording without live transcription. Afterwards: ct retranscribe")
    if args.quiet_call:
        print(f"Mode: {args.mode}. Switch with: ct listen | ct silent")
    print("Stop with: ct stop")


def cmd_stop(args, config):
    state = read_state(config)
    if not state:
        sys.exit("Nothing is recording")
    session = state["session"]
    open(os.path.join(session, "STOP"), "w").close()
    print("Stopping the recorder...", flush=True)
    while alive(state["recorder_pid"]):
        time.sleep(0.5)
    print("\n".join(tail(os.path.join(session, "record.log"), 1)))
    if state.get("transcriber_pid"):
        print("Finishing the transcript...", flush=True)
        live_log = os.path.join(session, "live.log")
        seen = len(tail(live_log, 10000))
        while alive(state["transcriber_pid"]):
            time.sleep(1)
            lines = tail(live_log, 10000)
            for line in lines[seen:]:
                if "Warning" not in line:
                    print("  " + line, flush=True)
            seen = len(lines)
        if not os.path.exists(os.path.join(session, "TRANSCRIBED")):
            print("Transcriber exited with an error, see " + live_log)
    os.remove(state_path(config))
    print(f"\nTranscript: {os.path.join(session, 'transcript.md')}")
    auto_cleanup(config)


def set_mode(mode, config):
    state = read_state(config)
    if not state or not alive(state.get("recorder_pid")):
        sys.exit("Nothing is recording")
    if not state.get("quiet_call"):
        sys.exit("This recording was started without --quiet-call; mute with the Windows volume icon instead")
    with open(os.path.join(state["session"], "MODE"), "w") as f:
        f.write(mode)
    print(f"Mode: {mode}")


def cmd_status(args, config):
    state = read_state(config)
    if not state:
        print("Not recording")
        return
    session = state["session"]
    minutes = (time.time() - state["started"]) / 60
    print(f"Session: {session} ({minutes:.0f} min)")
    print(f"Recorder:    {'running' if alive(state['recorder_pid']) else 'NOT running'}")
    if state.get("transcriber_pid"):
        print(f"Transcriber: {'running' if alive(state['transcriber_pid']) else 'NOT running'}")
    levels = tail(os.path.join(session, "levels.csv"), 1)
    if levels and not levels[0].startswith("sec"):
        sec, them, me, cpu, ram = levels[0].split(",")
        print(f"Levels: call {them} dBFS, mic {me} dBFS; CPU {cpu}%, RAM {ram}%")
    for line in tail(os.path.join(session, "record.log"), 3):
        if "WARNING" in line or "mode:" in line:
            print(line)
    asr_lines = [line for line in tail(os.path.join(session, "live.log"), 50) if line[:2].isdigit()]
    if asr_lines:
        print("Last transcription: " + asr_lines[-1])


def resolve_session(value, config):
    if value in (None, "latest"):
        sessions = sorted(d for d in os.listdir(config.sessions_dir)
                          if os.path.isdir(os.path.join(config.sessions_dir, d)))
        if not sessions:
            sys.exit("No sessions yet")
        return os.path.join(config.sessions_dir, sessions[-1])
    return value if os.path.isdir(value) else os.path.join(config.sessions_dir, value)


def cmd_open(args, config):
    session = resolve_session(args.session, config)
    os.startfile(os.path.join(session, "transcript.md"))


def cmd_retranscribe(args, config):
    from . import retranscribe
    print(retranscribe.main(resolve_session(args.session, config)))


def cmd_devices(args, config):
    from .record import list_devices
    list_devices()
    print(f"\nSettings: {config_module.CONFIG_PATH}")
    print(f"  call_device  = '{config.call_device}' (empty = Windows default output)")
    print(f"  mic_device   = '{config.mic_device}' (empty = Windows default microphone)")
    print(f"  quiet_device = '{config.quiet_device}'")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ct", description="Record and transcribe calls locally.")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="start recording and live transcription")
    start.add_argument("--quiet-call", action="store_true",
                       help="the call app outputs to the silent device; switch hearing with ct listen/silent")
    start.add_argument("--mode", choices=("listen", "silent"), default="listen", help="initial mode for --quiet-call")
    start.add_argument("--no-mic", action="store_true", help="do not record the microphone")
    start.add_argument("--no-transcribe", action="store_true", help="record only; transcribe later")
    sub.add_parser("stop", help="stop and wait for the transcript")
    sub.add_parser("listen", help="hear the call (with --quiet-call)")
    sub.add_parser("silent", help="do not hear the call, keep recording it (with --quiet-call)")
    sub.add_parser("status", help="show the running recording")
    sub.add_parser("devices", help="list audio devices and current settings")
    retr = sub.add_parser("retranscribe", help="transcribe a finished session again")
    retr.add_argument("session", nargs="?", default="latest")
    cl = sub.add_parser("cleanup", help="delete audio of sessions older than keep_audio_days (transcripts kept)")
    cl.add_argument("--dry-run", action="store_true", help="only show what would be deleted")
    cl.add_argument("--days", type=int, help="override keep_audio_days")
    ar = sub.add_parser("archive", help="transcript moved elsewhere: delete text from the session now, "
                                        "the whole session after keep_audio_days")
    ar.add_argument("session")
    op = sub.add_parser("open", help="open a transcript")
    op.add_argument("session", nargs="?", default="latest")
    args = parser.parse_args(argv)
    config = config_module.load()
    commands = {"start": cmd_start, "stop": cmd_stop, "status": cmd_status, "devices": cmd_devices,
                "retranscribe": cmd_retranscribe, "open": cmd_open, "cleanup": cmd_cleanup, "archive": cmd_archive,
                "listen": lambda a, c: set_mode("listen", c), "silent": lambda a, c: set_mode("silent", c)}
    commands[args.command](args, config)


if __name__ == "__main__":
    main()

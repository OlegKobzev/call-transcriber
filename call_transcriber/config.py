"""Settings from config.toml next to the project root, with defaults for every key."""
import os
import tomllib
from dataclasses import dataclass, fields

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.toml")


@dataclass
class Config:
    # Where session folders (audio, logs, transcripts) are written
    sessions_dir: str = os.path.join(PROJECT_ROOT, "sessions")
    # Substring of the output device whose audio is the call ("" = Windows default output)
    call_device: str = ""
    # Substring of the microphone name ("" = Windows default, virtual Steam microphones skipped)
    mic_device: str = ""
    # Silent virtual output device used by `ct start --quiet-call` (Steam Streaming Speakers, VB-Cable, ...)
    quiet_device: str = "Steam Streaming Speakers"
    # Speech recognition
    model: str = "large-v3"
    cpu_model: str = "small"
    device: str = "auto"  # auto | cuda | cpu
    languages: tuple = ("uk", "ru")
    fallback_language: str = "uk"
    # Microphone fragments quieter than this are treated as room noise, not my speech
    mic_min_level_dbfs: float = -40.0
    # Seconds between transcription cycles during a call
    interval_sec: int = 60
    # Audio (FLAC) of sessions older than this is deleted automatically once a transcript exists; 0 = never
    keep_audio_days: int = 7
    # ct watch: executables whose call starts/stops recording automatically
    watch_apps: tuple = ("Zoom.exe",)
    # Seconds without the call before the recording is stopped (rides out reconnects)
    watch_end_grace_sec: int = 60
    # Live transcription only when at least this much RAM is free at the start, otherwise after the call
    live_min_free_gb: float = 5.0


def load():
    config = Config()
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "rb") as f:
            values = tomllib.load(f)
        known = {f.name for f in fields(Config)}
        for key, value in values.items():
            if key not in known:
                raise SystemExit(f"Unknown setting '{key}' in {CONFIG_PATH}")
            setattr(config, key, tuple(value) if isinstance(value, list) else value)
    config.sessions_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(config.sessions_dir)))
    return config

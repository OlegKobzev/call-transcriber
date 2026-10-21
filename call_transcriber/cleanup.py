"""Retention of session data.

- `archive`: the transcript has been moved elsewhere (e.g. a notes vault). Text files are deleted from
  the session right away and the session is marked ARCHIVED; the audio stays for keep_audio_days.
- `run`: sessions older than keep_audio_days lose their audio. Archived sessions are deleted entirely.
  Sessions without a transcript keep their audio so nothing is lost.
"""
import datetime
import os
import shutil

AUDIO_EXTENSIONS = (".flac", ".pcm")
TRANSCRIPTS = ("transcript_v2.md", "transcript.md")
TEXT_FILES = ("transcript.md", "transcript_v2.md", "segments.jsonl", "segments_v2.jsonl",
              "dropped.jsonl", "dropped_v2.jsonl")
ARCHIVED = "ARCHIVED"


def session_date(path):
    name = os.path.basename(path)
    try:
        return datetime.datetime.strptime(name[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def has_transcript(path):
    return any(os.path.isfile(os.path.join(path, t)) and os.path.getsize(os.path.join(path, t)) > 0
               for t in TRANSCRIPTS)


def archive(path):
    """Delete the session's text files and mark it archived; returns the deleted file names."""
    removed = [f for f in TEXT_FILES if os.path.exists(os.path.join(path, f))]
    for f in removed:
        os.remove(os.path.join(path, f))
    open(os.path.join(path, ARCHIVED), "w").close()
    return removed


def find_expired(sessions_dir, keep_days, active_session=None):
    """Returns (to_delete, kept): to_delete holds (session, files) or (session, None) for the whole folder."""
    today = datetime.date.today()
    to_delete, kept = [], []
    if not os.path.isdir(sessions_dir):
        return to_delete, kept
    for name in sorted(os.listdir(sessions_dir)):
        path = os.path.join(sessions_dir, name)
        date = session_date(path)
        if not os.path.isdir(path) or date is None or (today - date).days < keep_days:
            continue
        audio = [f for f in os.listdir(path) if f.endswith(AUDIO_EXTENSIONS)]
        if active_session and os.path.abspath(path) == os.path.abspath(active_session):
            kept.append((name, "recording in progress"))
        # Sessions recorded before the RECORDED marker existed only have STOP
        elif not any(os.path.exists(os.path.join(path, m)) for m in ("RECORDED", "STOP")):
            kept.append((name, "recording not finished"))
        elif os.path.exists(os.path.join(path, ARCHIVED)):
            to_delete.append((name, None))
        elif not audio:
            continue
        elif not has_transcript(path):
            kept.append((name, "no transcript yet"))
        else:
            to_delete.append((name, audio))
    return to_delete, kept


def folder_size(path):
    return sum(os.path.getsize(os.path.join(root, f)) for root, _, files in os.walk(path) for f in files)


def run(sessions_dir, keep_days, active_session=None, dry_run=False):
    to_delete, kept = find_expired(sessions_dir, keep_days, active_session)
    freed = 0
    for name, files in to_delete:
        path = os.path.join(sessions_dir, name)
        if files is None:
            freed += folder_size(path)
            if not dry_run:
                shutil.rmtree(path)
            continue
        for f in files:
            freed += os.path.getsize(os.path.join(path, f))
            if not dry_run:
                os.remove(os.path.join(path, f))
    return to_delete, kept, freed

"""Delete audio of old sessions; transcripts, segments and logs are kept.

A session's audio is deleted when the session is older than keep_audio_days and it has a non-empty
transcript. Sessions without a transcript keep their audio so nothing is lost.
"""
import datetime
import os

AUDIO_EXTENSIONS = (".flac", ".pcm")
TRANSCRIPTS = ("transcript_v2.md", "transcript.md")


def session_date(path):
    name = os.path.basename(path)
    try:
        return datetime.datetime.strptime(name[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def has_transcript(path):
    return any(os.path.isfile(os.path.join(path, t)) and os.path.getsize(os.path.join(path, t)) > 0
               for t in TRANSCRIPTS)


def find_expired(sessions_dir, keep_days, active_session=None):
    """Returns (to_delete, kept): lists of (session, files or reason)."""
    today = datetime.date.today()
    to_delete, kept = [], []
    if not os.path.isdir(sessions_dir):
        return to_delete, kept
    for name in sorted(os.listdir(sessions_dir)):
        path = os.path.join(sessions_dir, name)
        date = session_date(path)
        if not os.path.isdir(path) or date is None:
            continue
        audio = [f for f in os.listdir(path) if f.endswith(AUDIO_EXTENSIONS)]
        if not audio or (today - date).days < keep_days:
            continue
        if active_session and os.path.abspath(path) == os.path.abspath(active_session):
            kept.append((name, "recording in progress"))
        # Sessions recorded before the RECORDED marker existed only have STOP
        elif not any(os.path.exists(os.path.join(path, m)) for m in ("RECORDED", "STOP")):
            kept.append((name, "recording not finished"))
        elif not has_transcript(path):
            kept.append((name, "no transcript yet"))
        else:
            to_delete.append((name, audio))
    return to_delete, kept


def run(sessions_dir, keep_days, active_session=None, dry_run=False):
    to_delete, kept = find_expired(sessions_dir, keep_days, active_session)
    freed = 0
    for name, files in to_delete:
        for f in files:
            path = os.path.join(sessions_dir, name, f)
            freed += os.path.getsize(path)
            if not dry_run:
                os.remove(path)
    return to_delete, kept, freed

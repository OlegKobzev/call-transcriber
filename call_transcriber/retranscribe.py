"""Re-run transcription of a finished session with the current settings.

Reads the lossless FLAC tracks, cuts them into ~60 s pieces the same way as during a call and writes
segments_v2.jsonl, dropped_v2.jsonl and transcript_v2.md next to the recording.
"""
import json
import os
import time

from faster_whisper import decode_audio

from . import asr
from . import config as config_module
from .assemble import write_transcript
from .live import ready_end


def main(session):
    config = config_module.load()
    model, model_name = asr.load_model(config)
    print(f"Model {model_name}", flush=True)
    kept, dropped = [], []
    for stream in ("them", "me"):
        audio = decode_audio(os.path.join(session, f"{stream}.flac"), sampling_rate=asr.SR)
        t = time.time()
        stream_kept, stream_dropped = [], []
        done, step = 0, 60 * asr.SR
        while done < len(audio):
            chunk = audio[done:done + step]
            take = len(chunk) if done + step >= len(audio) else ready_end(chunk)
            if take < asr.SR:
                take = len(chunk)
            k, d = asr.transcribe(model, chunk[:take], done / asr.SR, stream, config)
            stream_kept += k
            stream_dropped += d
            done += take
        print(f"{stream}: {len(audio) / asr.SR / 60:.1f} min in {time.time() - t:.0f}s, "
              f"kept {len(stream_kept)}, dropped {len(stream_dropped)}", flush=True)
        kept += stream_kept
        dropped += stream_dropped
    for name, rows in (("segments_v2.jsonl", kept), ("dropped_v2.jsonl", dropped)):
        with open(os.path.join(session, name), "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path = os.path.join(session, "transcript_v2.md")
    write_transcript(kept, path)
    return path

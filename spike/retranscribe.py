"""Re-run transcription of a finished session with the current asr.py settings.

Writes segments_v2.jsonl, dropped_v2.jsonl and transcript_v2.md next to the recording.
"""
import json
import os
import sys
import time

from faster_whisper import decode_audio

import asr
from assemble import write_transcript
from live import ready_end


def main(session):
    model = asr.load_model()
    kept, dropped = [], []
    for stream in ("them", "me"):
        audio = decode_audio(os.path.join(session, f"{stream}.flac"), sampling_rate=asr.SR)
        t = time.time()
        k, d = [], []
        # Same chunking as live.py: every 60 s of audio, cut at the end of the last finished speech
        done, step = 0, 60 * asr.SR
        for visible in list(range(step, len(audio), step)) + [len(audio)]:
            end = visible if visible == len(audio) else ready_end(audio[:visible], done)
            if end - done < asr.SR:
                continue
            chunk_kept, chunk_dropped = asr.transcribe(model, audio[done:end], done / asr.SR, stream)
            k += chunk_kept
            d += chunk_dropped
            done = end
        print(f"{stream}: {len(audio) / asr.SR / 60:.1f} min in {time.time() - t:.0f}s, "
              f"kept {len(k)}, dropped {len(d)}", flush=True)
        kept += k
        dropped += d
    for name, rows in (("segments_v2.jsonl", kept), ("dropped_v2.jsonl", dropped)):
        with open(os.path.join(session, name), "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    write_transcript(kept, os.path.join(session, "transcript_v2.md"))


if __name__ == "__main__":
    main(sys.argv[1])

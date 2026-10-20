"""Near-live transcription of a session that the recorder is still writing.

Every cycle reads only the new part of <track>.pcm (16 kHz mono float32 written by the recorder),
takes it up to the end of the last finished speech segment, transcribes it and appends to
segments.jsonl + transcript.md. Filtered-out segments go to dropped.jsonl with the reason.
Finishes once the recorder has written RECORDED and everything is processed; STOP_ASR aborts.
"""
import argparse
import json
import os
import time

import numpy as np
from faster_whisper.vad import VadOptions, get_speech_timestamps

from . import asr
from . import config as config_module
from .assemble import fmt, write_transcript

SR = asr.SR
TAIL_GUARD_SEC = 1.0
STREAMS = ("them", "me")


def ready_end(chunk):
    """Number of samples at the start of `chunk` that can be processed safely."""
    if len(chunk) < SR * 3:
        return 0
    speech = get_speech_timestamps(chunk, VadOptions(min_silence_duration_ms=500))
    if not speech:
        return len(chunk) - int(SR * TAIL_GUARD_SEC)
    finished = [s for s in speech if s["end"] < len(chunk) - SR * TAIL_GUARD_SEC]
    if not finished:
        return 0
    return min(len(chunk), finished[-1]["end"] + int(SR * 0.3))


def read_new(path, done):
    """Samples written to the .pcm file after sample index `done`."""
    total = os.path.getsize(path) // 4 if os.path.exists(path) else 0
    if total <= done:
        return np.zeros(0, dtype=np.float32)
    return np.fromfile(path, dtype=np.float32, count=total - done, offset=done * 4)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    args = parser.parse_args(argv)
    session = args.session
    config = config_module.load()

    t = time.time()
    model, model_name = asr.load_model(config)
    print(f"Model {model_name} loaded in {time.time() - t:.1f}s", flush=True)

    done = {s: 0 for s in STREAMS}
    segments = []
    log = open(os.path.join(session, "asr_log.csv"), "a")
    log.write("wall,stream,audio_sec,proc_sec,rtf,lag_sec\n")
    out = open(os.path.join(session, "segments.jsonl"), "a", encoding="utf-8")
    dropped_out = open(os.path.join(session, "dropped.jsonl"), "a", encoding="utf-8")
    transcript = os.path.join(session, "transcript.md")

    while True:
        recorded = os.path.exists(os.path.join(session, "RECORDED"))
        if os.path.exists(os.path.join(session, "STOP_ASR")):
            break
        cycle = time.time()
        for stream in STREAMS:
            chunk = read_new(os.path.join(session, f"{stream}.pcm"), done[stream])
            take = len(chunk) if recorded else ready_end(chunk)
            if take < SR:
                if recorded:
                    done[stream] += take
                continue
            offset = done[stream] / SR
            t = time.time()
            kept, dropped = asr.transcribe(model, chunk[:take], offset, stream, config)
            segments += kept
            out.writelines(json.dumps(i, ensure_ascii=False) + "\n" for i in kept)
            dropped_out.writelines(json.dumps(i, ensure_ascii=False) + "\n" for i in dropped)
            out.flush()
            dropped_out.flush()
            proc = time.time() - t
            audio_sec = take / SR
            lag = (len(chunk) - take) / SR
            log.write(f"{time.strftime('%H:%M:%S')},{stream},{audio_sec:.1f},{proc:.1f},"
                      f"{proc / audio_sec:.2f},{lag:.1f}\n")
            log.flush()
            print(f"{time.strftime('%H:%M:%S')} {stream}: {audio_sec:.0f}s audio in {proc:.1f}s "
                  f"(RTF {proc / audio_sec:.2f}), up to {fmt((done[stream] + take) / SR)}, dropped {len(dropped)}",
                  flush=True)
            done[stream] += take
            write_transcript(segments, transcript)
        if recorded:
            break
        time.sleep(max(0, config.interval_sec - (time.time() - cycle)))

    write_transcript(segments, transcript)
    out.close()
    dropped_out.close()
    log.close()
    if recorded:
        # The .pcm copies only feed the transcriber; FLAC stays as the lossless source
        for stream in STREAMS:
            path = os.path.join(session, f"{stream}.pcm")
            if os.path.exists(path):
                os.remove(path)
    open(os.path.join(session, "TRANSCRIBED"), "w").close()
    print(f"Done: {len(segments)} segments -> {transcript}", flush=True)


if __name__ == "__main__":
    main()

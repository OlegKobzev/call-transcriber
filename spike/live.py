"""Spike: near-live transcription of a session that record.py is still writing.

Every cycle re-reads them.flac / me.flac, takes the not yet processed audio up to the end of
the last finished speech segment, transcribes it and appends to segments.jsonl + transcript.md.
Filtered-out segments go to dropped.jsonl with the reason.
Stop by creating STOP_ASR in the session folder; it also finishes once the recorder has stopped
and everything is processed.
"""
import argparse
import json
import os
import time

import asr
from assemble import fmt, write_transcript
from faster_whisper import decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps

SR = asr.SR
TAIL_GUARD_SEC = 1.0


def ready_end(audio, done):
    """Absolute sample index up to which the new audio can be processed safely."""
    chunk = audio[done:]
    if len(chunk) < SR * 3:
        return done
    speech = get_speech_timestamps(chunk, VadOptions(min_silence_duration_ms=500))
    if not speech:
        return len(audio) - int(SR * TAIL_GUARD_SEC)
    finished = [s for s in speech if s["end"] < len(chunk) - SR * TAIL_GUARD_SEC]
    if not finished:
        return done
    return done + finished[-1]["end"] + int(SR * 0.3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session")
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    session = args.session

    t = time.time()
    model = asr.load_model(args.model)
    print(f"Model {args.model} loaded in {time.time() - t:.1f}s", flush=True)

    done = {"them": 0, "me": 0}
    segments = []
    log = open(os.path.join(session, "asr_log.csv"), "a")
    log.write("wall,stream,audio_sec,proc_sec,rtf,lag_sec\n")
    out = open(os.path.join(session, "segments.jsonl"), "a", encoding="utf-8")
    dropped_out = open(os.path.join(session, "dropped.jsonl"), "a", encoding="utf-8")
    transcript = os.path.join(session, "transcript.md")

    while True:
        recorder_stopped = os.path.exists(os.path.join(session, "STOP"))
        if os.path.exists(os.path.join(session, "STOP_ASR")):
            break
        cycle = time.time()
        for stream in ("them", "me"):
            audio = decode_audio(os.path.join(session, f"{stream}.flac"), sampling_rate=SR)
            end = len(audio) if recorder_stopped else ready_end(audio, done[stream])
            if end - done[stream] < SR:
                continue
            offset = done[stream] / SR
            t = time.time()
            kept, dropped = asr.transcribe(model, audio[done[stream]:end], offset, stream)
            segments += kept
            out.writelines(json.dumps(i, ensure_ascii=False) + "\n" for i in kept)
            dropped_out.writelines(json.dumps(i, ensure_ascii=False) + "\n" for i in dropped)
            out.flush()
            dropped_out.flush()
            proc = time.time() - t
            audio_sec = (end - done[stream]) / SR
            lag = (len(audio) - end) / SR
            log.write(f"{time.strftime('%H:%M:%S')},{stream},{audio_sec:.1f},{proc:.1f},"
                      f"{proc / audio_sec:.2f},{lag:.1f}\n")
            log.flush()
            print(f"{time.strftime('%H:%M:%S')} {stream}: {audio_sec:.0f}s audio in {proc:.1f}s "
                  f"(RTF {proc / audio_sec:.2f}), up to {fmt(end / SR)}, dropped {len(dropped)}",
                  flush=True)
            done[stream] = end
            write_transcript(segments, transcript)
        if recorder_stopped:
            break
        time.sleep(max(0, args.interval - (time.time() - cycle)))

    write_transcript(segments, transcript)
    print(f"Done: {len(segments)} segments -> {transcript}", flush=True)


if __name__ == "__main__":
    main()

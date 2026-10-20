"""Rebuild transcript.md from segments.jsonl, merging consecutive segments of one speaker."""
import json
import os
import sys

LABELS = {"me": "Я", "them": "Собеседник"}
MAX_GAP_SEC = 3.0


def fmt(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def write_transcript(segments, path):
    paragraphs = []
    for seg in sorted(segments, key=lambda s: s["start"]):
        last = paragraphs[-1] if paragraphs else None
        if last and last["stream"] == seg["stream"] and seg["start"] - last["end"] <= MAX_GAP_SEC:
            last["text"] += " " + seg["text"]
            last["end"] = seg["end"]
        else:
            paragraphs.append(dict(seg))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(f"**[{fmt(p['start'])}] {LABELS[p['stream']]}:** {p['text']}"
                            for p in paragraphs) + "\n")
    return len(paragraphs)


def main(session):
    with open(os.path.join(session, "segments.jsonl"), encoding="utf-8") as f:
        segments = [json.loads(line) for line in f if line.strip()]
    count = write_transcript(segments, os.path.join(session, "transcript.md"))
    print(f"{len(segments)} segments -> {count} paragraphs")


if __name__ == "__main__":
    main(sys.argv[1])

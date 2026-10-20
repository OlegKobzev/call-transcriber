"""Shared transcription step: Whisper + filters against hallucinations and room noise."""
import os
import re

import nvidia.cublas
import nvidia.cudnn
import numpy as np

# CTranslate2 on Windows needs the pip-installed CUDA DLLs on the search path
for _pkg in (nvidia.cublas, nvidia.cudnn):
    _bin = os.path.join(list(_pkg.__path__)[0], "bin")
    os.add_dll_directory(_bin)
    os.environ["PATH"] = _bin + os.pathsep + os.environ["PATH"]

from faster_whisper import WhisperModel  # noqa: E402

SR = 16000
ALLOWED_LANGUAGES = {"uk", "ru"}
FALLBACK_LANGUAGE = "uk"
# Microphone: own voice is close to the mic; distant room speech and silence are quieter
MIN_LEVEL_DBFS = {"me": -40.0, "them": None}
MAX_NO_SPEECH_PROB = 0.6
MIN_AVG_LOGPROB = -1.0
MAX_COMPRESSION_RATIO = 2.4

# Typical Whisper hallucinations learned from subtitle data
HALLUCINATIONS = re.compile(
    r"субтитр|продолжение следует|дякую за перегляд|спасибо за просмотр|подпис(ывайтесь|уйтесь)"
    r"|ставьте лайк|добро пожаловать|torzok|редактор субтитров|^\s*(thank you|thanks for watching)[.!]?\s*$"
    r"|takk for att|tack för att",
    re.IGNORECASE)


def load_model(name="large-v3"):
    return WhisperModel(name, device="cuda", compute_type="int8_float16")


def level_dbfs(audio):
    """Loudness of the speech part: 95th percentile of 50 ms frame RMS."""
    frames = audio[:len(audio) // 800 * 800].reshape(-1, 800)
    if not len(frames):
        return -120.0
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return float(20 * np.log10(np.percentile(rms, 95) + 1e-9))


def _run(model, audio, language):
    segments, info = model.transcribe(audio, language=language, vad_filter=True, beam_size=5,
                                      condition_on_previous_text=False)
    return list(segments), info


def transcribe(model, audio, offset, stream):
    """Transcribe one region; returns (kept, dropped) lists of segment dicts."""
    segments, info = _run(model, audio, None)
    language = info.language
    if language not in ALLOWED_LANGUAGES:
        segments, info = _run(model, audio, FALLBACK_LANGUAGE)
        language = FALLBACK_LANGUAGE

    kept, dropped = [], []
    min_level = MIN_LEVEL_DBFS.get(stream)
    for seg in segments:
        item = {"stream": stream, "start": round(offset + seg.start, 2), "end": round(offset + seg.end, 2),
                "text": seg.text.strip(), "language": language}
        reason = None
        if not item["text"]:
            reason = "empty"
        elif HALLUCINATIONS.search(item["text"]):
            reason = "hallucination phrase"
        elif seg.no_speech_prob > MAX_NO_SPEECH_PROB and seg.avg_logprob < MIN_AVG_LOGPROB:
            reason = "no speech"
        elif seg.compression_ratio > MAX_COMPRESSION_RATIO:
            reason = "repetition"
        elif min_level is not None:
            level = level_dbfs(audio[int(seg.start * SR):int(seg.end * SR)])
            item["level_dbfs"] = round(level, 1)
            if level < min_level:
                reason = "too quiet"
        if reason:
            item["dropped"] = reason
            dropped.append(item)
        else:
            kept.append(item)
    return kept, dropped

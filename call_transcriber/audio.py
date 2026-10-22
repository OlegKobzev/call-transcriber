"""Audio conversion shared by capture and offline transcription."""
import numpy as np
import soundfile as sf
import soxr


def mono(block):
    """Average populated channels without boosting quiet samples or changing the input.

    Some surround loopback endpoints deliver stereo plus exactly silent channels.
    Including those channels in the average needlessly attenuates speech.
    """
    if block.ndim == 1:
        return block
    active = np.any(block != 0, axis=0)
    return block[:, active].mean(axis=1) if active.any() else block[:, 0]


def read_mono(path, sample_rate):
    """Read native FLAC channels and apply the same downmix as live capture."""
    block, rate = sf.read(path, dtype="float32", always_2d=True)
    return soxr.resample(mono(block), rate, sample_rate)

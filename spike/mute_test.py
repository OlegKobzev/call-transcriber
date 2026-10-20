"""Check which kinds of "mute" still leave a signal in WASAPI loopback.

Plays a quiet test tone and measures what the loopback capture receives in each case:
baseline, endpoint mute, endpoint volume 0, per-app (session) mute, and a silent virtual device.
"""
import os
import threading
import time

import numpy as np
import pyaudiowpatch as pyaudio
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from comtypes import CLSCTX_ALL
from ctypes import POINTER, cast

TONE_AMPLITUDE = 0.01  # about -40 dBFS, barely audible
SECONDS = 2.0


def endpoint_volume():
    speakers = AudioUtilities.GetSpeakers()
    if hasattr(speakers, "EndpointVolume"):
        return speakers.EndpointVolume
    interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def own_session_volume():
    for session in AudioUtilities.GetAllSessions():
        if session.Process and session.Process.pid == os.getpid():
            return session.SimpleAudioVolume
    return None


def find(pa, name, loopback):
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d["hostApi"] != pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]:
            continue
        if bool(d.get("isLoopbackDevice")) != loopback:
            continue
        if loopback and not d["maxInputChannels"]:
            continue
        if not loopback and not d["maxOutputChannels"]:
            continue
        if name.lower() in d["name"].lower():
            return d
    raise SystemExit(f"device not found: {name} (loopback={loopback})")


def measure(pa, out_dev, loop_dev, during=None):
    rate = int(out_dev["defaultSampleRate"])
    out_ch = out_dev["maxOutputChannels"]
    loop_ch = loop_dev["maxInputChannels"]
    captured = []

    def on_capture(data, frames, info, status):
        captured.append(np.frombuffer(data, dtype=np.float32).copy())
        return (None, pyaudio.paContinue)

    cap = pa.open(format=pyaudio.paFloat32, channels=loop_ch, rate=int(loop_dev["defaultSampleRate"]),
                  input=True, input_device_index=loop_dev["index"], frames_per_buffer=1024,
                  stream_callback=on_capture)
    out = pa.open(format=pyaudio.paFloat32, channels=out_ch, rate=rate, output=True,
                  output_device_index=out_dev["index"])
    t = np.arange(int(rate * SECONDS)) / rate
    tone = (TONE_AMPLITUDE * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    undo = during() if during else None
    out.write(np.repeat(tone[:, None], out_ch, axis=1).tobytes())
    time.sleep(0.3)
    if undo:
        undo()
    out.close()
    cap.close()
    signal = np.concatenate(captured) if captured else np.zeros(1, dtype=np.float32)
    rms = float(np.sqrt(np.mean(signal ** 2)))
    return 20 * np.log10(rms) if rms > 0 else -120.0


def main():
    pa = pyaudio.PyAudio()
    default_out = pa.get_device_info_by_index(pa.get_host_api_info_by_type(pyaudio.paWASAPI)["defaultOutputDevice"])
    default_loop = pa.get_default_wasapi_loopback()
    print(f"Default output: {default_out['name']}")
    vol = endpoint_volume()

    def endpoint_mute():
        before = vol.GetMute()
        vol.SetMute(1, None)
        return lambda: vol.SetMute(before, None)

    def endpoint_zero():
        before = vol.GetMasterVolumeLevelScalar()
        vol.SetMasterVolumeLevelScalar(0.0, None)
        return lambda: vol.SetMasterVolumeLevelScalar(before, None)

    def session_mute():
        time.sleep(0.2)
        session = own_session_volume()
        if session is None:
            print("  (session not found)")
            return None
        session.SetMute(1, None)
        return lambda: session.SetMute(0, None)

    results = [
        ("baseline (default output)", measure(pa, default_out, default_loop)),
        ("Windows mute (default output)", measure(pa, default_out, default_loop, endpoint_mute)),
        ("Windows volume 0 (default output)", measure(pa, default_out, default_loop, endpoint_zero)),
        ("app muted in mixer (default output)", measure(pa, default_out, default_loop, session_mute)),
    ]
    steam_out = find(pa, "Steam Streaming Speakers", loopback=False)
    steam_loop = find(pa, "Steam Streaming Speakers", loopback=True)
    results.append(("Steam Streaming Speakers (silent device)", measure(pa, steam_out, steam_loop)))
    pa.terminate()
    print(f"Tone level: {20 * np.log10(TONE_AMPLITUDE / np.sqrt(2)):.0f} dBFS")
    for name, level in results:
        print(f"{name:45s} {level:7.1f} dBFS")


if __name__ == "__main__":
    main()

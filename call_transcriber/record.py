"""Recorder: call audio (WASAPI loopback) and microphone into two FLAC files.

Besides lossless FLAC, each track is also written as a 16 kHz mono float32 stream (<track>.pcm) that
the transcriber reads incrementally; it is deleted once the transcript is done.
Also logs per-second signal levels and CPU/GPU load. Stops when a STOP file appears in the session folder.

With --quiet-call the call app outputs to a silent virtual device and the recorder itself plays the
call into the default output. A MODE file in the session folder switches it at runtime:
"listen" - I hear the call and the microphone is recorded; "silent" - I don't hear the call and
the microphone is not recorded. The call itself is recorded in both modes.
"""
import argparse
import os
import queue
import subprocess
import sys
import threading
import time

import numpy as np
import psutil
import pyaudiowpatch as pyaudio
import soundfile as sf
import soxr

from . import config as config_module

SR = 16000
SILENCE_DBFS = -60.0
SILENCE_WARN_SEC = 30


class Track:
    def __init__(self, name, device, session):
        self.name = name
        self.device = device
        self.rate = int(device["defaultSampleRate"])
        self.channels = max(1, int(device["maxInputChannels"]))
        self.queue = queue.Queue()
        self.file = sf.SoundFile(os.path.join(session, f"{name}.flac"), "w", self.rate, self.channels,
                                 subtype="PCM_24", format="FLAC")
        self.pcm = open(os.path.join(session, f"{name}.pcm"), "ab")
        self.resampler = soxr.ResampleStream(self.rate, SR, 1, dtype="float32")
        self.frames = 0
        self.level = -120.0
        self.silent_since = time.time()
        self.enabled = True
        self.monitor = None
        self.t0 = None
        self.received = 0
        self.lock = threading.Lock()

    def pad_to(self, now, slack):
        """Enqueue silence for the time not covered by received audio (beyond `slack` seconds)."""
        with self.lock:
            gap = int((now - self.t0 - slack) * self.rate) - self.received
            if gap > 0:
                self.queue.put(np.zeros((gap, self.channels), dtype=np.float32))
                self.received += gap

    def callback(self, data, frame_count, time_info, status):
        block = np.frombuffer(data, dtype=np.float32).reshape(-1, self.channels).copy()
        if not self.enabled:
            block[:] = 0
        # Loopback sends no packets while nothing plays: fill such gaps so both tracks share one timeline
        self.pad_to(time.time() - len(block) / self.rate, 0.2)
        with self.lock:
            self.received += len(block)
            self.queue.put(block)
        if self.monitor is not None:
            self.monitor.feed(block)
        return (None, pyaudio.paContinue)

    def drain(self, elapsed, last=False):
        self.pad_to(self.t0 + elapsed, 0.5)
        while True:
            try:
                block = self.queue.get_nowait()
            except queue.Empty:
                break
            self.file.write(block)
            self.frames += len(block)
            self.pcm.write(self.resampler.resample_chunk(block.mean(axis=1)).tobytes())
            rms = float(np.sqrt(np.mean(block ** 2))) if block.size else 0.0
            self.level = 20 * np.log10(rms) if rms > 0 else -120.0
            if self.level > SILENCE_DBFS:
                self.silent_since = time.time()
        if last:
            self.pcm.write(self.resampler.resample_chunk(np.zeros(0, dtype=np.float32), last=True).tobytes())
        self.pcm.flush()

    def close(self):
        self.file.close()
        self.pcm.close()


class Monitor:
    """Plays captured call audio into the default output while enabled."""
    MAX_BUFFER_SEC = 0.3

    def __init__(self, rate, channels):
        self.rate = rate
        self.channels = channels
        self.enabled = False
        self.buffer = np.zeros((0, channels), dtype=np.float32)
        self.lock = threading.Lock()

    def feed(self, block):
        if not self.enabled:
            return
        with self.lock:
            self.buffer = np.concatenate([self.buffer, block])
            # keep latency bounded if playback falls behind
            limit = int(self.rate * self.MAX_BUFFER_SEC)
            if len(self.buffer) > limit:
                self.buffer = self.buffer[-limit:]

    def callback(self, data, frame_count, time_info, status):
        with self.lock:
            out = self.buffer[:frame_count]
            self.buffer = self.buffer[frame_count:]
        if len(out) < frame_count:
            out = np.concatenate([out, np.zeros((frame_count - len(out), self.channels), dtype=np.float32)])
        if not self.enabled:
            out = np.zeros_like(out)
        return (out.astype(np.float32).tobytes(), pyaudio.paContinue)


def wasapi_devices(pa):
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
    return [d for d in (pa.get_device_info_by_index(i) for i in range(pa.get_device_count()))
            if d["hostApi"] == wasapi]


def find_device(pa, wanted, loopback):
    candidates = [d for d in wasapi_devices(pa)
                  if d["maxInputChannels"] and bool(d.get("isLoopbackDevice")) == loopback]
    if wanted:
        for d in candidates:
            if wanted.lower() in d["name"].lower():
                return d
        sys.exit(f"No device matching '{wanted}': {[d['name'] for d in candidates]}")
    if loopback:
        return pa.get_default_wasapi_loopback()
    default = pa.get_device_info_by_index(pa.get_host_api_info_by_type(pyaudio.paWASAPI)["defaultInputDevice"])
    if "steam" not in default["name"].lower():
        return default
    real = [d for d in candidates if "steam" not in d["name"].lower()]
    return (real or candidates)[0]


def list_devices():
    pa = pyaudio.PyAudio()
    default_out = pa.get_device_info_by_index(pa.get_host_api_info_by_type(pyaudio.paWASAPI)["defaultOutputDevice"])
    print("Outputs (call audio is recorded from one of these):")
    for d in wasapi_devices(pa):
        if d["maxOutputChannels"]:
            mark = "  <- Windows default" if d["index"] == default_out["index"] else ""
            print(f"  {d['name']}{mark}")
    print("Microphones:")
    for d in wasapi_devices(pa):
        if d["maxInputChannels"] and not d.get("isLoopbackDevice"):
            print(f"  {d['name']}")
    pa.terminate()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--no-mic", action="store_true", help="do not record the microphone")
    parser.add_argument("--quiet-call", action="store_true",
                        help="record the silent virtual device and play the call into the default output")
    parser.add_argument("--mode", choices=("listen", "silent"), default="listen")
    args = parser.parse_args(argv)
    config = config_module.load()

    session = args.session
    os.makedirs(session, exist_ok=True)
    stop_file = os.path.join(session, "STOP")
    mode_file = os.path.join(session, "MODE")

    pa = pyaudio.PyAudio()
    loopback = find_device(pa, config.quiet_device if args.quiet_call else config.call_device, loopback=True)
    mic = find_device(pa, config.mic_device, loopback=False)

    them = Track("them", loopback, session)
    me = Track("me", mic, session)

    start = time.time()
    them.t0 = me.t0 = start
    streams = []
    for track in (them,) if args.no_mic else (them, me):
        streams.append(pa.open(format=pyaudio.paFloat32, channels=track.channels, rate=track.rate,
                               input=True, input_device_index=track.device["index"],
                               frames_per_buffer=1024, stream_callback=track.callback))

    monitor = None
    if args.quiet_call:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speaker = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if config.quiet_device.lower() in speaker["name"].lower():
            sys.exit("Windows default output is the silent device; set headphones as the default output")
        monitor = Monitor(them.rate, them.channels)
        them.monitor = monitor
        streams.append(pa.open(format=pyaudio.paFloat32, channels=them.channels, rate=them.rate, output=True,
                               output_device_index=speaker["index"], frames_per_buffer=512,
                               stream_callback=monitor.callback))
        with open(mode_file, "w") as f:
            f.write(args.mode)
        print(f"Playback -> {speaker['name']}")
    mode = None

    gpu_log = open(os.path.join(session, "gpu.csv"), "w")
    try:
        gpu_proc = subprocess.Popen(
            ["nvidia-smi", "--query-gpu=timestamp,utilization.gpu,memory.used,power.draw",
             "--format=csv,noheader", "-l", "1"], stdout=gpu_log, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except FileNotFoundError:
        gpu_proc = None

    log = open(os.path.join(session, "levels.csv"), "w")
    log.write("sec,them_dbfs,me_dbfs,cpu_pct,ram_pct\n")

    print(f"Session: {session}")
    print(f"them <- {loopback['name']} ({them.rate} Hz, {them.channels} ch)")
    print("me   <- (disabled)" if args.no_mic else f"me   <- {mic['name']} ({me.rate} Hz, {me.channels} ch)",
          flush=True)

    warned = False
    try:
        while not os.path.exists(stop_file):
            time.sleep(1)
            if monitor is not None:
                try:
                    with open(mode_file) as f:
                        wanted = f.read().strip()
                except OSError:
                    wanted = mode
                if wanted in ("listen", "silent") and wanted != mode:
                    mode = wanted
                    monitor.enabled = me.enabled = mode == "listen"
                    print(f"[{int(time.time() - start)}s] mode: {mode}", flush=True)
            elapsed = time.time() - start
            them.drain(elapsed)
            me.drain(elapsed)
            sec = int(elapsed)
            log.write(f"{sec},{them.level:.1f},{me.level:.1f},{psutil.cpu_percent():.0f},"
                      f"{psutil.virtual_memory().percent:.0f}\n")
            log.flush()
            silent = time.time() - them.silent_since
            if silent > SILENCE_WARN_SEC and not warned:
                print(f"[{sec}s] WARNING: no signal from call audio for {int(silent)}s", flush=True)
                warned = True
            elif silent <= SILENCE_WARN_SEC:
                warned = False
    except KeyboardInterrupt:
        pass
    finally:
        for s in streams:
            s.stop_stream()
            s.close()
        them.drain(time.time() - start, last=True)
        me.drain(time.time() - start, last=True)
        them.close()
        me.close()
        if gpu_proc:
            gpu_proc.terminate()
        gpu_log.close()
        log.close()
        pa.terminate()
        open(os.path.join(session, "RECORDED"), "w").close()
        print(f"Saved: them {them.frames / them.rate:.0f}s, me {me.frames / me.rate:.0f}s -> {session}", flush=True)


if __name__ == "__main__":
    main()

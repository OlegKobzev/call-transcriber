"""Post-install check: download the model, make sure it loads, show audio devices."""
import time

from . import asr
from . import config as config_module
from .record import list_devices


def main():
    config = config_module.load()
    cuda = config.device == "cuda" or (config.device == "auto" and asr.cuda_available())
    print(f"GPU (CUDA): {'yes' if cuda else 'no - transcription will run on CPU with the smaller model'}")
    print("Downloading / loading the speech model (first time ~3 GB for large-v3)...", flush=True)
    t = time.time()
    _, name = asr.load_model(config)
    print(f"Model {name} ready in {time.time() - t:.0f}s\n")
    list_devices()
    import pyaudiowpatch as pyaudio
    pa = pyaudio.PyAudio()
    names = [pa.get_device_info_by_index(i)["name"] for i in range(pa.get_device_count())]
    pa.terminate()
    if not any(config.quiet_device.lower() in n.lower() for n in names):
        print(f"\nNote: silent device '{config.quiet_device}' not found - `ct start --quiet-call` will not work.")
        print("Install a virtual audio cable (e.g. VB-Cable) and set quiet_device in config.toml.")


if __name__ == "__main__":
    main()

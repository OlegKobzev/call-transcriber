# Call Transcriber

Local recorder and transcriber for calls on Windows — Zoom, Google Meet or anything else that plays through
the speakers. It records the call audio and your microphone as two separate tracks and transcribes them with
Whisper while the call is still going, so the text is ready about a minute after you hang up.
Nothing leaves the computer.

- Two tracks: the call (what you hear) and you (your microphone) — the transcript is labelled «Я» / «Собеседник».
- Lossless FLAC is always kept; the transcript can be rebuilt later with another model.
- Tuned for Ukrainian with Russian words mixed in; filters drop Whisper's invented phrases and room noise.
- You can stop hearing a call and still record it in full.

## Requirements

- Windows 10 or 11.
- Python 3.11+ (`winget install Python.Python.3.13`).
- An NVIDIA GPU with ~4 GB of memory for live transcription with `large-v3`.
  Without one, transcription runs on the CPU with a smaller model and is slower.
- ~5 GB of disk space (model, libraries, recordings).
- Headphones during calls — otherwise the other side's voice also gets into your microphone track.

## Install

```
git clone <repository-url> call_transcriber
cd call_transcriber
install.cmd
```

`install.cmd` creates a private Python environment in `.venv`, installs the packages, adds the CUDA libraries if
an NVIDIA GPU is present, creates `config.toml`, downloads the speech model and lists your audio devices.
Add the folder to `PATH` to use `ct` from anywhere.

## Use

| Command | What it does |
|---|---|
| `ct start` | start recording and live transcription |
| `ct status` | is it running, signal levels, last transcription cycle |
| `ct stop` | stop, finish the transcript, print its path |
| `ct open` | open the latest transcript |
| `ct devices` | list audio devices and current settings |
| `ct retranscribe [session]` | transcribe a finished session again with the current settings |

Recordings go to `sessions\<date time>\`: `them.flac`, `me.flac`, `segments.jsonl`, `dropped.jsonl`
(what the filters removed and why) and `transcript.md`.

### Not hearing a call while still recording it

- **Mute everything:** mute Windows sound (volume icon or the mute key). Recording continues at full level.
- **Mute only the call, keep hearing other things (e.g. a Meet in the browser):**
  1. In the call app set the speaker to the silent device — **Steam Streaming Speakers** if Steam is installed,
     otherwise a virtual cable such as VB-Cable (set `quiet_device` in `config.toml`).
  2. `ct start --quiet-call` — the recorder plays the call into your headphones itself.
  3. `ct silent` / `ct listen` switch hearing on and off; the call is recorded either way.
     In `silent` mode your microphone is not recorded, so talking in another call does not end up in this transcript.

Do **not** mute inside Zoom, in a browser tab or in the Windows volume mixer — the recording becomes silent.

## Settings

`config.toml` (created from `config.example.toml`): sessions folder, device names, model, languages,
microphone threshold, transcription interval. Every line is optional.

## Privacy

Recordings contain other people's voices — tell them you are recording. `sessions/` is git-ignored;
never commit or share it.

## Layout

```
call_transcriber/   package
  cli.py            ct command
  record.py         recorder (FLAC + 16 kHz stream for the transcriber)
  live.py           transcription during the call
  asr.py            Whisper + filters
  assemble.py       transcript.md
  retranscribe.py   offline re-run
  prepare.py        post-install check
  config.py         settings
tools/mute_test.py  which kinds of mute keep the loopback signal (needs requirements-tools.txt)
```

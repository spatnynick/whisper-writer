# Fork notes

Personal fork of [savbell/whisper-writer](https://github.com/savbell/whisper-writer), a
hotkey-triggered dictation tool. Run on three computers from this same fork; `origin` is
this fork, `upstream` is the original (unmaintained since Aug 2024) project.

## Why this fork exists

Upstream doesn't work out of the box on a current Ubuntu system. Three real bugs, all fixed here:

1. **Doesn't install on Python 3.12** (Ubuntu 24.04's default, no `python3.11` package
   available without a third-party PPA). `numpy==1.24.3` and `onnxruntime==1.16.3` have no
   3.12 wheel; `numba`, `llvmlite`, `aiohttp`, `aiosignal`, `async-timeout`, `multidict`,
   `yarl`, `frozenlist`, `networkx` fail to build on 3.12 — and are confirmed **unused**
   (not imported anywhere in `src/`, not required by any package that *is* used — checked
   via `pip show <pkg>` → empty `Required-by`). Dropped rather than fixed.
2. **`Ctrl+Shift+<any letter>` could fire the `Ctrl+Shift+Space` hotkey**, stealing shortcuts
   from other apps (e.g. Thunderbird's Reply). `pynput` reports the *shifted* character while
   Shift is held (`'R'`, not `'r'`), which missed the lowercase-only key map in
   `src/key_listener.py` and silently fell back to `KeyCode.SPACE`. Fixed: case-insensitive
   lookup, and unmapped keys are now dropped instead of defaulting to Space. Also tightened
   the chord matcher to require an exact key set (no extra keys held) as defense in depth.
3. **Crashes on the second recording.** `sounddevice`'s CFFI callback bridge hits a native
   `Fatal Python error: PyGILState_Release: auto-releasing thread-state, but no thread-state
   for this thread` and aborts the whole process on the second `sd.InputStream`/`sd.rec()`
   call in one session — reproduces standalone, outside the app, and identically on the
   *original* pinned `sounddevice==0.4.6`, so it's a pre-existing upstream bug, not something
   this fork's version bumps caused. `PyAudio` (a hand-written C extension, not CFFI) does not
   hit it across repeated open/close cycles. Fixed in `src/result_thread.py`: recording now
   goes through `PyAudio` instead of `sounddevice`.

## What's different from upstream, file by file

- `requirements.txt` — Python-3.12-compatible pins; `sounddevice` → `pyaudio`; added
  `PyGObject` (needed by `audioplayer`'s Linux backend, `import gi`, upstream never pinned
  it) and `setuptools<81` (`webrtcvad-wheels` still imports the now-removed `pkg_resources`).
- `src/key_listener.py` — the two hotkey fixes above.
- `src/result_thread.py` — `PyAudio` instead of `sounddevice` for recording.
- `src/config_schema.yaml` — updated the `sound_device` help text (referenced
  `python -m sounddevice`, which no longer exists in the dependency set).
- `start.sh` (new) — launcher: calls `venv/bin/python3` directly rather than
  `source venv/bin/activate` (`activate` hardcodes an absolute path and breaks if the venv
  directory is ever renamed/moved), and sets a placeholder `OPENAI_API_KEY` (the app reads
  the key from the environment even when `base_url` points at a local, self-hosted endpoint
  that doesn't check it — `config.yaml`'s own `api_key` field is dead code for this path).

## System dependencies (apt, beyond what a fresh Ubuntu 24.04 has)

```
build-essential portaudio19-dev libgirepository-2.0-dev libcairo2-dev \
gobject-introspection pkg-config python3-dev python3-venv
```

## Setup on a new machine

No `pyenv` needed — system Python 3.12 works directly now.

```
gh auth login --hostname github.com --git-protocol https --web   # interactive: browser approval
git clone https://github.com/spatnynick/whisper-writer.git ~/apps/whisper-writer
cd ~/apps/whisper-writer
git remote add upstream https://github.com/savbell/whisper-writer.git
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

Then `src/config.yaml` (gitignored, per-machine — see below), the KDE desktop entry/icon, and
`start.sh` (already executable, tracked in git). Launch via `~/apps/whisper-writer/start.sh`
or the "WhisperWriter" KDE menu entry.

## Config (kept identical on all three machines)

`src/config.yaml` is intentionally gitignored (see `.gitignore`) so each machine can diverge,
but in practice all three should carry this exact content — self-hosted STT on the NAS,
`Ctrl+Shift+Space` press-to-toggle:

```yaml
misc:
  hide_status_window: false
  noise_on_completion: false
  print_to_terminal: true
model_options:
  api:
    api_key: null
    base_url: http://192.168.98.3:8100/v1
    model: deepdml/faster-whisper-large-v3-turbo-ct2
  common:
    initial_prompt: null
    language: null
    temperature: 0.0
  local:
    compute_type: default
    condition_on_previous_text: true
    device: auto
    model: base
    model_path: null
    vad_filter: false
  use_api: true
post_processing:
  add_trailing_space: true
  input_method: pynput
  remove_capitalization: false
  remove_trailing_period: false
  writing_key_press_delay: 0.005
recording_options:
  activation_key: ctrl+shift+space
  input_backend: pynput
  min_duration: 100
  recording_mode: press_to_toggle
  sample_rate: 16000
  silence_duration: 900
  sound_device: null
```

`input_backend: pynput` is required, not cosmetic — `auto` picks `evdev` (importable, so
`is_available()` returns true) whenever the library is installed, but `evdev` needs read
access to `/dev/input/eventN` (owned `root:input`), which this account isn't a member of.
`evdev` then fails `PermissionError` at startup, and PyQt silently swallows it — the app
*looks* fine (tray icon present) but the hotkey never engages. `pynput` listens via X11
instead and needs no special permissions.

## Known non-issues (already investigated, don't re-open)

- Rebinding the hotkey **through the app's own Settings UI** throws "Invalid shortcut
  combination" for every combination — unrelated, unfixed upstream bug
  (issue #1559, duplicate of #1264, closed as "not planned"). Edit `activation_key` in
  `config.yaml` directly instead; the app itself has no problem with it.
- Idle CPU is ~0% (verified) — this was a real, separate problem with a different dictation
  app (Whispering) we evaluated and rejected before landing on WhisperWriter; not relevant here.

## Pulling upstream changes later

```
git fetch upstream
git merge upstream/main   # resolve conflicts, re-verify the three fixes above still apply
```

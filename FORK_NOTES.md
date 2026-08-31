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
sudo mkdir -p /opt/whisper-writer
sudo chown bogo:bogo /opt/whisper-writer
git clone https://github.com/spatnynick/whisper-writer.git /opt/whisper-writer
cd /opt/whisper-writer
git remote add upstream https://github.com/savbell/whisper-writer.git
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
sudo chown bogo:users /opt/whisper-writer   # directory itself; files stay bogo:bogo
```

Lives at `/opt/whisper-writer`, owned by `bogo` (not root) so it can be updated with a plain
`git pull` — no `sudo` needed day to day, matching how `openwhispr` and `whispering` were set
up under `/opt` before. Then `src/config.yaml` (gitignored, per-machine — see below), the KDE
desktop entry/icon (`Exec=/opt/whisper-writer/start.sh`, `Path=/opt/whisper-writer`), and
`start.sh` (already executable, tracked in git). Launch via `/opt/whisper-writer/start.sh` or
the "WhisperWriter" KDE menu entry.

If the venv was ever created at a different path and then moved (e.g. relocated into
`/opt`), rebuild it rather than trying to reuse it — `venv/bin/activate` and `venv/bin/pip`
both hardcode the venv's absolute path at creation time and break silently (fall through to
the system Python, or a broken shebang) if the directory is renamed or moved afterward. This
is exactly why `start.sh` calls `venv/bin/python3` directly instead of sourcing `activate`.

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

## UI refresh, auto-start, debug logging (2026-08-31)

A second round of changes, independent of the three bug fixes above — cosmetic/UX polish, not
correctness fixes:

- **Auto-starts listening on launch.** `main.py` no longer shows the "Start/Settings" popup on
  startup — it calls `key_listener.start()` directly, same as clicking Start. The main window
  still exists and is reachable via the tray's "WhisperWriter Main Menu" action.
  `KeyListener.start()` is now idempotent (safe to call twice) since this made a redundant
  Start click a real possibility.
- **`--debug` flag** (`start.sh --debug` / `run.py --debug`): enables DEBUG-level logging to
  `~/.cache/whisper-writer/debug.log` (stdout too) via `src/logging_setup.py`. Instrumented:
  `input_simulation.py typewrite()` (timing), `key_listener.py on_input_event()` (every raw key
  event + activate/deactivate), `result_thread.py` (recording/stream timing, alongside the
  existing `console_print` calls, not replacing them), `transcription.py transcribe_api()`
  (HTTP call duration specifically, to isolate NAS network latency). Off by default — zero
  behavior change when the flag isn't passed. `run.py` now forwards its own argv to the
  `src/main.py` subprocess it spawns (it silently dropped all args before).
- **New app icon and status-popup icons.** `assets/ww-logo.svg`/`.png`/`.ico` — a flat indigo
  (`#4F46E5`) circle with a white mic glyph, replacing the old generic 800x800 logo.
  `assets/icon-mic.svg`/`icon-pencil.svg` → `assets/microphone.png`/`pencil.png` — redesigned
  as solid-black flat silhouettes on transparent backgrounds (pure alpha masks), so the status
  popup can tint them at runtime to match the active theme instead of shipping fixed-color art.
  The KDE desktop entry's `Icon=` should point directly at the tracked
  `/opt/whisper-writer/assets/ww-logo.png` (absolute path), not an icon-theme name — an earlier
  attempt copied it into `~/.local/share/icons/hicolor/...` and used `Icon=whisper-writer`,
  which didn't reliably pick up a later icon update through KDE's icon-theme cache. Pointing at
  the file directly means a future icon change only needs `git pull` +
  `kbuildsycoca5 --noincremental`, no re-copy step, and it works the same way on every machine
  that clones this fork since the icon travels with the repo.
- **`src/ui/base_window.py` now follows the active KDE/Qt palette** instead of a hardcoded
  white background + `#404040` text — `self.card_color`/`text_color`/`accent_color` are
  resolved from `QPalette.Window`/`WindowText`/`Highlight`, so every window automatically
  matches light or dark KDE color schemes with no per-theme code. Added a real drop shadow
  (`QGraphicsDropShadowEffect` on the inset card widget — see `SHADOW_MARGIN` below), dropped
  the hardcoded `'Segoe UI'` font (doesn't exist on Linux, was silently falling back anyway —
  now just overrides size/weight and inherits the system font), and added a shared
  `_build_stylesheet()` QSS builder (flat rounded buttons/inputs/tabs) inherited by every
  `BaseWindow` subclass. `MainWindow`'s Start button is now visually primary (accent-filled,
  `objectName('primaryButton')`).
  - **Gotcha:** `BaseWindow.SHADOW_MARGIN = 16` (px) pads the actual top-level window beyond
    the caller-requested `width`/`height` to leave room for the shadow to render (a frameless
    translucent window can't paint outside its own pixel bounds). `self.width()`/`height()` on
    any subclass therefore return `requested + 32`, not the requested size — anything doing
    manual on-screen positioning math (see `StatusWindow.show()`) needs to account for this.
  - `BaseWindow` also gained `show_title_bar` (constructor arg, default `True`) and
    `self.corner_radius` (default `16`) so a subclass can opt out of the title bar and use a
    different corner radius (`StatusWindow` sets both, for its pill shape).
- **Status popup redesigned**: shrunk from a boxy `320x120` to a small `200x56` pill
  (`corner_radius = 28`, no title bar), theme-tinted icons (mic tinted with `accent_color`
  while recording, pencil tinted with `text_color` while transcribing — the mic/pencil
  distinction itself is unchanged, still shows which state the app is in), a subtle pulsing
  opacity animation on the mic icon while recording. Position is now configurable —
  `misc.status_window_position` in `config_schema.yaml` (`bottom_right` / `bottom_center` /
  `bottom_left` / `top_right` / `top_center` / `top_left` / `center`, default `bottom_right`,
  shows up automatically as a Settings dropdown since the schema already auto-generates one for
  any `str` field with `options`).

## Backported fixes from upstream's open PRs (2026-08-31)

Upstream (`savbell/whisper-writer`) is unmaintained but still has 23 open PRs. Reviewed all of
them for anything worth reusing rather than re-discovering the same bugs later. Most were
Windows-specific, superseded by fixes this fork already made independently (pynput key-mapping
SPACE fallback, exact-match key chords, double-start guarding — see PRs #75, #154, #148), or
large abandoned rewrites (#61, #102, #103, #136) not worth grafting onto a stable personal fork.
Three small ones were genuinely missing and got backported directly (not full merges, just the
relevant lines):

- **#72** — `post_process_transcription()` was adding a trailing space even to an empty
  transcription (a silent/false-trigger recording), typing a stray space each time.
- **#148** — `PynputBackend.start()` now calls `self.stop()` first, so it's safe to call twice
  in a row without leaking an X11 listener connection. (`KeyListener` already guarded this one
  layer up; this covers the backend directly too, in case anything ever calls it without going
  through the wrapper.)
- **#155** — `config.yaml`/`config_schema.yaml` are now read with `encoding='utf-8-sig'`, so a
  BOM-prefixed file (e.g. saved once from a Windows editor) doesn't break YAML parsing;
  `config.yaml` is written back as plain `utf-8`.

If pulling in upstream changes later (below), these three are already covered — don't worry if
`git merge` reports them as already-applied/no-op.

## Recording toggle sounds, Settings window overhaul (2026-08-31)

- Two short tones now play on the recording start/stop toggle (not on transcription
  completion — that's still `misc.noise_on_completion`), matching a feature OpenWhispr had.
  Toggle with `misc.play_toggle_sounds` (default on). **Gotcha:** `AudioPlayer(...).play(block=False)`
  on a throwaway object produces no audio at all — its GStreamer pipeline is torn down by
  Python's GC before playback starts, since nothing holds a reference once play() returns.
  Fixed by keeping `self.recording_start_sound`/`self.recording_stop_sound` as persistent
  instance attributes, reused across every toggle. If adding another non-blocking sound
  anywhere in this app, reuse this pattern rather than a fresh `AudioPlayer(...)` per call.
  Loudness is a real Settings option (`misc.toggle_sound_volume`, 0-100, default 25) applied via
  `AudioPlayer.volume`, not baked into the WAV amplitude — after two rounds of "still too loud"
  from re-baking the files, this needed to be a runtime knob instead.
- Settings window switched to a normal, non-transparent, native-look top-level window instead
  of the frameless "card" style — `BaseWindow` now takes a `frameless=True/False` switch; only
  `SettingsWindow` opts out. (`MainWindow` was later removed entirely — see below — so
  `StatusWindow` is now the only other `BaseWindow` subclass, still a frameless card.)
  Reset/Save buttons moved onto one row, right-aligned.
- New **About** tab in Settings: commit hash/date, commits-ahead-of-upstream count, and
  clickable links for this fork and upstream — all read live via `git` subprocess calls in
  `settings_window.py` (`git_info()`/`github_web_url()`), nothing hardcoded. If this repo is
  ever forked again under a different GitHub owner, it'll show correctly with zero code changes
  as long as the `origin`/`upstream` remotes are named the same.

## Recover a lost transcript (2026-08-31)

Nothing is persisted to disk — if `typewrite()` fails to reach the focused window (wrong
window had focus, target app crashed, etc.), the transcript used to just be gone. The tray menu
now has a **"Copy Last Transcript"** item (greyed out until at least one transcript exists) that
copies the most recently transcribed text to the clipboard. It's purely manual/opt-in — the app
never touches the clipboard on its own, only in-memory tracking (`WhisperWriterApp.last_transcript`
in `src/main.py`), cleared on restart.

## Tray-only app, no more Main Window (2026-08-31)

`MainWindow` (`src/ui/main_window.py`) was just a "Start" / "Settings" button pair shown on
launch — pure friction, since the app already auto-starts listening on launch and both actions
were already duplicated in the tray menu. Removed entirely:

- `src/ui/main_window.py` deleted.
- `src/main.py`: no longer constructs it, tray menu's "WhisperWriter Main Menu" item removed.
  Tray menu is now just Open Settings / Copy Last Transcript / Exit — each with a small standard
  Qt icon (`QStyle.SP_*` — no new asset files needed) instead of plain text.
- The app now has exactly one window a user ever sees: Settings (opened from the tray). Nothing
  else references `MainWindow`/`main_window` — confirmed via a full grep of `src/` before deleting.
- **Gotcha this caused:** `QApplication.quitOnLastWindowClosed` defaults to `True`. With
  `MainWindow` gone, Settings became the *only* window Qt ever sees shown — so closing it (as
  "the last window") quit the entire app, tray icon and key listener included, not just the
  window. Fixed with one line in `WhisperWriterApp.__init__`:
  `self.app.setQuitOnLastWindowClosed(False)`. The tray icon (`exit_app`) now controls app
  lifetime, not window count. If any future window is added and shown persistently, re-check
  this still behaves as intended.

## Status popup: colored border for recording/transcribing (2026-08-31)

The pill was too subtle to notice out of the corner of your eye. `BaseWindow` now supports an
optional colored ring around its painted "card" (`self.border_color: QColor|None`,
`self.border_width`, default `None`/off — generic, any frameless subclass can use it, drawn as
a `QPen` stroke on the same rounded-rect `QPainterPath` used for the fill). `StatusWindow` sets
it per status: red (`#E53935`) while recording, amber (`#F59E0B`) while transcribing — the same
hex values as the tray icon's status glyphs (`ww-logo-recording.svg`/`ww-logo-transcribing.svg`),
so the popup and tray icon read as one consistent status language rather than introducing a
second color scheme. Remember to call `self.update()` after changing `border_color` — the window
may already be visible (recording → transcribing) rather than freshly shown.

## Settings: only nag/restart when something that needs it actually changed (2026-08-31)

Two separate annoyances, same root cause — the Settings window used to treat every close/save
identically regardless of whether anything changed:

- **Closing without saving** always asked "Are you sure?", even if you'd touched nothing.
- **Saving** always showed "Settings have been saved. The application will now restart." and
  triggered a full process restart (`WhisperWriterApp.restart_app`) — even for a change like
  toggle-sound volume that doesn't need one. Worse, the old `save_settings()` called
  `self.close()` *after* already emitting `settings_saved` — which re-entered the unconditional
  `closeEvent()` confirmation dialog a second time before the process actually quit. Every save
  briefly flashed a redundant "close without saving?" dialog.

Fixed with dirty-tracking + a per-setting `live_reload` schema flag:

- `SettingsWindow.baseline_values`: a `{(category, sub_category, key): typed_value}` snapshot
  taken right after the widgets are built, and refreshed after every successful `Reset` or
  `Save`. `changed_settings()` compares live widget values against this baseline.
- `closeEvent()` now only shows the confirmation dialog when `changed_settings()` is non-empty;
  otherwise it closes immediately. Since a successful save refreshes the baseline *before*
  `self.close()` runs, the redundant post-save dialog is gone too — `changed_settings()` is
  empty by the time `closeEvent()` fires.
- `save_settings()`: if nothing changed, it's a no-op close. Otherwise it checks whether every
  changed field has `live_reload: true` in `config_schema.yaml` (and `SettingsWindow.
  allow_live_reload` is `True` — only set once `WhisperWriterApp.initialize_components()` has
  actually run, so a first-run save with no config.yaml yet still takes the restart path, since
  that's what creates the components in the first place). If so, it emits `settings_saved_live`
  instead of `settings_saved`; `main.py`'s `apply_live_settings()` handles it with no restart and
  no dialog.
  - Currently flagged `live_reload: true`: `misc.print_to_terminal`, `misc.noise_on_completion`,
    `misc.status_window_position`, `misc.show_tray_status_icon`, `misc.play_toggle_sounds`,
    `misc.toggle_sound_volume`. All of these except the last were *already* read live via
    `ConfigManager.get_config_value()` at the point of use (checked each call site before
    flagging) — the flag just stops the app from unnecessarily restarting for them. Volume is
    the one exception: it's cached on the `AudioPlayer.volume` property at construction time, so
    `apply_live_settings()` explicitly re-sets it on both persistent sound-player instances.
  - Everything else (model options, recording options, post-processing, `hide_status_window`)
    still restarts on save — those genuinely are read once at construction (model load, key
    listener backend, input simulator method, `StatusWindow` existing at all) and re-reading
    them live isn't safe to fake with a flag; a real restart is correct there.
  - **If you add a new schema field**: only mark it `live_reload: true` after verifying its
    call site actually re-reads `ConfigManager.get_config_value()` on every use rather than
    caching a value at `__init__`/construction time — mislabeling something as live-reload-safe
    will make a save silently apply nothing until the next real restart.

## Pulling upstream changes later

```
git fetch upstream
git merge upstream/main   # resolve conflicts, re-verify the three fixes above still apply
```

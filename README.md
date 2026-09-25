# <img src="./assets/ww-logo.png" alt="WhisperWriter icon" width="25" height="25"> WhisperWriter

> **Personal fork** of [savbell/whisper-writer](https://github.com/savbell/whisper-writer)
> (unmaintained upstream since August 2024) for Linux/X11. This README
> describes the fork as it is today. [FORK_NOTES.md](FORK_NOTES.md) records why individual
> changes were made, [REVIEW.md](REVIEW.md) the code reviews, remaining risks and feature plans,
> and [CHANGELOG.md](CHANGELOG.md) what changed.

![version](https://img.shields.io/badge/version-1.0.1-blue)

<p align="center">
    <img src="./assets/ww-demo-image-02.gif" alt="WhisperWriter demo gif" width="340" height="136">
</p>

**Update (2024-05-28):** I've just merged in a major rewrite of WhisperWriter! We've migrated from using `tkinter` to using `PyQt5` for the UI, added a new settings window for configuration, a new continuous recording mode, support for a local API, and more! Please be patient as I work out any bugs that may have been introduced in the process. If you encounter any problems, please [open a new issue](https://github.com/savbell/whisper-writer/issues)!

WhisperWriter is a small speech-to-text app that uses [OpenAI's Whisper model](https://openai.com/research/whisper) to auto-transcribe recordings from a user's microphone to the active window.

Once started, the script runs in the background and waits for a keyboard shortcut to be pressed (`ctrl+shift+space` by default). When the shortcut is pressed, the app starts recording from your microphone. There are four recording modes to choose from:
- `continuous` (default): Recording will stop after a long enough pause in your speech. The app will transcribe the text and then start recording again. To stop listening, press the keyboard shortcut again.
- `voice_activity_detection`: Recording will stop after a long enough pause in your speech. Recording will not start until the keyboard shortcut is pressed again.
- `press_to_toggle` Recording will stop when the keyboard shortcut is pressed again. Recording will not start until the keyboard shortcut is pressed again.
- `hold_to_record` Recording will continue until the keyboard shortcut is released. Recording will not start until the keyboard shortcut is held down again.

You can change the keyboard shortcut (`activation_key`) and recording mode in the [Configuration Options](#configuration-options). While recording and transcribing, a small status window is displayed that shows the current stage of the process (but this can be turned off). Once the transcription is complete, the transcribed text will be automatically written to the active window.

The transcription can either be done locally through the [faster-whisper Python package](https://github.com/SYSTRAN/faster-whisper/) or through a request to [OpenAI's API](https://developers.openai.com/api/docs/guides/speech-to-text). By default, the app will use a local model, but you can change this in the [Configuration Options](#configuration-options). If you choose to use the API, you will need to either provide your OpenAI API key or change the base URL endpoint.

**Fun fact:** Almost the entirety of the initial release of the project was pair-programmed with [ChatGPT-4](https://openai.com/product/gpt-4) and [GitHub Copilot](https://github.com/features/copilot) using VS Code. Practically every line, including most of this README, was written by AI. After the initial prototype was finished, WhisperWriter was used to write a lot of the prompts as well!

## Getting Started

### Supported platforms

- **Linux with X11** (developed on Ubuntu 24.04). Wayland sessions need the
  `evdev` input backend (read access to `/dev/input`) and the `ydotool`/`dotool` output methods.
- **CPython 3.10 – 3.14** (Ubuntu 22.04 through 26.04). Every supported version is covered by the
  single lock file `requirements.txt`, so a distribution upgrade does not need other pins.
- Windows/macOS were supported upstream but are not tested in this fork.

### System packages (Ubuntu/Debian)

A few Python packages have no ready-made Linux build and are compiled during installation
(`pyaudio`, `PyGObject`, `evdev`, and `webrtcvad-wheels` on Python 3.14). They need:

```bash
sudo apt install git build-essential python3-dev python3-venv pkg-config \
    portaudio19-dev libgirepository-2.0-dev libcairo2-dev gobject-introspection \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good
```

`libgirepository-2.0-dev` is available from Ubuntu 24.04; on 22.04 install
`libgirepository1.0-dev` instead. GStreamer plays the start/stop sounds; without it
WhisperWriter works silently and logs one warning.

If you want to run `faster-whisper` on your GPU, you'll also need the NVIDIA libraries below.
The pinned `ctranslate2` 4.x requires **CUDA 12 and cuDNN 9**:

- [cuBLAS for CUDA 12](https://developer.nvidia.com/cublas)
- [cuDNN 9 for CUDA 12](https://developer.nvidia.com/cudnn)

<details>
<summary>More information on GPU execution</summary>

Adapted from the [`faster-whisper` README](https://github.com/SYSTRAN/faster-whisper?tab=readme-ov-file#gpu):

The installed `ctranslate2` supports CUDA 12 with cuDNN 9 only. For CUDA 12 with cuDNN 8,
downgrade with `venv/bin/python3 -m pip install --force-reinstall ctranslate2==4.4.0`; for
CUDA 11 with cuDNN 8, use `ctranslate2==3.24.0`. `update.sh` reinstalls the pinned version on
the next update, so repeat the downgrade afterwards (or keep that computer on the API backend).

On Linux the libraries can be installed with `pip`. Note that `LD_LIBRARY_PATH` must be set before launching Python.

```bash
venv/bin/python3 -m pip install nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'

export LD_LIBRARY_PATH=`venv/bin/python3 -c 'import os; import nvidia.cublas.lib; import nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__) + ":" + os.path.dirname(nvidia.cudnn.lib.__file__))'`
```

Alternatively, use the official NVIDIA CUDA Docker images, or Purfview's
[whisper-standalone-win](https://github.com/Purfview/whisper-standalone-win) library archive.
If the GPU cannot be initialized, WhisperWriter falls back to the CPU.

</details>

### Installation

```bash
git clone https://github.com/spatnynick/whisper-writer.git
cd whisper-writer
python3 -m venv venv
venv/bin/python3 -m pip install -r requirements.txt
venv/bin/python3 -m pip check
./start.sh
```

`start.sh` runs `venv/bin/python3` directly, so it works from a desktop entry or autostart
without activating the venv. Do not move the `venv/` directory after creating it; rebuild it
instead with `./update.sh --rebuild-venv`.

On first run, a Settings window appears. After saving, WhisperWriter restarts into the system
tray and listens for the activation shortcut (`ctrl+shift+space` by default). Press it to start
recording. Open Settings from the tray menu or double-click the tray icon.

Settings live outside the checkout, in `~/.config/whisper-writer/` (`config.yaml`, the API key
in `.env`, and the synchronization settings in `sync.yaml`), so updates and branch switches never
touch them. An old `src/config.yaml` from earlier versions is still read until the next save
writes the new location.

### Updating

Use the tray's **Update** action, or run `./update.sh` in a terminal. Finish dictation first:
the update refuses to start while recording or transcribing. A failed or cancelled recording
waiting for Retry does not block it; the restart after the update discards that recording.
The updater:

- fetches the branch this installation follows from `origin` and fast-forwards to it. A branch
  that was force-pushed on GitHub (for example a test branch restarted from `main`) is followed
  too, but only when this checkout has no commits of its own; local changes or unpublished
  commits make it stop without changing anything;
- installs `requirements.txt` into `venv/` and runs `pip check`, even when the code was already
  current, so an interrupted earlier update is repaired;
- rebuilds `venv/` when its Python interpreter no longer runs (for example after a distribution
  upgrade replaced Python 3.12 with 3.14), keeping the previous one as `venv.previous/` until the
  new one works;
- goes back to the previous version (code and dependencies) when the new dependencies cannot
  be installed, e.g. without network access, so the installation never mixes new code with an
  old environment;
- restarts WhisperWriter. A failed update is reported and does not restart.

WhisperWriter also checks for updates in the background (`update_check_interval_hours`) and
marks the tray icon when one is available; it never installs anything by itself.

```text
./update.sh                  update the followed branch, reconcile the venv, restart
./update.sh --check-only     exit 0 = current, 10 = update available, 11 = branch deleted on GitHub
./update.sh --no-restart     install only (used by the tray, which restarts the app itself)
./update.sh --switch BRANCH  follow another branch of origin (see below)
./update.sh --list-branches  list origin's branches
./update.sh --rebuild-venv   recreate venv/ from scratch
```

### Testing a branch before it is merged

**Settings → About → Application branch** shows the branch this installation follows. Pick
another branch of the fork (use **Refresh list** to load the current branches from GitHub) and
press **Switch and restart**. WhisperWriter downloads that branch, installs its dependencies and
restarts on it; the tray Update action then follows that branch, so new commits pushed to it
arrive like any other update. When you are done testing, switch back to `main` the same way.
This also works as a way back to a working version: switching installs exactly what `main` is
on GitHub, and pip downgrades every package to the version `main` pins (packages only the test
branch needed stay installed, unused). If the switch cannot install the dependencies, it
returns to the branch you were on. Your settings are kept, and invalid values from an older or newer branch fall back to defaults
instead of preventing startup.

If a followed branch is deleted on GitHub (typically after its pull request was merged), the
Update action and the background check say so, and you switch back to `main` in the About tab.
The same is available from a terminal with `./update.sh --switch main`.

### Dependencies

`requirements.in` lists the direct dependencies with compatible version ranges.
`requirements.txt` is the lock generated from it with [uv](https://docs.astral.sh/uv/) by
`tools/lock_requirements.sh`: exact versions of every package, with environment markers where a
Python version needs a different build (for example NumPy or onnxruntime on Python 3.10). The
packages compiled locally (`pyaudio`, `PyGObject`, `evdev`) are listed with ranges so an update
reuses what is already built instead of recompiling it.

To change dependencies, edit `requirements.in`, run `tools/lock_requirements.sh`, install the
result into a fresh venv on each supported Python version and run the tests.

### Tests

```bash
venv/bin/python3 -m unittest discover -s tests -p 'test_*.py'
```

The tests use an offscreen Qt platform, dummy keyboard input, synthetic audio, local HTTP(S)
servers and temporary Git repositories; they never record the microphone or type into other
windows. `tests/x11_checks.py` contains additional checks that need an isolated X11 display.

### Configuration Options

WhisperWriter stores its configuration in `~/.config/whisper-writer/config.yaml`. The Settings
window is organized into Transcription, Recording, Text output, General, Synchronization and
About tabs. Related controls are grouped together; the tabs scroll when the window is small.
Save applies changes, while Discard changes restores the saved values. Escape discards edits and
closes Settings.

Every value is validated against `src/config_schema.yaml` (type, allowed choices, numeric range,
hotkey names, URL form) when it is saved, loaded or received by synchronization. Settings refuses
to save an invalid value and names the field. An invalid value in `config.yaml` (edited by hand
or written by another version) is replaced by its default at startup and reported in the log
instead of preventing startup.

#### Optional Git synchronization

The Synchronization tab can synchronize selected settings through a separate Git repository. The
normal configuration remains one local YAML file; WhisperWriter stores a managed clone under the
user configuration directory (`~/.config/whisper-writer/sync/repository` on Linux or
`%APPDATA%\\WhisperWriter\\sync\\repository` on Windows) and writes only the selected areas to
`whisperwriter-sync.yaml`. Prompt context, provider settings, hotkeys, recording, audio, local
model, text output and interface settings can each be enabled or disabled independently. API keys
and other secrets are never synchronized, and neither are machine-specific values: the microphone
index (`sound_device`) and the local model's `device`/`compute_type` stay on each computer.

Each synchronization merges setting by setting: a value changed on only one computer keeps that
change, so two computers editing different settings do not revert each other. When the same
setting was changed differently on two computers, the remote value is kept and the Synchronization
tab and a tray message name the setting. Remote values are validated before they are applied;
invalid ones are ignored and reported. A synchronized change of the API server URL is applied only
after you confirm it; declining keeps this computer's server and stops synchronizing provider
settings on this computer.

Enter the remote repository URL and use **Test connection and refresh branches** in the Git
authentication section to discover and select a branch. A new setup does not preselect a branch:
the remote's declared default (or a recognized `main`/`master` branch) is selected automatically;
otherwise choose one of the discovered branches yourself. Git is detected automatically on PATH,
with an optional executable-path override.
Git authentication can use the existing Git credential helper/SSH agent, an HTTPS username and
token/password, or an SSH private key. Authentication values remain local to the machine.

All synchronization areas are enabled by default and the default check interval is 15 minutes.
When synchronization is enabled, the connection must be tested successfully before settings can
be saved. Save can push selected changes automatically after the first synchronization has been
completed. A new client always pulls a non-empty remote repository first; only an empty remote may
be bootstrapped by a push. A manual Pull saves the local synchronization preferences first and
never pushes as part of that save. Synchronization is paused during recording and transcription.
Pull applies the selected remote areas and restarts WhisperWriter only after the operation succeeds.
Saving settings that need a restart always restarts, even when the automatic push fails (for
example while offline); the failure stays visible in the Synchronization tab and the next
synchronization publishes the saved values.
Errors are shown in the Synchronization tab and marked on the idle tray icon; no synchronization
error popup or desktop notification is displayed.

Updates and branch switching are described under [Updating](#updating) and
[Testing a branch before it is merged](#testing-a-branch-before-it-is-merged).

#### Model Options
- `use_api`: Toggle to choose whether to use the OpenAI API or a local Whisper model for transcription. (Default: `false`)
- `common`: Options common to both API and local models.
  - `language`: Optional input language in [ISO-639-1 format](https://en.wikipedia.org/wiki/List_of_ISO_639_language_codes). Leave it empty to detect the spoken language automatically; transcription stays in that input language. (Default: `null`)
  - `temperature`: Controls the randomness of the transcription output. Lower values make the output more focused and deterministic. (Default: `0.0`)
  - `initial_prompt`: Optional context used to condition the transcription. An empty setting leaves the prompt unset. For multilingual dictation, leave it empty or use keywords/proper names; custom prose should be written in the recording language. OpenAI documents that the prompt should match the audio language. See the [transcription API reference](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create).

- `api`: Configuration options for the OpenAI API. See the [OpenAI transcription API reference](https://developers.openai.com/api/reference/python/resources/audio/subresources/transcriptions/methods/create) for more information.
  - `base_url`: The base URL for an OpenAI-compatible API (`http://` or `https://`, no credentials in the URL). The Settings window loads the model list from its `/models` endpoint in the background; HTTPS certificates are checked against the system certificate store. (Default: `https://api.openai.com/v1`)
  - `model`: The primary transcription model used by a short activation. Model ids ending in `.en` are English-only, so choose the model that fits your recordings. A held activation switches to the configured secondary model; WhisperWriter never substitutes a model based on detected language. (Default: `whisper-1`)
  - `secondary_model`: Optional secondary model. After the first press, a short activation stops and transcribes; a held activation alternates primary and secondary without stopping capture. The active model is shown in the tray tooltip and status popup. (Default: `null`)
  - `timeout_seconds`: HTTP inactivity timeout in seconds, 1–3600. Failed requests are not retried automatically; a failed recording can be retried from the tray. (Default: `120`)
  - `api_key`: Your API key for the OpenAI API. Required for non-local API usage. (Default: `null`)
    The key is stored in the per-user `.env` file together with the server it was saved for
    (`WHISPER_WRITER_API_KEY_HOST`). It is sent only to that server, only over HTTPS (plain HTTP
    only to this computer, e.g. `http://localhost`), and never to a server URL that arrived by
    synchronization until you save the key for it in Settings. Other servers receive a
    placeholder key, which keyless local servers accept. Existing installations bind the key to
    the currently configured server on first start.

- `local`: Configuration options for the local Whisper model.
  - `model`: The model to use for transcription. The larger models provide better accuracy but are slower. See [available models and languages](https://github.com/openai/whisper?tab=readme-ov-file#available-models-and-languages). (Default: `base`)
  - `device`: The device to run the local Whisper model on. Use `cuda` for NVIDIA GPUs, `cpu` for CPU-only processing, or `auto` to let the system automatically choose the best available device. (Default: `auto`)
  - `compute_type`: The compute type to use for the local Whisper model. [More information on quantization here](https://opennmt.net/CTranslate2/quantization.html). (Default: `default`)
  - `condition_on_previous_text`: Set to `true` to use the previously transcribed text as a prompt for the next transcription request. (Default: `true`)
  - `vad_filter`: Set to `true` to use [a voice activity detection (VAD) filter](https://github.com/snakers4/silero-vad) to remove silence from the recording. (Default: `false`)
  - `model_path`: The path to the local Whisper model. If not specified, the default model will be downloaded. (Default: `null`)

#### Recording Options
- `activation_key`: The keyboard shortcut to activate the recording and transcribing process. Separate keys with a `+`, e.g. `ctrl+alt+d`. Unknown key names are rejected in Settings; a broken value in `config.yaml` falls back to the default shortcut with a warning. (Default: `ctrl+shift+space`)
- `input_backend`: The input backend to use for detecting key presses: `pynput` (X11, no special permissions), `evdev` (reads `/dev/input`, needs membership in the `input` group; also works on Wayland and picks up keyboards connected later) or `auto` (evdev when an input device is readable, otherwise pynput). (Default: `auto`)
- `recording_mode`: The recording mode to use. Options include `continuous` (auto-restart recording after pause in speech until activation key is pressed again), `voice_activity_detection` (stop recording after pause in speech), `press_to_toggle` (stop recording when activation key is pressed again), `hold_to_record` (stop recording when activation key is released). (Default: `continuous`)
- `sound_device`: The numeric index of the sound device to use for recording; empty uses the system default. It is not synchronized between computers. To list devices, run `venv/bin/python3 -c "import pyaudio; p = pyaudio.PyAudio(); [print(i, p.get_device_info_by_index(i)['name']) for i in range(p.get_device_count())]"`. (Default: `null`)
- `sample_rate`: The sample rate in Hz to use for recording. Local transcription requires `16000`; the `continuous` and `voice_activity_detection` modes accept 8000, 16000, 32000 or 48000. (Default: `16000`)
- `silence_duration`: The duration in milliseconds to wait for silence before stopping the recording. (Default: `900`)
- `min_duration`: The minimum duration in milliseconds for a recording to be processed. Recordings shorter than this will be discarded. (Default: `100`)

#### Post-processing Options
- `writing_key_press_delay`: The delay in seconds between each key press when writing the transcribed text. (Default: `0.005`)
- `remove_trailing_period`: Set to `true` to remove the trailing period from the transcribed text. (Default: `false`)
- `add_trailing_space`: Set to `true` to add a space to the end of the transcribed text. (Default: `true`)
- `remove_capitalization`: Set to `true` to convert the transcribed text to lowercase. (Default: `false`)
- `input_method`: The method to use for simulating keyboard input: `pynput` (X11), `ydotool` or `dotool` (Wayland; need the respective tool installed). `dotool` refuses multi-line text; use Copy Last Transcript from the tray for it. (Default: `pynput`)

Glossary corrections from `src/glossary.yaml` are applied after transcription: fixed phrase
replacements match whole words only, and optional fuzzy corrections apply only when one of the
configured context words occurs in the text.

#### Miscellaneous Options
- `print_to_terminal`: Set to `true` to print status messages to the terminal. The transcribed text itself is never printed or logged; use Copy Last Transcript to recover it. (Default: `true`)
- `hide_status_window`: Set to `true` to hide the status window during operation. (Default: `false`)
- `status_window_position`: Where the status popup appears (`bottom_right`, `bottom_center`, `bottom_left`, `top_right`, `top_center`, `top_left`, `center`). (Default: `bottom_right`)
- `show_tray_status_icon`: Change the tray icon while recording and transcribing. (Default: `true`)
- `noise_on_completion`: Set to `true` to play a noise after the transcription has been typed out. (Default: `false`)
- `play_toggle_sounds`: Play a short sound when recording starts and stops. A sound that cannot be played (no audio output, missing GStreamer plugins) is skipped. (Default: `true`)
- `toggle_sound_volume`: Volume of the start/stop sounds, 0–100. (Default: `25`)
- `update_check_interval_hours`: How often to check in the background whether an update is available for the followed branch; `0` or empty disables the check. It never installs anything. (Default: `4`)

Options that are missing from `config.yaml` use their default values; invalid ones are replaced by
their defaults as described above.

## Troubleshooting

- **The hotkey does nothing:** with `input_backend: auto`, check the log for the chosen backend.
  If `evdev` cannot read any input device it is not used; set `pynput` on X11.
- **"Unavailable" next to the model list:** hover it for the reason (certificate, connection,
  timeout). An `HTTP 401/403 (API key not sent ...)` means the key is saved for another server;
  save the key again while this server is selected.
- **Update fails:** the dialog shows the updater's reason. Run `./update.sh` in a terminal for the
  full output; `./update.sh --rebuild-venv` recreates a damaged environment.
- **Logs:** warnings go to the output of the process that started WhisperWriter (the terminal,
  or `~/.cache/whisper-writer/restart.log` when `./update.sh` restarted it from a terminal). `./start.sh --debug` additionally writes a rotating debug
  log to `~/.cache/whisper-writer/debug.log`.

## Known Issues

You can see all reported issues and their current status in our [Issue Tracker](https://github.com/savbell/whisper-writer/issues). If you encounter a problem, please [open a new issue](https://github.com/savbell/whisper-writer/issues/new) with a detailed description and reproduction steps, if possible.

## Roadmap
Below are features I am planning to add in the near future:
- [x] Restructuring configuration options to reduce redundancy
- [x] Update to use the latest version of the OpenAI API
- [ ] Additional post-processing options:
  - [ ] Simple word replacement (e.g. "gonna" -> "going to" or "smiley face" -> "😊")
  - [ ] Using GPT for instructional post-processing
- [x] Updating GUI
- [ ] Creating standalone executable file

Below are features not currently planned:
- [ ] Pipelining audio files

Implemented features can be found in the [CHANGELOG](CHANGELOG.md).

## Contributing

Contributions are welcome! I created this project for my own personal use and didn't expect it to get much attention, so I haven't put much effort into testing or making it easy for others to contribute. If you have ideas or suggestions, feel free to [open a pull request](https://github.com/savbell/whisper-writer/pulls) or [create a new issue](https://github.com/savbell/whisper-writer/issues/new). I'll do my best to review and respond as time allows.

## Credits

- [OpenAI](https://openai.com/) for creating the Whisper model and providing the API. Plus [ChatGPT](https://chat.openai.com/), which was used to write a lot of the initial code for this project.
- [Guillaume Klein](https://github.com/guillaumekln) for creating the [faster-whisper Python package](https://github.com/SYSTRAN/faster-whisper).
- All of our [contributors](https://github.com/savbell/whisper-writer/graphs/contributors)!

## License

This project is licensed under the GNU General Public License. See the [LICENSE](LICENSE) file for details.

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]
### Added
- Settings → About → Application branch: switch this installation to another branch of the
  fork (e.g. to test a pull request before merging) and back to `main`. The tray Update action
  follows the selected branch. `update.sh` gained `--switch BRANCH`, `--list-branches` and
  `--rebuild-venv`.
- `requirements.in` (direct dependencies) and `tools/lock_requirements.sh`, which generates the
  pinned `requirements.txt` lock for Python 3.10–3.14.

### Changed
- Dependencies updated and locked for CPython 3.10–3.14 with ready-made Linux packages: NumPy 2,
  ctranslate2 4.8 / faster-whisper 1.2, PyAV 18 (the former `av==11.0.0` had no Linux build and
  was compiled on every install), openai 3.19, webrtcvad-wheels 2.0.14, PyQt5 5.15.11 with Qt
  5.15.19. About 20 unused packages (PyAutoGUI family, tiktoken, regex, ...) were dropped. Local
  GPU transcription now needs CUDA 12 with cuDNN 9.
- `update.sh` follows a branch that was force-pushed on GitHub when the checkout has no commits
  of its own, reports a branch deleted on GitHub (exit code 11; the tray explains how to switch
  back), rebuilds a venv whose Python interpreter disappeared (e.g. after a distribution
  upgrade), and fetches with an explicit refspec so single-branch clones can switch branches.

### Security
- No known vulnerabilities in the locked dependencies (pip-audit, 2026-09-24): `anyio` advisories
  CVE-2026-63374 / CVE-2026-64847 fixed, and the `setuptools<81` cap (PYSEC-2026-3447) removed
  because `webrtcvad-wheels` no longer needs `pkg_resources`.
- The API key is bound to the server it was saved for and sent only there, over HTTPS or to
  localhost. A synchronized server URL change requires confirmation before recordings go there.
- Transcribed text is no longer printed to the terminal/restart log.

### Fixed
- Model discovery in Settings works with HTTPS servers again. Qt 5's network stack cannot use
  OpenSSL 3 (Ubuntu 22.04+), so the model list is now loaded with Python's standard library in a
  background thread, verified against the system certificate store.
- A start/stop sound that cannot be played (no audio output or GStreamer plugins) no longer
  aborts the application in the middle of a recording.
- Glossary phrase replacements match whole words only ("This is a problem." no longer becomes
  "This iSAProblem.").
- Settings synchronization merges per setting instead of overwriting whole areas, applies
  remote updates without discarding settings edited during a sync, validates remote values,
  recovers from an unpushed commit after the remote moved, and no longer synchronizes the
  microphone index or the local model's device/compute type.
- Settings, config.yaml and synchronized values are validated against the schema (types,
  options, ranges, hotkey names, URLs); invalid loaded values fall back to defaults instead of
  crashing startup. A misspelled or empty hotkey falls back to the default shortcut.
- An empty request timeout or key delay no longer disables the timeout or aborts typing.
- Saving settings restarts the app even if the push-on-save synchronization fails.
- Suspending during recording keeps the audio for Retry; a transcription finishing across
  suspend is kept for Copy Last Transcript instead of being discarded or typed after resume.
- The evdev input backend is only chosen when input devices are readable, picks up keyboards
  connected later, and no longer overrides SIGTERM/SIGINT.

### Added
- Optional Git synchronization for independently selected configuration areas, with automatic
  branch discovery, background push/pull, interval checks, local Git authentication settings and
  idle tray error marking.
- New settings window to configure WhisperWriter.
- New main window to either start the keyboard listener or open the settings window.
- New continuous recording mode ([Issue #40](https://github.com/savbell/whisper-writer/issues/40)).
- New option to play a sound when transcription finishes ([Issue #40](https://github.com/savbell/whisper-writer/issues/40)).
- Optional secondary API model selection; a held activation shortcut alternates between primary and secondary models without ending capture.
- First-key long press selects the secondary model for a new recording; new recordings reset to the primary model and use a 400 ms hold threshold.
- Tray double-click toggles Settings, and the API model selectors plus refresh controls are grouped in a dedicated box.

### Changed
- Increased Settings height for the synchronization controls; moved branch testing into Git
  authentication; enabled all synchronization areas by default; set the default interval to 15
  minutes; and display sync times in the user's local format.
- Persisted discovered branches and local sync preferences before manual Pull without pushing.
  New clients always pull a non-empty remote before any push, and scheduled synchronization stays
  paused while recording or transcribing.
- Empty `initial_prompt` values no longer receive an implicit glossary/default prompt; prompt
  context is now explicitly configured and can be supplied later by settings synchronization.
- Migrated status window from using `tkinter` to `PyQt5`.
- Migrated from using JSON to using YAML to store configuration settings.
- Upgraded to latest versions of `openai` and `faster-whisper`, including support for local API ([Issue #32](https://github.com/savbell/whisper-writer/issues/32)).
- API model selectors preserve the last selected values after model-list refresh and when Settings is reopened; the active model is shown during recording and transcription.
- The initial prompt remains empty unless explicitly configured, with a wide editor and the current OpenAI speech-to-text guide below it.

### Removed
- No longer using `keyboard` package to listen for key presses.

## [1.0.1] - 2024-01-28
### Added
- New message to identify whether Whisper was being called using the API or running locally.
- Additional hold-to-talk ([PR #28](https://github.com/savbell/whisper-writer/pull/28)) and press-to-toggle recording methods ([Issue #21](https://github.com/savbell/whisper-writer/issues/21)).
- New configuration options to:
  - Choose recording method (defaulting to voice activity detection).
  - Choose which sound device and sample rate to use.
  - Hide the status window ([PR #28](https://github.com/savbell/whisper-writer/pull/28)).

### Changed
- Migrated from `whisper` to `faster-whisper` ([Issue #11](https://github.com/savbell/whisper-writer/issues/11)).
- Migrated from `pyautogui` to `pynput` ([PR #10](https://github.com/savbell/whisper-writer/pull/10)).
- Migrated from `webrtcvad` to `webrtcvad-wheels` ([PR #17](https://github.com/savbell/whisper-writer/pull/17)).
- Changed default activation key combo from `ctrl+alt+space` to `ctrl+shift+space`.
- Changed to using a local model rather than the API by default.
- Revamped README.md, including new Roadmap, Contributing, and Credits sections.

### Fixed
- Local model is now only loaded once at start-up, rather than every time the activation key combo was pressed.
- Default configuration now auto-chooses compute type for the local model to avoid warnings.
- Graceful degradation to CPU if CUDA isn't available ([PR #30](https://github.com/savbell/whisper-writer/pull/30)).
- Removed long prefix of spaces in transcription ([PR #19](https://github.com/savbell/whisper-writer/pull/19)).

## [1.0.0] - 2023-05-29
### Added
- Initial release of WhisperWriter.
- Added CHANGELOG.md.
- Added Versioning and Known Issues to README.md.

### Changed
- Updated Whisper Python package; the local model is now compatible with Python 3.11.

[Unreleased]: https://github.com/savbell/whisper-writer/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/savbell/whisper-writer/releases/tag/v1.0.0...v1.0.1
[1.0.0]: https://github.com/savbell/whisper-writer/releases/tag/v1.0.0

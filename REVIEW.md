# Review follow-up — 2026-09-24

A read-only review found five high and seven medium issues; all twelve are fixed on this
branch with regression tests in `tests/test_review_fixes.py` (119 headless tests pass).

| Severity | Issue | Fix |
|---|---|---|
| High | Glossary phrases matched inside/across words: "This is a problem." became "This iSAProblem." | Whole-word matching; replacement text is literal. |
| High | A synchronized `base_url` received `OPENAI_API_KEY` (also over plain HTTP) and all audio. | Key bound to the host it was saved for (`WHISPER_WRITER_API_KEY_HOST` in the user `.env`), sent only over HTTPS or to localhost; model discovery follows the same rule. A remote server-URL change needs confirmation; declining stops provider sync locally. Existing installs bind to their current server on first start. |
| High | Pulled values were written unvalidated; e.g. `activation_key: 123` crashed every computer at startup. | `config_validation.py`: schema types, options, ranges, hotkey names, URLs and sample-rate/mode combinations. Used for pulled values (rejected and reported), config.yaml loading (invalid values fall back to defaults) and Settings save. |
| High | Sync overwrote whole areas, silently reverting another computer's changes. | Per-setting three-way merge (last synced state / this computer / remote). Conflicts keep the remote value and are reported in the Synchronization tab and a tray message. An unpushed commit after the remote moved is rebuilt on the remote instead of failing permanently. |
| High | Machine-specific values synced by default. | `sound_device` and local `device`/`compute_type` removed from synchronization. |
| Medium | Push-on-save failure (offline) skipped the restart, leaving saved settings unapplied. | Save-triggered restarts happen regardless; the error stays visible and the next sync publishes. |
| Medium | A pull replaced the whole config with the worker's snapshot, losing edits made meanwhile. | Results carry per-setting updates with their previous value; edits made during the sync win. |
| Medium | Empty timeout meant "no timeout"; empty/negative key delay aborted typing. | Validation plus runtime defaults (120 s, 5 ms). |
| Medium | Unknown hotkey names were dropped (typo → fires on every Ctrl+Shift; empty → every key release). | Strict parser; invalid shortcut falls back to `ctrl+shift+space` with a warning. |
| Medium | Suspend discarded the recording and any finishing transcription. | Recording is cancelled with audio kept for Retry (even after an overrun); a transcription finishing across suspend is kept for Copy Last Transcript and not typed after resume. |
| Medium | Transcripts were printed to stdout (restart.log / session log). | Only length and timing are printed. |
| Medium | evdev backend: SIGTERM ignored, no devices without `input` group, no hotplug. | Default signal handling; chosen only with readable devices; rescans for new keyboards. |

## Compatibility and dependency plan

**Status 2026-09-24 — steps 1–4 applied** (details in FORK_NOTES.md, "Dependencies, updates
and branch testing"):

1. Done, differently than first planned: instead of one hashed lock per Python version, a
   single universal lock (`requirements.txt`, generated from `requirements.in` by
   `tools/lock_requirements.sh`) with environment markers. The currently deployed `update.sh`
   therefore installs the right pins without new selection logic. Hashes were left out: the
   locally compiled packages are deliberately ranges so existing builds are reused, and pip's
   hash mode requires every line to be pinned and hashed.
2. Done: all pins have Linux wheels except pyaudio/PyGObject/evdev (and webrtcvad-wheels on
   3.14). `setuptools<81` was dropped; `setuptools>=83` upgrades old venvs past the advisory.
   `--only-binary` was not added because it cannot exempt the compiled packages.
   `PyQt5-Qt5==5.15.19` did not fix HTTPS: no Qt 5 wheel works with OpenSSL 3, so model
   discovery moved to Python's `urllib` (the finding below was confirmed and is fixed that way).
3. Done: NumPy 2, ctranslate2 4.8, tokenizers 0.23, pydantic 2.13, openai 3.19. Verified on
   3.10–3.14 with the test suite and an Xvfb/PulseAudio dictation; local-model inference and GPU
   (now CUDA 12 + cuDNN 9) remain unverified.
4. Done: `update.sh` rebuilds a venv whose interpreter no longer runs (`--rebuild-venv` on
   demand), keeping the previous one until the new one works. It still installs after the
   fast-forward rather than staging a complete new environment first.
5. Open: PyQt6 migration and GStreamer-free sound playback.

The original findings and plan follow.


Findings from PyPI wheel metadata (2026-09-24), Linux x86_64:

- `av==11.0.0` has **no Linux wheels**; every install compiles it (needs FFmpeg dev packages
  not listed in FORK_NOTES). `av>=12` ships wheels (12.3: cp38–cp312; 18.x: abi3, Python ≥3.11).
- No cp312 wheels for `regex==2023.5.5`, `tiktoken==0.3.1` (sdist needs a Rust toolchain) or
  `webrtcvad-wheels==2.0.11.post1` (2.0.14 has cp310–cp313 wheels).
- No cp313+ wheels for `numpy<2` (1.26.x), `tokenizers==0.15.0`, `ctranslate2==4.2.1`,
  `pydantic_core==2.18.2`. A distro upgrade to a Python ≥3.13 release breaks both the existing
  venv (its interpreter disappears) and a reinstall.
- About 20 pins are not imported by the code or required by a used package: the PyAutoGUI
  family (MouseInfo, PyGetWindow, PyMsgBox, PyRect, PyScreeze, pyscreenshot, mss,
  EasyProcess, entrypoint2, pytweening, pyperclip), `tiktoken`, `regex`, `ffmpeg-python`,
  `future`, `colorama`, `pyreadline3`, `Jinja2`/`MarkupSafe`, `attrs`, `more-itertools`,
  `Pillow`, and `sympy`/`mpmath`/`coloredlogs`/`humanfriendly` (no longer needed by current
  onnxruntime). Each is extra attack surface and build risk.
- `anyio==4.3.0` has two advisories fixed in 4.14.2 (CVE-2026-63374, CVE-2026-64847; low
  relevance here). `PyGObject` and `onnxruntime` are unpinned. PyQt5/Qt 5.15 is end of life;
  `PyQt5-Qt5==5.15.2` predates OpenSSL 3 support, so HTTPS model discovery may fail on
  Ubuntu 22.04+ (verify; 5.15.19 exists).

Proposed order, each step verified on one machine before the others:

1. **Trim** `requirements.txt` to direct dependencies (PyQt5, python-xlib, audioplayer,
   PyGObject, python-dotenv, faster-whisper, numpy, openai, httpx, pyaudio, pynput, rapidfuzz,
   soundfile, webrtcvad-wheels, PyYAML) and generate a full lock with `pip-compile
   --generate-hashes` per Python version (`requirements-py312.txt`, later `-py313`); install
   with `--require-hashes`. `update.sh` selects the lock matching `venv/bin/python3`.
2. **Wheel-only upgrades** within the current Python: `av>=12`, `webrtcvad-wheels==2.0.14`
   (then retest whether `pkg_resources`/`setuptools<81` is still needed), `anyio>=4.14.2`,
   pin `PyGObject` and `onnxruntime`, try `PyQt5-Qt5==5.15.19`. Add `pip install
   --only-binary=:all:` (except pyaudio/PyGObject) so a missing wheel fails loudly instead of
   compiling.
3. **Python 3.13/3.14 readiness:** `numpy>=2` with `ctranslate2>=4.5`, `tokenizers>=0.20`,
   current `pydantic`/`openai`; run the suite plus a real microphone/API/local-model smoke
   test. Update README (still says Python 3.11) to the supported range.
4. **Venv resilience:** `update.sh` detects a venv whose interpreter is missing or whose
   version differs from the lock, and rebuilds it into `venv.new` before swapping (also the
   staged-install/rollback topic under "Remaining topics" below).
5. **Longer term:** PyQt6 migration (Qt 5 is EOL), and GStreamer-free sound playback to drop
   the PyGObject build dependency.

# Critical-path review — 2026-09-06

Reviewed the application lifecycle, recording/transcription, hotkeys, text injection,
configuration, dependency set and pull-based deployment. Changes are intended for the
Ubuntu/X11 installations described in FORK_NOTES.md. This is a targeted code review and
installed-package audit, not a guarantee that all vulnerabilities or hardware faults are covered.

## End-of-day follow-up

Reviewed all 12 September 6 commits (`93ea665..db56e26`) and their combined behavior.
The original 49 headless tests passed before these additional corrections:

- Preserve complete microphone callback frames still queued when capture stops; close
  the stream even if stopping it raises, and convert a configured microphone index to an integer.
- Freeze model selection atomically when capture ends or is cancelled. Cancel pending
  hold timers on Escape/stop, and accept Escape before the queued recording status arrives.
- Restore the effective API key when discarding Settings edits. Invalid numeric text now
  shows a validation message on Save and can still be discarded. Invalidate model discovery
  replies immediately when the endpoint changes, and cancel discovery when Settings closes.
- Supervise tray updates through completion. Recheck active/recoverable audio after the
  asynchronous Git check, block recording during installation, report install failures,
  and defer Exit/restart until installation finishes. The tray uses `--no-restart`; the
  standalone updater retains its usual restart behavior. This does not add deployment rollback.
- Replace the existing process on a Settings/update restart, releasing its old instance
  lock on exec. Avoid initializing components twice when running with unsaved defaults.
- Reorganize Settings into Transcription, Recording, Text output and General tabs with
  named groups, aligned fields, units, readable choices and scrollable content. Prompt
  context has its own full-width editor; API/local groups follow the selected backend.

Follow-up verification: 67 headless regression tests and 8 isolated X11 checks pass.
The suite includes real Git checks against a temporary local repository for dirty,
ahead and divergent installations, and supervised-update checks without restarting
the actual application. Installed dependency consistency, Python compilation and shell
syntax pass. Offscreen Settings renders were inspected. The running app and its real
configuration were not restarted or modified; microphone/NAS/GPU behavior still needs
normal-use testing. The older verification counts below describe earlier review stages.

The multilingual follow-up also replaces the English prose prompt with glossary keywords and
migrates that earlier built-in prompt at runtime for existing ignored configs. Model selection is
explicit: a short activation uses the configured primary model, while a held activation switches
to the configured secondary model. The app does not substitute models based on detected language,
so an English-only primary remains a deliberate choice for fast English dictation.

## Fixed in this review

| Priority | Finding and effect | Correction |
| --- | --- | --- |
| High | Global keyboard callbacks called application/Qt methods directly from listener threads. | Queue activation, release and cancel signals onto the GUI thread. |
| High | A one-frame shared deque overwrote audio while the consumer was busy and raced with clearing the buffer. | Queue complete PCM frames in order; detect overflow and fail visibly instead of transcribing incomplete audio. Use compact PCM storage instead of a list of individual samples. |
| High | Stop waited synchronously for network/model work, freezing Qt. A silent microphone could leave the capture wait blocked indefinitely. | Stop is a nonblocking request; capture polls every 100 ms. Exit/settings restart retain the worker until it finishes. Native device open/close and model inference remain non-interruptible. |
| High | Continuous restart raced QThread completion; popup programmatic close acted as cancellation; stop before worker startup was overwritten. | Restart continuous mode only from `finished`; hide the popup on programmatic status changes; initialize recording state before starting the worker. |
| High | Debug logs captured global keys and grew without limit. | Remove per-key logging and rotate at 2 MB with two backups. Existing logs are not deleted; terminal transcript printing is still controlled by `print_to_terminal`. |
| High | Launcher replaced a real API key with a placeholder, preventing `.env` from loading it. | Preserve environment keys; load `.env`; honor config fallback; only supply a placeholder for a non-OpenAI endpoint with no key. |
| High | Newlines in dotool text became additional dotool protocol commands. | Reject multiline dotool output before writing anything; preserve the text for Copy Last Transcript. Safe multiline support needs a separate implementation. |
| High | Typing exceptions could leave hotkeys disabled; error/empty results erased the recoverable transcript. | Restore the listener in `finally`, report typing errors, retain the last nonempty transcript. Command failure now raises an ordinary exception instead of terminating the application. |
| High | Installed dependencies had 47 distinct advisory IDs across 12 packages. | Update affected pins and compatible HTTP dependencies. One distinct setuptools advisory remains, discussed below. |
| Medium | Updater exited before repairing dependencies if already at the latest commit. | Always reconcile requirements and run `pip check`, including after a manual pull or failed prior install. Use `python -m pip`; do not upgrade pip implicitly. |
| Medium | Restart logs used a predictable shared `/tmp` file; launcher left an extra waiting Python process. | Put restart logs in the user's private cache directory; replace launcher process with `execv`. |
| Medium | Empty/non-mapping YAML or malformed sections crashed config loading; interrupted saves could truncate config. | Ignore invalid section shapes and save YAML via an atomic replacement. Full scalar validation remains open. |
| Medium | HTTP clients were not explicitly closed; default SDK retries prolonged failures. | Close each client; disable automatic retries; add a 120-second configurable HTTP inactivity timeout. This is not a total request deadline. |
| Medium | Local transcription interpreted non-16-kHz arrays as 16-kHz audio. | Reject incompatible local recording rates before opening the microphone. API WAV files retain the configured sample rate. |
| Medium | There was no in-app way to discover and apply a newer commit. | The tray Update action now checks `origin/<current branch>` asynchronously, shows a dedicated updating icon while checking/applying, keeps the current-state dialog, and starts the existing fast-forward updater silently when a commit is available. |
| Medium | API model selection was single-valued and refresh could move the selection to the first discovered model. | Settings now keeps separate primary/secondary selectors, preserves both selected values across discovery and reopen, and lets a held activation shortcut alternate the model without ending capture. The active model is shown in the tray tooltip and status popup. |

Dependency audit evidence, with duplicate advisory IDs removed, is in
[docs/dependency-audit-2026-09-06.json](docs/dependency-audit-2026-09-06.json).
Advisory counts describe installed versions, not demonstrated exploitability through this app.

## Remaining topics, in priority order

1. **Recording during transcription — planned, not implemented.** The current worker owns
   recording and transcription in one `run()`. Starting another worker would also overlap
   model access, result typing and status events. Typing currently pauses the hotkey listener;
   doing that while another recording is active could lose its stop/release/Escape event.
   A safe implementation should:
   - Separate microphone capture from a single FIFO transcription worker. Keep one microphone
     stream and one model/API job active; bound queued recordings by total audio duration/bytes.
   - Assign job IDs and snapshot settings at submission. Deliver results in recording order;
     expose queued count and make clear which recording Escape cancels.
   - Retain completed results while recording, then insert them when capture stops, or add
     an output mechanism that keeps stop/cancel hotkeys responsive without synthetic feedback.
     Preserve explicit Copy Last Transcript recovery on failures.
   - Give recording status priority over transcription status; coordinate continuous mode,
     queue-full behavior, shutdown, settings restart and suspend/resume in one GUI controller.
   - Test slow/failing transcription, multiple recordings, cancellation, result order,
     hold-to-record release, full queue and shutdown with both workers active.
   This is a moderate architectural change, so it is deliberately separate from these repairs.

2. **Escape suppression — implemented in the follow-up.** On X11, a recording-scoped
   Escape grab now consumes the key and its release, with cleanup on completion and
   suspend/shutdown. Idle and transcription leave Escape alone. Unsupported sessions or
   grab conflicts fall back to unsuppressed cancellation. See FORK_NOTES.md for behavior
   and isolated X11 checks. Recording during transcription remains deferred.

3. **One remaining dependency advisory:** setuptools is capped below 81 because the installed
   `webrtcvad-wheels` imports `pkg_resources`, removed in newer setuptools. The audit reports
   [GHSA-h35f-9h28-mq5c](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c),
   fixed in 83.0.0: Unicode filename normalization can bypass exclusions while building a
   source distribution on macOS. This app runs from a Linux checkout and does not build its
   own source distributions; that reduces relevance here but does not remove the advisory.
   Replace/update the VAD dependency and test all VAD modes before lifting the cap. The raw
   audit lists this same advisory twice; it is one distinct advisory.

4. **Audio recovery — retry implemented; persistence and duration limits remain open.**
   Failed transcriptions retain audio in memory, with a Retry Transcription tray action
   and a distinct persistent error icon. Starting the next recording discards the failed
   audio. Retry preserves the original sample rate; a failed retry retains that audio.
   Audio does not survive application exit, restart or crash; persistent recovery would
   need private storage and a retention policy.
   Add a maximum recording duration and a no-callback deadline for disconnected devices
   that still report active. Individual recording duration is still unbounded.

5. **Deployment consistency and failure recovery.** `update.sh` fast-forwards before installing
   dependencies. A failed install can therefore leave newer code with an incomplete environment;
   rerunning now repairs it even when Git is current. A stronger updater would stage an entire
   environment and switch only after import/smoke checks. `PyGObject`, `onnxruntime`, NumPy and
   transitive dependencies are not fully locked, so machines can still resolve differently.
   Test a fresh install on each machine's Python/OS before generating per-platform lock files.
   Update currently terminates active work: finish dictation first. Restart launch is logged,
   but the updater does not yet verify app readiness or roll back a failed launch.

6. **Settings and output reliability.** Schema scalar types/ranges are not comprehensively
   validated; malformed booleans, timeouts, sample rates and hotkeys need explicit validation.
   Long pynput output still blocks the GUI and pauses listening. Output targets whichever
   app has focus at completion; consider preview/explicit insertion if focus changes matter.
   Error recovery preserves text, but cannot undo partial typing. Fuzzy glossary corrections
   can change legitimate words; evaluate against representative dictation before tuning them.

7. **Future feature: transcribe while recording.** Process a configurable chunk (for example,
   eight seconds) while capture continues, then transcribe the final partial chunk when the
   user stops. This can reduce the wait after stopping, but it needs overlapping audio and
   reconciliation because Whisper may revise words near chunk boundaries. The implementation
   should keep one capture stream and a bounded transcription queue, assign recording/chunk
   IDs, preserve output order, and show whether a result is provisional or final. If a chunk
   takes longer to process than the interval, latency grows instead of improving; the UI needs
   a backlog indicator and a clear policy for queue limits. For API mode, the server must also
   keep up with the selected interval and repeated uploads may increase cost and bandwidth.

8. **Future feature: model warm-up.** The first transcription can be slower because a local
   model may initialize CUDA kernels or JIT paths, while a remote server may load the model or
   compile its inference graph on its first request. Measure cold and warm request timings before
   choosing a fix. For a self-hosted remote service, the strongest solution is server-side model
   preloading plus one real short inference warm-up after startup; a health-check request alone
   may not exercise the model. For local mode, an optional short silent warm-up after model
   creation can move the delay to application startup, at the cost of startup time and GPU/CPU
   resources. Periodic keep-warm requests should be opt-in because they consume resources and
   still cannot prevent a server, proxy or GPU from unloading the model.

## Verification and limits

Run `venv/bin/python -m unittest discover -s tests -v` from the checkout. Tests use dummy
keyboard input, offscreen Qt and synthetic audio; one test uses the real SDK against a
loopback HTTP server. They never record the microphone or type into another application.

The current suite has 45 headless regression tests, including real HTTP failure/retry using
identical WAV audio, retry failures, model-list discovery, model alternation and preservation
of selected models. Seven isolated X11 checks also pass, covering suppression, existing desktop
grabs, the real pynput observer, popup focus, Settings activation and Escape discarding Settings
edits without quitting. Python compilation, shell syntax, `git diff --check`, installed
dependency consistency, and offscreen application startup/shutdown with hardware adapters
mocked were also checked. Real microphone dictation, NAS transcription, GPU inference,
suspend/resume and operation on the other computers still need normal use validation.

The tray-update follow-up adds shell checks for current and newer remote commits, plus GUI
checks for the current, available, failed and worker-active update paths. The updater uses the
existing `origin` remote and current branch; it does not modify or merge a divergent checkout.

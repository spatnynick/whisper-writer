# Critical-path review — 2026-09-06

Reviewed the application lifecycle, recording/transcription, hotkeys, text injection,
configuration, dependency set and pull-based deployment. Changes are intended for the
Ubuntu/X11 installations described in FORK_NOTES.md. This is a targeted code review and
installed-package audit, not a guarantee that all vulnerabilities or hardware faults are covered.

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

All 18 regression tests passed on this machine. Also checked Python compilation, shell syntax, `git diff --check`, installed dependency
consistency, dependency audit before/after, and offscreen full application startup/shutdown
with hardware adapters mocked. The desktop application was restarted after these checks and remained running; its
startup log contained only the known VAD/setuptools deprecation warning. Real microphone dictation, NAS transcription, GPU inference, suspend/resume and
operation on the other computers still need normal use validation.


Follow-up verification: all 27 headless regression tests passed, including real HTTP failure and
retry using identical WAV audio, retry failures and discarding failed audio on the next recording.
Seven isolated X11 checks passed both with bare Xvfb and with KDE KWin, covering suppression,
existing desktop grabs, the real pynput observer, popup focus, Settings activation and Escape discarding Settings edits without quitting.

The tray-update follow-up adds shell checks for current and newer remote commits, plus GUI
checks for the current, available, failed and worker-active update paths. The updater uses the
existing `origin` remote and current branch; it does not modify or merge a divergent checkout.

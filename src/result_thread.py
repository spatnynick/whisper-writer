import logging
import time
import traceback
import numpy as np
import pyaudio
import webrtcvad
from PyQt5.QtCore import QThread, QMutex, pyqtSignal
from queue import Queue, Empty, Full
from threading import Event

from transcription import transcribe
from utils import ConfigManager

logger = logging.getLogger(__name__)


class ResultThread(QThread):
    """
    A thread class for handling audio recording, transcription, and result processing.

    This class manages the entire process of:
    1. Recording audio from the microphone
    2. Detecting speech and silence
    3. Saving the recorded audio as numpy array
    4. Transcribing the audio
    5. Emitting the transcription result

    Signals:
        statusSignal: Emits the current status of the thread (e.g., 'recording', 'transcribing', 'idle')
        resultSignal: Emits the transcription result
    """

    statusSignal = pyqtSignal(str)
    resultSignal = pyqtSignal(str)
    failedAudioSignal = pyqtSignal(object)

    def __init__(self, local_model=None, audio_data=None, sample_rate=None, model_name=None):
        """
        Initialize the ResultThread.

        :param local_model: Local transcription model (if applicable)
        """
        super().__init__()
        self.local_model = local_model
        self.audio_data = audio_data
        self.transcription_succeeded = False
        self.transcription_failed = False
        self.is_recording = audio_data is None
        self.is_running = True
        self.is_cancelled = False
        self.sample_rate = sample_rate
        self.model_name = model_name
        self.mutex = QMutex()

    def stop_recording(self):
        """Stop the current recording session."""
        self.mutex.lock()
        self.is_recording = False
        self.mutex.unlock()

    def cancel_recording(self):
        """Cancel the current recording: stop capturing and discard whatever was recorded."""
        self.mutex.lock()
        self.is_cancelled = True
        self.is_recording = False
        self.mutex.unlock()

    def stop(self):
        """Stop the entire thread execution."""
        self.mutex.lock()
        self.is_running = False
        self.mutex.unlock()
        self.stop_recording()

    def run(self):
        """Main execution method for the thread."""
        audio_data = None
        try:
            if not self.is_running:
                return

            if self.audio_data is None:
                self.statusSignal.emit('recording')
                ConfigManager.console_print('Recording...')
                logger.debug('Recording started')
                audio_data = self._record_audio()
                # Capture is over before transcription starts. Keep this state explicit so
                # the GUI can distinguish a worker still recording from one already transcribing.
                self.stop_recording()
            else:
                audio_data = self.audio_data

            if not self.is_running:
                return

            if self.is_cancelled:
                ConfigManager.console_print('Recording cancelled.')
                logger.debug('Recording cancelled.')
                self.statusSignal.emit('cancel')
                return

            if audio_data is None:
                self.statusSignal.emit('idle')
                return

            self.statusSignal.emit('transcribing')
            ConfigManager.console_print('Transcribing...')
            logger.debug('Transcription started')

            # Time the transcription process
            start_time = time.time()
            transcribe_options = {'sample_rate': self.sample_rate}
            if self.model_name is not None:
                transcribe_options['model_name'] = self.model_name
            result = transcribe(audio_data, self.local_model, **transcribe_options)
            end_time = time.time()

            transcription_time = end_time - start_time
            ConfigManager.console_print(f'Transcription completed in {transcription_time:.2f} seconds. Post-processed line: {result}')
            logger.debug(f'Transcription completed in {transcription_time:.2f}s. Result length: {len(result)} chars')

            if not self.is_running:
                return

            self.statusSignal.emit('idle')
            self.transcription_succeeded = True
            self.resultSignal.emit(result)

        except Exception:
            if audio_data is not None and self.is_running:
                self.transcription_failed = True
                failed_recording = (audio_data, self.sample_rate)
                if self.model_name is not None:
                    failed_recording += (self.model_name,)
                self.failedAudioSignal.emit(failed_recording)
            traceback.print_exc()
            self.statusSignal.emit('error')

        finally:
            self.stop_recording()

    def _record_audio(self):
        """
        Record PCM audio in memory, preserving callback frame order.

        :return: numpy array of audio data, or None if the recording is too short
        """
        recording_options = ConfigManager.get_config_section('recording_options')
        self.sample_rate = recording_options.get('sample_rate') or 16000
        if not ConfigManager.get_config_value('model_options', 'use_api') and self.sample_rate != 16000:
            raise ValueError('Local transcription requires a 16000 Hz recording sample rate.')
        frame_duration_ms = 30  # 30ms frame duration for WebRTC VAD
        frame_size = int(self.sample_rate * (frame_duration_ms / 1000.0))
        silence_duration_ms = recording_options.get('silence_duration') or 900
        silence_frames = int(silence_duration_ms / frame_duration_ms)

        # 150ms delay before starting VAD to avoid mistaking the sound of key pressing for voice
        initial_frames_to_skip = int(0.15 * self.sample_rate / frame_size)

        # Create VAD only for recording modes that use it
        recording_mode = recording_options.get('recording_mode') or 'continuous'
        vad = None
        if recording_mode in ('voice_activity_detection', 'continuous'):
            vad = webrtcvad.Vad(2)  # VAD aggressiveness: 0 to 3, 3 being the most aggressive
            speech_detected = False
            silent_frame_count = 0

        audio_frames = Queue(maxsize=100)  # Three seconds of scheduling slack.
        overflow = Event()
        recording = bytearray()

        def audio_callback(in_data, frame_count, time_info, status):
            if status:
                overflow.set()
            try:
                audio_frames.put_nowait(in_data)
            except Full:
                overflow.set()
            return (None, pyaudio.paContinue)

        sound_device = recording_options.get('sound_device')
        audio = pyaudio.PyAudio()
        try:
            stream = audio.open(format=pyaudio.paInt16, channels=1, rate=self.sample_rate,
                                 input=True, frames_per_buffer=frame_size,
                                 input_device_index=sound_device,
                                 stream_callback=audio_callback)
            logger.debug(f"Audio stream opened: sample_rate={self.sample_rate} device={sound_device} recording_mode={recording_mode}")
            try:
                while self.is_running and self.is_recording:
                    if overflow.is_set():
                        raise RuntimeError('Audio capture overflowed; recording is incomplete. Please retry.')
                    try:
                        frame_bytes = audio_frames.get(timeout=0.1)
                    except Empty:
                        if not stream.is_active():
                            raise RuntimeError('Microphone stream stopped unexpectedly.')
                        continue
                    recording.extend(frame_bytes)

                    # Avoid trying to detect voice in initial frames
                    if initial_frames_to_skip > 0:
                        initial_frames_to_skip -= 1
                        continue

                    if vad:
                        if vad.is_speech(frame_bytes, self.sample_rate):
                            silent_frame_count = 0
                            if not speech_detected:
                                ConfigManager.console_print("Speech detected.")
                                speech_detected = True
                        else:
                            silent_frame_count += 1

                        if speech_detected and silent_frame_count > silence_frames:
                            break
            finally:
                stream.stop_stream()
                stream.close()
                logger.debug("Audio stream closed")
        finally:
            audio.terminate()

        if overflow.is_set():
            raise RuntimeError('Audio capture overflowed; recording is incomplete. Please retry.')
        audio_data = np.frombuffer(recording, dtype=np.int16)
        duration = len(audio_data) / self.sample_rate

        ConfigManager.console_print(f'Recording finished. Size: {audio_data.size} samples, Duration: {duration:.2f} seconds')
        logger.debug(f'Recording finished. Size: {audio_data.size} samples, Duration: {duration:.2f}s')

        min_duration_ms = recording_options.get('min_duration') or 100

        if (duration * 1000) < min_duration_ms:
            ConfigManager.console_print(f'Discarded due to being too short.')
            logger.debug(f'Recording discarded: duration {duration*1000:.0f}ms < min_duration {min_duration_ms}ms')
            return None

        return audio_data

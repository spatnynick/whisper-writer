"""Load the model list of an OpenAI-compatible server without blocking the GUI.

Qt 5's network stack cannot use OpenSSL 3: every PyQt5-Qt5 wheel either fails to load it
or fails the TLS handshake on Ubuntu 22.04 and later, so HTTPS model discovery through
QNetworkAccessManager never worked there. The request is made with Python's standard
library in a background thread instead, which uses the system OpenSSL, the system
certificate store and the usual proxy environment variables.
"""

import threading
import urllib.error
import urllib.request

from PyQt5.QtCore import QObject, pyqtSignal

TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def fetch(url, api_key=None, timeout=TIMEOUT_SECONDS):
    """
    GET ``url`` and return ``{'status': int|None, 'body': bytes, 'error': str|None}``.
    Never raises: failures are reported through ``error`` (and ``status`` for HTTP errors).
    """
    headers = {'User-Agent': 'WhisperWriter', 'Accept': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    request = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                return {'status': response.status, 'body': b'', 'error': 'response too large'}
            return {'status': response.status, 'body': body, 'error': None}
    except urllib.error.HTTPError as error:
        return {'status': error.code, 'body': b'', 'error': f'HTTP {error.code}'}
    except (urllib.error.URLError, OSError, ValueError) as error:
        reason = getattr(error, 'reason', None) or error
        return {'status': None, 'body': b'', 'error': str(reason)}


class ModelDiscovery(QObject):
    """Runs :func:`fetch` in a daemon thread and reports back on the GUI thread."""

    # request_id, result dict from fetch()
    finished = pyqtSignal(int, object)

    def start(self, request_id, url, api_key=None):
        thread = threading.Thread(
            target=self._run, args=(request_id, url, api_key),
            name='model-discovery', daemon=True,
        )
        thread.start()
        return thread

    def _run(self, request_id, url, api_key):
        # Emitting from a plain Python thread queues the signal to this object's (GUI)
        # thread, so slots never run concurrently with the UI.
        result = fetch(url, api_key)
        try:
            self.finished.emit(request_id, result)
        except RuntimeError:
            pass  # Settings was destroyed while the request was running.

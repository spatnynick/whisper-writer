"""Decide when the API key may be sent to the configured transcription server.

The key is bound to the server host it was saved for in Settings. A server URL that
arrives any other way (for example through settings synchronization) does not receive the
key until the user saves it for that server, and the key is never sent over plain HTTP to
another computer. Keyless local servers keep working: they receive a placeholder key.
"""

import logging
import os
from urllib.parse import urlparse

from config_validation import is_loopback_host

logger = logging.getLogger(__name__)

API_KEY_ENV = 'OPENAI_API_KEY'
API_KEY_HOST_ENV = 'WHISPER_WRITER_API_KEY_HOST'
DEFAULT_BASE_URL = 'https://api.openai.com/v1'
DEFAULT_API_HOST = 'api.openai.com'
PLACEHOLDER_KEY = 'not-needed'


def endpoint_host(base_url):
    """Return ``host`` or ``host:port`` (lowercase) for a base URL, or ``''``."""
    try:
        parsed = urlparse((base_url or DEFAULT_BASE_URL).strip())
        host = (parsed.hostname or '').lower()
        port = parsed.port
    except ValueError:
        return ''
    if not host:
        return ''
    return f'{host}:{port}' if port else host


def bound_host():
    """Return the host the saved key belongs to (the OpenAI API unless saved otherwise)."""
    return (os.getenv(API_KEY_HOST_ENV) or DEFAULT_API_HOST).strip().lower()


def transport_allows_key(base_url):
    """Only HTTPS, or plain HTTP to this computer, may carry the key."""
    try:
        parsed = urlparse((base_url or DEFAULT_BASE_URL).strip())
    except ValueError:
        return False
    if parsed.scheme == 'https':
        return bool(parsed.hostname)
    return parsed.scheme == 'http' and is_loopback_host(parsed.hostname)


def may_send_key(base_url, host=None):
    """Return whether the saved key may be sent to ``base_url``."""
    host = bound_host() if host is None else host.strip().lower()
    return transport_allows_key(base_url) and endpoint_host(base_url) == host


def api_key_for(base_url, configured_key=None):
    """
    Return the key to send to ``base_url``: the saved key when it is bound to that host,
    otherwise the placeholder used for keyless OpenAI-compatible servers.
    """
    key = os.getenv(API_KEY_ENV) or configured_key or ''
    if key and may_send_key(base_url):
        return key
    if key:
        logger.warning(
            'Not sending the API key to %s: it is saved for %s. Save the key for this '
            'server in Settings to use it there.', endpoint_host(base_url) or base_url, bound_host())
    return PLACEHOLDER_KEY


def bind_api_key(env_path, base_url):
    """Record that the saved key belongs to the host of ``base_url``."""
    from dotenv import set_key

    host = endpoint_host(base_url)
    set_key(env_path, API_KEY_HOST_ENV, host)
    os.environ[API_KEY_HOST_ENV] = host
    return host


def migrate_api_key_binding(env_path, base_url):
    """
    Bind an existing key to the currently configured server once, so installations from
    before key binding keep working with their existing (e.g. self-hosted) server.
    """
    if os.getenv(API_KEY_HOST_ENV) or not os.getenv(API_KEY_ENV):
        return None
    return bind_api_key(env_path, base_url)

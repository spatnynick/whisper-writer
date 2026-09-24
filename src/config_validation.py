"""Schema-driven validation of individual WhisperWriter settings.

Every value that reaches the running configuration — loaded from config.yaml, entered in
Settings, or pulled from the synchronization repository — is checked here first. An invalid
value is rejected (or replaced by its schema default when loading) instead of crashing the
application at startup or silently changing behavior, e.g. a misspelled hotkey.
"""

import ipaddress
import math
from urllib.parse import urlparse

# Inclusive numeric ranges for settings whose schema type alone is not enough.
NUMERIC_RANGES = {
    ('model_options', 'common', 'temperature'): (0.0, 1.0),
    ('model_options', 'api', 'timeout_seconds'): (1, 3600),
    ('recording_options', 'sample_rate'): (8000, 192000),
    ('recording_options', 'silence_duration'): (0, 60000),
    ('recording_options', 'min_duration'): (0, 60000),
    ('post_processing', 'writing_key_press_delay'): (0.0, 1.0),
    ('misc', 'toggle_sound_volume'): (0, 100),
    ('misc', 'update_check_interval_hours'): (0, 8760),
}

# Settings whose schema default is not null but which have a documented "empty" meaning:
# an empty base URL uses the OpenAI default, an empty update interval disables checks.
NULLABLE_PATHS = {
    ('model_options', 'api', 'base_url'),
    ('misc', 'update_check_interval_hours'),
}

ACTIVATION_KEY_PATH = ('recording_options', 'activation_key')
SOUND_DEVICE_PATH = ('recording_options', 'sound_device')
BASE_URL_PATH = ('model_options', 'api', 'base_url')

# WebRTC VAD only accepts these rates; the other recording modes accept any positive rate.
VAD_SAMPLE_RATES = (8000, 16000, 32000, 48000)


def schema_leaves(schema, prefix=()):
    """Yield ``(path, meta)`` for every setting in the schema."""
    for key, item in (schema or {}).items():
        if not isinstance(item, dict):
            continue
        path = prefix + (key,)
        if 'value' in item:
            yield path, item
        else:
            yield from schema_leaves(item, path)


def schema_meta(schema, path):
    """Return the schema entry for ``path`` or ``None`` when it is unknown."""
    item = schema
    for key in path:
        if not isinstance(item, dict) or key not in item:
            return None
        item = item[key]
    return item if isinstance(item, dict) and 'value' in item else None


def is_loopback_host(host):
    """Return whether ``host`` names this computer."""
    if not host:
        return False
    host = host.strip('[]').lower()
    if host == 'localhost' or host.endswith('.localhost'):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_base_url(value):
    """Return an error for anything but an absolute http(s) URL with a host."""
    try:
        parsed = urlparse(value.strip())
        host = parsed.hostname
    except ValueError:
        return 'enter a valid http:// or https:// URL'
    if parsed.scheme not in ('http', 'https') or not host:
        return 'enter a valid http:// or https:// URL'
    if parsed.username or parsed.password:
        return 'do not put credentials in the URL; use the API key field'
    return None


def _validate_type(value, value_type):
    if value_type == 'bool':
        return None if isinstance(value, bool) else 'expected true or false'
    if value_type == 'int':
        if isinstance(value, bool) or not isinstance(value, int):
            return 'expected a whole number'
        return None
    if value_type == 'float':
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 'expected a number'
        if not math.isfinite(value):
            return 'expected a finite number'
        return None
    if value_type == 'str':
        return None if isinstance(value, str) else 'expected text'
    return None


def validate_value(schema, path, value):
    """
    Return ``None`` when ``value`` is acceptable for ``path``, otherwise a short,
    user-readable reason. Unknown paths are rejected.
    """
    path = tuple(path)
    meta = schema_meta(schema, path)
    if meta is None:
        return 'unknown setting'

    if value is None or (isinstance(value, str) and not value.strip()):
        if meta.get('value') is None or path in NULLABLE_PATHS:
            return None
        return 'a value is required'

    if path == SOUND_DEVICE_PATH and isinstance(value, int) and not isinstance(value, bool):
        # Hand-written YAML often stores the index as a number rather than text.
        return None if value >= 0 else 'the device index cannot be negative'

    error = _validate_type(value, meta.get('type'))
    if error:
        return error

    options = meta.get('options')
    if options and value not in options:
        return 'choose one of: ' + ', '.join(str(option) for option in options)

    if path in NUMERIC_RANGES:
        low, high = NUMERIC_RANGES[path]
        if not low <= value <= high:
            return f'must be between {low} and {high}'

    if path == ACTIVATION_KEY_PATH:
        # Imported lazily: key_listener imports utils, which imports this module.
        from key_listener import parse_key_combination
        try:
            parse_key_combination(value)
        except ValueError as parse_error:
            return str(parse_error)

    if path == SOUND_DEVICE_PATH and not value.strip().isdigit():
        return 'enter the numeric device index, or leave it empty for the system default'

    if path == BASE_URL_PATH:
        return validate_base_url(value)

    return None


def validate_config_combination(config):
    """Return errors for settings that are only invalid together, keyed by path."""
    errors = {}
    recording = config.get('recording_options', {}) if isinstance(config, dict) else {}
    options = config.get('model_options', {}) if isinstance(config, dict) else {}
    sample_rate = recording.get('sample_rate') or 16000
    mode = recording.get('recording_mode') or 'continuous'
    if not options.get('use_api') and sample_rate != 16000:
        errors[('recording_options', 'sample_rate')] = 'local transcription requires 16000 Hz'
    elif mode in ('continuous', 'voice_activity_detection') and sample_rate not in VAD_SAMPLE_RATES:
        errors[('recording_options', 'sample_rate')] = (
            'this recording mode needs one of: ' + ', '.join(str(rate) for rate in VAD_SAMPLE_RATES)
        )
    return errors


def _get(config, path):
    value = config
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _set(config, path, value):
    target = config
    for key in path[:-1]:
        if not isinstance(target.get(key), dict):
            target[key] = {}
        target = target[key]
    target[path[-1]] = value


def sanitize_config(schema, config):
    """
    Replace every invalid value in ``config`` with its schema default, in place.

    Returns a list of ``(path, value, reason)`` for the values that were replaced so the
    caller can report them. Used when loading config.yaml so one bad value (hand edited,
    or written by an older release) cannot stop the application from starting.
    """
    replaced = []
    for path, meta in schema_leaves(schema):
        value = _get(config, path)
        reason = validate_value(schema, path, value)
        if reason:
            replaced.append((path, value, reason))
            _set(config, path, meta.get('value'))
    for path, reason in validate_config_combination(config).items():
        meta = schema_meta(schema, path)
        replaced.append((path, _get(config, path), reason))
        _set(config, path, meta.get('value') if meta else None)
    return replaced

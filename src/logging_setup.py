import logging
import os


def configure_logging(debug: bool):
    """
    Configure application logging.

    If `debug` is True, enable DEBUG-level logging on the root logger with
    two handlers: one to stdout and one to a persistent log file under
    ~/.cache/whisper-writer/debug.log. Writing outside the git repo means
    debug logs can never accidentally end up committed.

    If `debug` is False, leave logging essentially untouched (WARNING level,
    no extra handlers), matching behavior from before this flag existed.
    """
    root_logger = logging.getLogger()

    if not debug:
        root_logger.setLevel(logging.WARNING)
        return

    root_logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter('%(asctime)s %(levelname)-7s %(name)s: %(message)s')

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    log_dir = os.path.expanduser('~/.cache/whisper-writer')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'debug.log')

    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    logging.getLogger(__name__).debug(f"Debug logging enabled. Writing to {log_path}")

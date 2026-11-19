import logging
import sys


logger = logging.getLogger("chisels_play")

if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(area)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _details(details):
    parts = [f"{key}={value}" for key, value in details.items() if value not in (None, "")]
    return (" | " + " ".join(parts)) if parts else ""


def log_event(area, message, **details):
    logger.info(message + _details(details), extra={"area": area})


def log_error(area, message, error=None, **details):
    if error is not None:
        details["error"] = repr(error)[:300]
    logger.error(message + _details(details), extra={"area": area})

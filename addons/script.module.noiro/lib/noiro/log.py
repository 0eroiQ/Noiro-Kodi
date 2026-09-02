import logging
import re


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,]+"),
    re.compile(r"(?i)(authkey\s*[\"']?\s*[:=]\s*[\"'])[^\"']+"),
    re.compile(r"(?i)(api[_-]?key\s*[\"']?\s*[:=]\s*[\"'])[^\"']+"),
    re.compile(r"(?i)(github[_-]?token\s*[\"']?\s*[:=]\s*[\"'])[^\"']+"),
)


def redact(value):
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1<redacted>", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def get_logger(name):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger

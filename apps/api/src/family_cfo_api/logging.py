import logging
import re

SENSITIVE_FIELD_PATTERN = re.compile(
    r"(?i)\b(password|secret|token|credential|authorization|cookie|api[_-]?key)\b"
    r"(\s*[=:]\s*)"
    r"([^,\s]+)"
)


def redact_message(message: str) -> str:
    return SENSITIVE_FIELD_PATTERN.sub(r"\1\2[REDACTED]", message)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_message(record.getMessage())
        record.args = ()
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

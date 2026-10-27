"""Structured (JSON lines) logging. Log messages must never include document text or answers."""

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    _RESERVED: frozenset[str] = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None)))

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in vars(record).items():
            if k not in self._RESERVED and not k.startswith("_"):
                data[k] = v
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("pdfminer", "httpx", "httpcore", "PIL", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

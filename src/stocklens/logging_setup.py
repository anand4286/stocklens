"""Structured JSON logs to stdout — what every log pipeline ingests.

Secrets never reach here: settings hold them as `SecretStr`, and the access
log records the path, never the query string or headers.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update({k: v for k, v in vars(record).items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure(level: str = "INFO", as_json: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if as_json else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn's own access log duplicates ours, without request IDs.
    logging.getLogger("uvicorn.access").disabled = True
    # HTTP clients log every upstream URL, query string included, at INFO.
    # Our own adapters log failures without URLs; keep the clients to warnings.
    for name in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)

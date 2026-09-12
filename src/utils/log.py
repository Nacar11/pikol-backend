import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from src.utils.request_id import request_id_var


class JsonFormatter(logging.Formatter):
    """JSON lines, because Render's log viewer is grep and nothing else.

    Every record carries the current request_id, so one failed booking can be
    traced across every line it produced.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            # Without this, a line grepped out of Render's viewer and pasted
            # into a ticket loses all timing — you cannot tell whether the 409
            # preceded the webhook, or line anything up against PayMongo's
            # timestamps during a payment dispute.
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(debug: bool) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if debug else logging.INFO)

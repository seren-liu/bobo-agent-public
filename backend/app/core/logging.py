from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime

_request_id_var: ContextVar[str | None] = ContextVar("log_request_id", default=None)
_user_id_var: ContextVar[str | None] = ContextVar("log_user_id", default=None)
_method_var: ContextVar[str | None] = ContextVar("log_method", default=None)
_path_var: ContextVar[str | None] = ContextVar("log_path", default=None)
_auth_source_var: ContextVar[str | None] = ContextVar("log_auth_source", default=None)


def set_log_context(**values: str | None) -> dict[str, Token]:
    tokens: dict[str, Token] = {}
    mapping = {
        "request_id": _request_id_var,
        "user_id": _user_id_var,
        "method": _method_var,
        "path": _path_var,
        "auth_source": _auth_source_var,
    }
    for key, value in values.items():
        if key in mapping:
            tokens[key] = mapping[key].set(value)
    return tokens


def reset_log_context(tokens: dict[str, Token]) -> None:
    mapping = {
        "request_id": _request_id_var,
        "user_id": _user_id_var,
        "method": _method_var,
        "path": _path_var,
        "auth_source": _auth_source_var,
    }
    for key, token in reversed(list(tokens.items())):
        variable = mapping.get(key)
        if variable is not None:
            variable.reset(token)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
        }

        request_id = _request_id_var.get()
        user_id = _user_id_var.get()
        method = _method_var.get()
        path = _path_var.get()
        auth_source = _auth_source_var.get()
        if request_id:
            payload["request_id"] = request_id
        if user_id:
            payload["user_id"] = user_id
        if method:
            payload["method"] = method
        if path:
            payload["path"] = path
        if auth_source:
            payload["auth_source"] = auth_source

        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) >= 5:
            client_addr, req_method, req_path, http_version, status_code = record.args[:5]
            payload.update(
                {
                    "event": "http_access",
                    "client_addr": client_addr,
                    "method": req_method,
                    "path": req_path,
                    "http_version": http_version,
                    "status_code": int(status_code),
                }
            )
            payload.setdefault("message", f"{req_method} {req_path}")
        else:
            message = record.getMessage()
            parsed_message: dict[str, object] | None = None
            if message.startswith("{") and message.endswith("}"):
                try:
                    loaded = json.loads(message)
                except json.JSONDecodeError:
                    loaded = None
                if isinstance(loaded, dict):
                    parsed_message = loaded

            if parsed_message is not None:
                payload.update(parsed_message)
            else:
                payload["message"] = message

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_bobo_json_logging_configured", False):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    setattr(root, "_bobo_json_logging_configured", True)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(logging.INFO)

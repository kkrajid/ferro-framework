"""Ferro: a Python application framework with a Rust-native execution engine."""

import re as _re

from ferro._ferro import Request
from ferro._ferro import __version__ as _native_version

# The package version as Python spells it (PEP 440): Cargo's "0.5.0-rc.1" is "0.5.0rc1".
__version__ = _re.sub(
    r"-(?:(a)lpha|(b)eta|(rc))\.?(\d+)$",
    lambda m: f"{m.group(1) or m.group(2) or m.group(3)}{m.group(4)}",
    _native_version,
)

# isort: split
from ferro.app import App
from ferro.exceptions import ConfigurationError, HTTPException, ValidationError
from ferro.response import (
    EventStream,
    JSONResponse,
    Response,
    ServerSentEvent,
    StreamingResponse,
)
from ferro.security import CORS
from ferro.websockets import WebSocket, WebSocketDisconnect

__all__ = [
    "CORS",
    "App",
    "ConfigurationError",
    "EventStream",
    "HTTPException",
    "JSONResponse",
    "Request",
    "Response",
    "ServerSentEvent",
    "StreamingResponse",
    "ValidationError",
    "WebSocket",
    "WebSocketDisconnect",
    "__version__",
]

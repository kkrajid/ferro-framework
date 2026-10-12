"""Exceptions that application code raises or receives."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from http import HTTPStatus

__all__ = ["ConfigurationError", "HTTPException", "ValidationError"]


def _reason(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


class HTTPException(Exception):
    """Raise from a handler to send an error response with the standard error envelope.

    >>> raise HTTPException(404, "Order not found")
    """

    def __init__(
        self,
        status: int,
        message: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if isinstance(status, bool) or not isinstance(status, int) or not 400 <= status <= 599:
            raise ValueError(f"HTTPException status must be an int from 400 to 599, got {status!r}")
        self.status = status
        self.message = message if message is not None else _reason(status)
        self.headers: dict[str, str] = dict(headers or {})
        super().__init__(status, self.message)

    def __str__(self) -> str:
        return f"{self.status} {self.message}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.status!r}, {self.message!r})"


class ValidationError(HTTPException):
    """Request data did not match what the handler declared (status 422).

    ``details`` holds one ``{"param", "location", "problem"}`` dict per problem.
    """

    def __init__(self, details: Iterable[Mapping[str, str]]) -> None:
        super().__init__(422, "Validation failed")
        self.details: list[dict[str, str]] = [dict(detail) for detail in details]


class ConfigurationError(Exception):
    """The application, a route, a setting or the CLI target is invalid."""

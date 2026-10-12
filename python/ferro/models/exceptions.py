"""Errors raised by the ORM."""

from __future__ import annotations

__all__ = [
    "DatabaseError",
    "DatabaseUnavailableError",
    "FieldError",
    "IntegrityError",
    "MultipleObjectsReturned",
    "ObjectDoesNotExist",
    "OperationalError",
    "ProtectedError",
    "RelatedNotLoaded",
    "TransactionError",
]


class FieldError(Exception):
    """An invalid model definition, field, lookup, ordering or value."""


class DatabaseError(Exception):
    """The database reported an error."""


class IntegrityError(DatabaseError):
    """A constraint was violated. ``model`` and ``fields`` name what was violated."""

    def __init__(self, message: str, model: str | None = None, fields: list[str] | None = None):
        super().__init__(message)
        self.model = model
        self.fields: list[str] = list(fields or [])


class ProtectedError(IntegrityError):
    """A delete was refused because other rows still refer to the row (``on_delete=PROTECT``)."""


class RelatedNotLoaded(FieldError):
    """A related object was read without being loaded first (``select_related``,
    ``prefetch_related`` or ``await obj.fetch_related(...)``). Attribute access never queries."""


class OperationalError(DatabaseError):
    """The database could not run the operation (locked, missing table, unreadable file)."""


class DatabaseUnavailableError(OperationalError):
    """The database cannot be reached or cannot take more work right now: no connection, a
    lost connection, a lock wait that expired, or a full queue. Unhandled in a handler it is
    answered with 503 and ``Retry-After``; catch it to degrade instead."""


class TransactionError(Exception):
    """An operation that needs a transaction ran outside ``transaction()`` (for example
    evaluating ``select_for_update()``, whose row locks only last until the transaction ends)."""


class ObjectDoesNotExist(Exception):
    """Base of every ``Model.DoesNotExist``."""


class MultipleObjectsReturned(Exception):
    """Base of every ``Model.MultipleObjectsReturned``."""

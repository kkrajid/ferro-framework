"""Routes Python's ``logging`` into the native log pipeline and adds request ids to records.

Every record created in the process gets ``record.request_id``: the id of the request whose task
(or sync handler thread) created it, else ``None`` (Feature 011). Records of the ``ferro`` logger
always go to the native pipeline; application loggers do too when the application has not
configured logging itself.
"""

from __future__ import annotations

import logging

from ferro import _ferro

__all__ = ["RustHandler", "configure"]


class RustHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _ferro.log(
                record.levelname,
                record.name,
                self.format(record),
                getattr(record, "request_id", None),
            )
        except Exception:
            self.handleError(record)


_PYTHON_LEVELS = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_factory_installed = False


def _install_record_factory() -> None:
    global _factory_installed
    if _factory_installed:
        return
    previous = logging.getLogRecordFactory()
    request_id = _ferro.request_id_context

    def factory(*args, **kwargs) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        record.request_id = request_id.get()
        return record

    logging.setLogRecordFactory(factory)
    _factory_installed = True


def configure(level: str, fmt: str = "text") -> None:
    """Initializes native logging (``fmt``: ``text`` or ``json``) and attaches the forwarding
    handler once; the root logger only when nothing else configured it."""
    _ferro.init_logging(level, fmt)
    _install_record_factory()
    python_level = _PYTHON_LEVELS.get(level, logging.INFO)
    logger = logging.getLogger("ferro")
    logger.setLevel(python_level)
    if not any(isinstance(h, RustHandler) for h in logger.handlers):
        logger.addHandler(RustHandler())
        logger.propagate = False
    root = logging.getLogger()
    if all(isinstance(h, RustHandler) for h in root.handlers):
        if not root.handlers:
            root.addHandler(RustHandler())
        root.setLevel(python_level)

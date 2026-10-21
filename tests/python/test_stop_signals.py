"""A stop request must never be swallowed by code that handles ordinary errors (found in the
Feature 024 verification pass: `ferro run` kept serving when CTRL_BREAK arrived while it was
logging, because logging handlers catch `Exception`)."""

from __future__ import annotations

import logging

import pytest

from ferro import log, reloader, server, supervisor


@pytest.mark.parametrize("module", [server, supervisor, reloader])
def test_stop_requests_are_not_ordinary_errors(module):
    assert issubclass(module._Shutdown, BaseException)
    assert not issubclass(module._Shutdown, Exception)


def test_a_stop_request_during_logging_propagates(monkeypatch):
    def interrupted(*args, **kwargs):
        raise server._Shutdown(21)

    monkeypatch.setattr(log._ferro, "log", interrupted)
    logger = logging.getLogger("ferro.test.stop")
    handler = log.RustHandler()
    logger.addHandler(handler)
    try:
        with pytest.raises(server._Shutdown):
            logger.warning("logging when the stop request arrives")
    finally:
        logger.removeHandler(handler)

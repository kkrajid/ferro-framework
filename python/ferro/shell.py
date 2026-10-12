"""``ferro shell``: Python with top-level ``await``, the app's models and the database."""

from __future__ import annotations

import ast
import asyncio
import code
import inspect
import sys
import traceback
import types
from typing import Any

__all__ = ["namespace", "run_interactive", "run_source"]

FLAGS = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT


def namespace(app: Any) -> dict[str, Any]:
    from ferro import db, models
    from ferro.models.base import registered_model_classes

    scope: dict[str, Any] = {"app": app, "db": db, "models": models, "__name__": "__console__"}
    for cls in registered_model_classes():
        if not getattr(cls, "_auto_created", False):
            scope.setdefault(cls.__name__, cls)
    return scope


def run_source(source: str, scope: dict[str, Any], loop: asyncio.AbstractEventLoop) -> int:
    """Runs a script once (``-c`` or piped input); 1 when it raises."""
    try:
        compiled = compile(source, "<ferro shell>", "exec", flags=FLAGS)
        result = eval(compiled, scope)
        if inspect.iscoroutine(result):
            loop.run_until_complete(result)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 0
    except BaseException:
        traceback.print_exc()
        return 1
    return 0


class _Console(code.InteractiveConsole):
    def __init__(self, scope: dict[str, Any], loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(scope)
        self.compile.compiler.flags |= FLAGS
        self.loop = loop

    def runcode(self, compiled: types.CodeType) -> None:
        function = types.FunctionType(compiled, self.locals)
        try:
            result = function()
            if inspect.iscoroutine(result):
                self.loop.run_until_complete(result)
        except SystemExit:
            raise
        except BaseException:
            self.showtraceback()


def run_interactive(scope: dict[str, Any], loop: asyncio.AbstractEventLoop) -> int:
    names = sorted(name for name, value in scope.items() if isinstance(value, type))
    banner = (
        f"Ferro shell (Python {sys.version.split()[0]}). `await` works at the top level.\n"
        f"In scope: app, db, models{', ' + ', '.join(names) if names else ''}"
    )
    try:
        _Console(scope, loop).interact(banner=banner, exitmsg="")
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 0
    return 0

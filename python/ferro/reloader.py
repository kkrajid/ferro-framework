"""``ferro dev`` auto-reload: a supervisor that restarts the server process on source changes.

Polling ``*.py`` modification times needs no dependencies and meets the 2-second target
(research R9). Restarting the whole process guarantees no stale state survives a reload.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

__all__ = ["run_with_reload"]

POLL_INTERVAL = 0.5
STOP_TIMEOUT = 5.0
IGNORED_DIRS = frozenset(
    {"__pycache__", "node_modules", "target", "build", "dist", "venv", "env", "site-packages"}
)

Snapshot = dict[str, int]


class _Shutdown(Exception):
    pass


def _say(message: str) -> None:
    print(f"ferro: {message}", file=sys.stderr, flush=True)


def scan(directories: list[Path]) -> Snapshot:
    """Modification times of every ``.py`` file under ``directories``."""
    snapshot: Snapshot = {}
    for directory in directories:
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(root, name)
                    try:
                        snapshot[path] = os.stat(path).st_mtime_ns
                    except OSError:
                        continue
    return snapshot


def _changed(before: Snapshot, after: Snapshot) -> list[str]:
    paths = set(before) | set(after)
    return sorted(p for p in paths if before.get(p) != after.get(p))


def _forget_bytecode(paths: list[str]) -> None:
    """Deletes the cached bytecode of changed files: Python validates ``.pyc`` files by size and
    whole-second mtime, so an edit that keeps the size within the same second would otherwise
    load the old code."""
    for path in paths:
        with contextlib.suppress(OSError, ValueError, NotImplementedError):
            os.remove(importlib.util.cache_from_source(path))


def _start(child_argv: list[str]) -> subprocess.Popen[bytes]:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return subprocess.Popen([sys.executable, "-m", "ferro", *child_argv], creationflags=flags)


def _stop(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    if sys.platform == "win32":
        child.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        child.terminate()
    try:
        child.wait(STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def _raise_shutdown(signum: int, frame: object) -> None:
    raise _Shutdown(signum)


def run_with_reload(child_argv: list[str], directories: list[Path]) -> int:
    signal.signal(signal.SIGTERM, _raise_shutdown)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, _raise_shutdown)

    snapshot = scan(directories)
    _say(f"watching {', '.join(str(d) for d in directories)} for changes")
    child = _start(child_argv)
    exit_reported = False
    try:
        while True:
            time.sleep(POLL_INTERVAL)
            current = scan(directories)
            if current != snapshot:
                time.sleep(0.05)  # let editors finish multi-step saves
                current = scan(directories)
                changed = _changed(snapshot, current)
                snapshot = current
                shown = ", ".join(os.path.relpath(p) for p in changed[:3])
                _say(f"change detected in {shown}; reloading")
                _stop(child)
                _forget_bytecode(changed)
                child = _start(child_argv)
                exit_reported = False
            elif child.poll() is not None and not exit_reported:
                _say(f"server exited with code {child.returncode}; waiting for changes")
                exit_reported = True
    except (KeyboardInterrupt, _Shutdown):
        _stop(child)
        return 0

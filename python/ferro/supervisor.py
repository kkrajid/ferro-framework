"""``ferro run --processes N``: one listening socket shared by N worker processes (Feature 010).

The supervisor binds the port, starts the workers, restarts the ones that crash (with a
back-off), stops when workers keep failing right after starting, and shuts every worker down
gracefully on Ctrl+C / SIGTERM. Each worker is an ordinary single-process server with its own
interpreter, engine threads and database pool.
"""

from __future__ import annotations

import _thread
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

__all__ = ["BindError", "adopt_listener", "supervise", "watch_supervisor"]

EXIT_OK = 0
EXIT_FAILURE = 1
QUICK_EXIT = 5.0  # a worker that dies sooner than this after starting failed to start
STARTUP_FAILURES = 3
POLL = 0.2


class BindError(Exception):
    """The listening address could not be bound (no worker was started)."""


class _Shutdown(Exception):
    pass


def _raise_shutdown(signum: int, frame: object) -> None:
    raise _Shutdown(signum)


@dataclass
class _Worker:
    index: int
    process: subprocess.Popen[bytes] | None = None
    started: float = 0.0
    quick_failures: int = 0
    restart_at: float | None = None


def _spawn(sock: socket.socket, argv: list[str], index: int) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "FERRO_WORKER_ID": str(index),
        "FERRO_SUPERVISOR_PID": str(os.getpid()),
    }
    command = [sys.executable, "-m", "ferro", *argv]
    if sys.platform == "win32":
        process = subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        assert process.stdin is not None
        try:
            process.stdin.write(sock.share(process.pid))
            process.stdin.close()
        except OSError:
            pass  # the worker died already; the monitor loop sees it
        return process
    env["FERRO_WORKER_FD"] = str(sock.fileno())
    # Own process group: a terminal Ctrl+C reaches only the supervisor, which then stops
    # every worker once (a second signal would make a worker stop immediately).
    return subprocess.Popen(command, env=env, pass_fds=[sock.fileno()], process_group=0)


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGTERM)
    except OSError:
        pass


def supervise(
    host: str,
    port: int,
    processes: int,
    worker_argv: list[str],
    banner: str,
    grace: float,
    *,
    scheme: str = "http",
) -> int:
    """Serves until interrupted; returns the exit code. Raises :class:`BindError` if the
    address cannot be bound."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        sock = socket.create_server((host, port), family=family, backlog=1024)
    except OSError as exc:
        raise BindError(f"could not bind to {host}:{port}: {exc}") from None
    bound_host, bound_port = sock.getsockname()[:2]
    shown = f"[{bound_host}]" if ":" in bound_host else bound_host
    print(banner.format(url=f"{scheme}://{shown}:{bound_port}"), file=sys.stderr, flush=True)
    if processes > (os.cpu_count() or 1):
        print(
            f"warning: {processes} processes on {os.cpu_count()} CPUs",
            file=sys.stderr,
            flush=True,
        )
    signal.signal(signal.SIGTERM, _raise_shutdown)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, _raise_shutdown)
    workers = [_Worker(index) for index in range(processes)]
    code = EXIT_OK
    try:
        for worker in workers:
            worker.process = _spawn(sock, worker_argv, worker.index)
            worker.started = time.monotonic()
        while True:
            time.sleep(POLL)
            now = time.monotonic()
            for worker in workers:
                if worker.process is None:
                    if worker.restart_at is not None and now >= worker.restart_at:
                        worker.process = _spawn(sock, worker_argv, worker.index)
                        worker.started = now
                        worker.restart_at = None
                    continue
                exit_code = worker.process.poll()
                if exit_code is None:
                    if now - worker.started >= QUICK_EXIT:
                        worker.quick_failures = 0
                    continue
                if now - worker.started < QUICK_EXIT:
                    worker.quick_failures += 1
                if worker.quick_failures >= STARTUP_FAILURES:
                    print(
                        f"error: worker {worker.index} failed to start (exit code {exit_code}); "
                        "see its output above",
                        file=sys.stderr,
                        flush=True,
                    )
                    code = EXIT_FAILURE
                    raise _Shutdown(0)
                delay = min(0.5 * 2**worker.quick_failures, 10.0)
                print(
                    f"warning: worker {worker.index} exited with code {exit_code}; restarting",
                    file=sys.stderr,
                    flush=True,
                )
                worker.process = None
                worker.restart_at = now + delay
    except (KeyboardInterrupt, _Shutdown):
        pass
    finally:
        sock.close()
        _stop_all([w.process for w in workers if w.process is not None], grace)
    return code


def _stop_all(running: list[subprocess.Popen[bytes]], grace: float) -> None:
    """Graceful stop; workers still running after the grace period (or after a second
    Ctrl+C) are killed."""
    for process in running:
        _stop(process)
    deadline = time.monotonic() + grace + 5
    try:
        for process in running:
            try:
                process.wait(max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
    except (KeyboardInterrupt, _Shutdown):
        for process in running:
            if process.poll() is None:
                process.kill()


def adopt_listener() -> int:
    """In a worker: the listening socket handed over by the supervisor, as a raw handle."""
    if sys.platform == "win32":
        data = sys.stdin.buffer.read()
        sock = socket.fromshare(data)
    else:
        sock = socket.socket(fileno=int(os.environ["FERRO_WORKER_FD"]))
    return sock.detach()


def watch_supervisor(grace: float) -> None:
    """Stops this worker (as if interrupted) when the supervisor process goes away; exits
    outright if that graceful stop has not finished ``grace`` + 10 seconds later."""
    pid = int(os.environ.get("FERRO_SUPERVISOR_PID", "0"))
    if not pid:
        return

    def watch() -> None:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            synchronize, infinite = 0x00100000, 0xFFFFFFFF
            handle = kernel32.OpenProcess(synchronize, False, pid)
            if handle:
                kernel32.WaitForSingleObject(handle, infinite)
                kernel32.CloseHandle(handle)
        else:
            while os.getppid() == pid:
                time.sleep(1)
        _thread.interrupt_main()
        time.sleep(grace + 10)
        os._exit(EXIT_FAILURE)  # nobody would ever stop an orphan that hangs

    threading.Thread(target=watch, name="ferro-supervisor-watch", daemon=True).start()

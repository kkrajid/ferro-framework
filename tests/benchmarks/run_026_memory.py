"""Memory of Ferro vs FastAPI (uvicorn) vs Flask (waitress) (Feature 026).

The resident memory (RSS) of each server's process tree: idle after one request, the peak while
each scenario of ``run_025_compare.py`` runs for ``--seconds`` with ``oha``, and after the load.
Needs the same environment as ``run_025_compare.py`` plus ``psutil``:

    python tests/benchmarks/run_026_memory.py [--seconds 10] [--rounds 2]

Writes tests/benchmarks/results/026-memory.md. Only the processes this script started are
stopped.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import platform
import statistics
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import psutil

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "026-memory.md"
MIB = 1024 * 1024

_spec = importlib.util.spec_from_file_location("compare", HERE / "run_025_compare.py")
assert _spec is not None and _spec.loader is not None
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)


def tree_rss(pid: int) -> int:
    """Resident memory of a process and its children, in bytes."""
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return 0
    total = 0
    for process in processes:
        with contextlib.suppress(psutil.NoSuchProcess):
            total += process.memory_info().rss
    return total


def peak_during(pid: int, work: Callable[[], Any]) -> tuple[int, Any]:
    """The highest RSS sampled every 100 ms while ``work`` runs, and its result."""
    peak = 0
    done = threading.Event()

    def sample() -> None:
        nonlocal peak
        while not done.is_set():
            peak = max(peak, tree_rss(pid))
            time.sleep(0.1)

    sampler = threading.Thread(target=sample)
    sampler.start()
    try:
        result = work()
    finally:
        done.set()
        sampler.join()
    return peak, result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--connections", type=int, default=50)
    args = parser.parse_args()
    import ferro._ferro as native

    if native.BUILD_PROFILE != "release":
        print("warning: Ferro is a debug build; results are not representative")
    servers = ["Ferro", "FastAPI (uvicorn)", "Flask (waitress)"]
    measured: dict[str, list[dict[str, Any]]] = {name: [] for name in servers}
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "ferro_app.py").write_text(compare.FERRO)
        (folder / "fastapi_app.py").write_text(compare.FASTAPI)
        (folder / "flask_app.py").write_text(compare.FLASK)
        compare.prepare_database(folder)
        for _ in range(args.rounds):
            for name in servers:
                port = compare.free_port()
                process = compare.start(name, folder, port)
                try:
                    compare.check(port)
                    time.sleep(1)
                    run: dict[str, Any] = {"idle": tree_rss(process.pid), "peak": {}, "rps": {}}
                    for label, method, path, body in compare.SCENARIOS:
                        peak, result = peak_during(
                            process.pid,
                            lambda port=port, method=method, path=path, body=body: compare.run_oha(
                                port, method, path, body, args.seconds, args.connections
                            ),
                        )
                        run["peak"][label] = peak
                        run["rps"][label] = result["rps"]
                    time.sleep(2)
                    run["after"] = tree_rss(process.pid)
                    measured[name].append(run)
                    print(
                        f"{name:18} idle {run['idle'] / MIB:6.1f} MiB  "
                        f"after {run['after'] / MIB:6.1f} MiB",
                        flush=True,
                    )
                finally:
                    compare.stop_tree(process)

    def median(name: str, pick: Callable[[dict[str, Any]], float]) -> float:
        return statistics.median(pick(run) for run in measured[name])

    lines = [
        "# Feature 026: memory of Ferro vs FastAPI vs Flask",
        "",
        f"Measured {datetime.now(UTC):%Y-%m-%d %H:%M} UTC on {platform.platform()} "
        f"({compare.hardware()}), CPython {platform.python_version()}; resident memory (RSS) "
        "of each server's process tree, sampled every 100 ms; oha "
        f"{args.connections} connections, {args.seconds} s per scenario; medians of "
        f"{args.rounds} rounds. Client and servers share the machine.",
        "",
        f"Ferro {version('ferro-framework')} ({native.BUILD_PROFILE} build, "
        "`ferro run --workers 4`, 1 process); "
        f"FastAPI {version('fastapi')} on uvicorn {version('uvicorn')} (1 worker, httptools); "
        f"Flask {version('flask')} on waitress {version('waitress')} (8 threads). Every app "
        "imports Pydantic.",
        "",
        "| Server | Idle MiB | Peak MiB | After load MiB | Growth under load |",
        "|---|---|---|---|---|",
    ]
    for name in servers:
        idle = median(name, lambda run: run["idle"])
        peak = median(name, lambda run: max(run["peak"].values()))
        after = median(name, lambda run: run["after"])
        lines.append(
            f"| {name} | {idle / MIB:.1f} | {peak / MIB:.1f} | {after / MIB:.1f} "
            f"| {(peak - idle) / MIB:+.1f} MiB |"
        )
    lines += [
        "",
        "Peak memory and throughput per scenario:",
        "",
        "| Scenario | Server | Peak MiB | req/s |",
        "|---|---|---|---|",
    ]
    for label, *_ in compare.SCENARIOS:
        for name in servers:
            peak = median(name, lambda run, label=label: run["peak"][label])
            rps = median(name, lambda run, label=label: run["rps"][label])
            lines.append(f"| {label} | {name} | {peak / MIB:.1f} | {rps:,.0f} |")
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Metrics overhead for Feature 011 SC-005: hello world with metrics off and on, alternating.

The verdict uses the server's CPU time per request (user + kernel time of the serving process
for a fixed number of requests): on the development machine throughput varied by about +-40%
between back-to-back runs, which hides a 5% difference, while CPU time per request does not
depend on what else the machine is doing. Throughput is reported too.

Requires `oha` and a release build of the native module. Run from the repo root:

    .venv/Scripts/python tests/benchmarks/run_011.py --rounds 6

Writes tests/benchmarks/results/011-metrics.md.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_010 as bench

sys.path.insert(0, str(bench.ROOT))

from tests.support.process import cpu_seconds

RESULTS = Path(__file__).resolve().parent / "results" / "011-metrics.md"
REQUESTS = 100_000


def run(directory: Path, port: int, metrics: bool) -> tuple[float, float]:
    """(CPU microseconds per request, requests per second) for REQUESTS hello-world requests."""
    hello = next(s for s in bench.SCENARIOS if s.key == "hello_1")
    os.environ["FERRO_METRICS"] = "1" if metrics else "0"
    try:
        server = bench.start(directory, port, hello)
    finally:
        os.environ.pop("FERRO_METRICS", None)
    try:
        url = f"http://127.0.0.1:{port}/"
        pid = httpx.get(f"http://127.0.0.1:{port}/pid").json()["pid"]
        bench.oha(url, "2s", 50, None)
        before = cpu_seconds(pid)
        command = ["oha", "-n", str(REQUESTS), "-c", "100", "--no-tui", "--output-format", "json"]
        result = subprocess.run([*command, url], capture_output=True, text=True, check=True)
        used = cpu_seconds(pid) - before
        report = json.loads(result.stdout)
        if bench.failures(report):
            raise SystemExit(f"{bench.failures(report)} failed requests")
        return used / REQUESTS * 1e6, report["summary"]["requestsPerSec"]
    finally:
        bench.stop_server(server)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=6)
    args = parser.parse_args()
    cpu: dict[str, list[float]] = {"off": [], "on": []}
    rps: dict[str, list[float]] = {"off": [], "on": []}
    port = 9600
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        bench.prepare(directory)
        for number in range(args.rounds):
            for variant in ["off", "on"] if number % 2 == 0 else ["on", "off"]:
                port += 1
                per_request, throughput = run(directory, port, variant == "on")
                cpu[variant].append(per_request)
                rps[variant].append(throughput)
    off, on = statistics.median(cpu["off"]), statistics.median(cpu["on"])
    ratios = [b / a for a, b in zip(cpu["off"], cpu["on"], strict=True)]
    cost = statistics.median(ratios) - 1

    def runs(values: list[float], fmt: str) -> str:
        return ", ".join(format(v, fmt) for v in values)

    lines = [
        "# Feature 011 results: metrics overhead",
        "",
        f"Measured {datetime.now(UTC):%Y-%m-%d %H:%M} UTC on {platform.platform()}, "
        f"{os.cpu_count()} logical CPUs, CPython {platform.python_version()}, release build, "
        f"`ferro run --workers 4`, hello world, {REQUESTS:,} requests per run (oha, 100 "
        f"connections) after a 2 s warm-up, {args.rounds} rounds alternating metrics off/on.",
        "",
        "| Metrics | server CPU per request (median) | runs (us) | req/s (median) | runs |",
        "|---|---|---|---|---|",
        f"| off | {off:.1f} us | {runs(cpu['off'], '.1f')} | "
        f"{statistics.median(rps['off']):,.0f} | {runs(rps['off'], ',.0f')} |",
        f"| on | {on:.1f} us | {runs(cpu['on'], '.1f')} | "
        f"{statistics.median(rps['on']):,.0f} | {runs(rps['on'], ',.0f')} |",
        "",
        f"Per-round CPU ratios on/off: {runs(ratios, '.3f')}; median cost {cost:+.1%} "
        f"(SC-005 target: at most +5%) - {'PASS' if cost <= 0.05 else 'FAIL'}.",
    ]
    text = "\n".join(lines) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

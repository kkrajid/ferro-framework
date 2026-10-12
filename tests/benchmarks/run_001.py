"""Load scenarios for Feature 001 success criteria SC-002, SC-003 and SC-004.

Requires `oha` (``cargo install oha``) and a release build of the native module
(``maturin develop --release --uv``). Run from the repository root:

    .venv/Scripts/python tests/benchmarks/run_001.py --workers 4

Results are printed as Markdown and written to tests/benchmarks/results/001-runtime-core.md.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.support.process import rss_bytes  # noqa: E402

APP_DIR = ROOT / "examples" / "hello_world"
RESULTS = Path(__file__).resolve().parent / "results" / "001-runtime-core.md"


def start_server(workers: int, port: int) -> subprocess.Popen[bytes]:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ferro",
            "run",
            "--workers",
            str(workers),
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        cwd=APP_DIR,
        creationflags=flags,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/", timeout=1).status_code == 200:
                return process
        except httpx.HTTPError:
            time.sleep(0.1)
    process.kill()
    raise RuntimeError("server did not start")


def stop_server(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.terminate()
    process.wait(60)


def oha_command(url: str, duration: str, connections: int) -> list[str]:
    return [
        "oha",
        "-z",
        duration,
        "-c",
        str(connections),
        "--no-tui",
        "--output-format",
        "json",
        url,
    ]


def oha(url: str, duration: str, connections: int) -> dict:
    result = subprocess.run(
        oha_command(url, duration, connections), capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def failures(report: dict) -> int:
    """Non-2xx responses plus client errors, excluding requests oha itself cut off when the
    run's duration ended ("aborted due to deadline")."""
    non_ok = sum(
        count
        for status, count in report.get("statusCodeDistribution", {}).items()
        if not status.startswith("2")
    )
    errors = sum(
        count
        for kind, count in report.get("errorDistribution", {}).items()
        if kind != "aborted due to deadline"
    )
    return non_ok + errors


def server_pid(process: subprocess.Popen[bytes]) -> int:
    """PID of the interpreter actually serving. On Windows a venv's python.exe is a launcher
    that runs the base interpreter as a child process."""
    if sys.platform != "win32":
        return process.pid
    query = (
        f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={process.pid}' | "
        "Select-Object -First 1 -ExpandProperty ProcessId)"
    )
    output = subprocess.run(
        ["powershell", "-NoProfile", "-Command", query], capture_output=True, text=True
    ).stdout.strip()
    return int(output) if output.isdigit() else process.pid


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--quick", action="store_true", help="short runs for a smoke check")
    args = parser.parse_args()
    if shutil.which("oha") is None:
        print("oha not found; install it with `cargo install oha`", file=sys.stderr)
        return 2

    base = f"http://127.0.0.1:{args.port}"
    d002, d003, d004 = ("5s", "5s", "5s") if args.quick else ("30s", "60s", "20s")
    rows: list[tuple[str, str, str, str]] = []

    server = start_server(args.workers, args.port)
    try:
        oha(f"{base}/", "3s", 50)  # warm-up

        sc002 = oha(f"{base}/", d002, 100)
        rps = sc002["summary"]["requestsPerSec"]
        rows.append(
            (
                "SC-002",
                f"hello world, 100 clients, {d002}",
                f"{rps:,.0f} req/s, {failures(sc002)} failed",
                "PASS" if rps >= 10_000 and failures(sc002) == 0 else "FAIL",
            )
        )

        pid = server_pid(server)
        idle = rss_bytes(pid)
        sc003 = oha(f"{base}/", d003, 1000)
        time.sleep(1)
        after = rss_bytes(pid)
        ratio = after / idle
        rows.append(
            (
                "SC-003",
                f"hello world, 1000 clients, {d003}",
                f"{failures(sc003)} failed; memory {idle / 2**20:.1f} -> {after / 2**20:.1f} MiB "
                f"({ratio:.2f}x)",
                "PASS" if failures(sc003) == 0 and ratio <= 2 else "FAIL",
            )
        )

        blocker = subprocess.Popen(
            oha_command(f"{base}/sync-sleep", d004, 20),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        sc004 = oha(f"{base}/", "10s" if not args.quick else "3s", 10)
        blocker.wait(120)
        p99_ms = sc004["latencyPercentiles"]["p99"] * 1000
        rows.append(
            (
                "SC-004",
                "fast route p99 while 20 clients hit a 1 s sync handler",
                f"p99 {p99_ms:.2f} ms, {failures(sc004)} failed",
                "PASS" if p99_ms < 50 and failures(sc004) == 0 else "FAIL",
            )
        )
    finally:
        stop_server(server)

    oha_version = subprocess.run(["oha", "--version"], capture_output=True, text=True).stdout
    lines = [
        "# Feature 001 load results",
        "",
        f"- Date: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Machine: {platform.platform()}, {platform.processor() or platform.machine()}, "
        f"{os.cpu_count()} logical CPUs",
        f"- Python {platform.python_version()}, release build, `ferro run --workers "
        f"{args.workers} --log-level error`, client `{oha_version.strip()}` on the same machine",
        f"- Mode: {'quick smoke' if args.quick else 'full'}",
        "",
        "| Criterion | Scenario | Result | Verdict |",
        "|---|---|---|---|",
        *[f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows],
        "",
    ]
    report = "\n".join(lines)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(report, encoding="utf-8")
    sys.stdout.buffer.write(report.encode("utf-8") + b"\n")
    sys.stdout.flush()
    return 0 if all(row[3] == "PASS" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

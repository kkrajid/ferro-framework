"""Measurements for Feature 006 success criteria SC-002 and SC-004.

Requires `oha`, a release build of the native module (``maturin develop --release --uv``) and
the Rust baseline example (built here with ``cargo build --release``). Run from the repo root:

    .venv/Scripts/python tests/benchmarks/run_006.py

Writes tests/benchmarks/results/006-middleware-security.md.
"""

from __future__ import annotations

import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
RESULTS = Path(__file__).resolve().parent / "results" / "006-middleware-security.md"

from run_001 import failures, oha, stop_server  # noqa: E402

FEATURE_001_RPS = 18_886
FEATURE_001_RATIO = 0.76  # Python path / Rust-only baseline in Feature 001

APP = """
import os

from ferro import App

variant = os.environ.get("BENCH_VARIANT", "plain")
app = App(
    rate_limit="1000000/second" if variant == "limited" else None,
    trusted_proxies=["127.0.0.1"] if variant == "limited" else None,
)

if variant == "middleware":

    @app.middleware
    async def passthrough(request, call_next):
        return await call_next(request)


@app.get("/")
async def home():
    return {"message": "Hello from Rust"}
"""


def start(directory: Path, port: int, variant: str) -> subprocess.Popen[bytes]:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ferro",
            "run",
            "--workers",
            "4",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        cwd=directory,
        env={**os.environ, "BENCH_VARIANT": variant},
        creationflags=flags,
    )
    wait_ready(port)
    return process


def wait_ready(port: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError(f"server on port {port} did not start")


def throughput(port: int, duration: str = "15s") -> tuple[float, int]:
    oha(f"http://127.0.0.1:{port}/", "3s", 50)
    report = oha(f"http://127.0.0.1:{port}/", duration, 100)
    return report["summary"]["requestsPerSec"], failures(report)


def limiter_bench(cargo: str) -> str:
    output = subprocess.run(
        [cargo, "run", "--release", "-q", "-p", "ferro-core", "--example", "limiter_bench"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return dict(part.split("=") for part in output.split())


def main() -> None:
    if shutil.which("oha") is None:
        raise SystemExit("oha not found; install it with `cargo install oha`")
    cargo = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")
    subprocess.run(
        [cargo, "build", "--release", "-p", "ferro-http", "--example", "baseline"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    name = "baseline.exe" if os.name == "nt" else "baseline"
    exe = ROOT / "target" / "release" / "examples" / name
    rounds = 3
    samples: dict[str, list[float]] = {"rust": [], "plain": [], "middleware": [], "limited": []}
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        (directory / "app.py").write_text(APP, encoding="utf-8")
        # Variants alternate so slow phases of the machine hit all of them alike.
        for round_number in range(rounds):
            baseline = subprocess.Popen([str(exe), "8091", "4"], stderr=subprocess.DEVNULL)
            try:
                wait_ready(8091)
                samples["rust"].append(throughput(8091, "10s")[0])
            finally:
                baseline.kill()
            for offset, variant in enumerate(("plain", "middleware", "limited")):
                port = 8092 + round_number * 3 + offset
                server = start(directory, port, variant)
                try:
                    rps, errors = throughput(port, "10s")
                    samples[variant].append(rps)
                    failed += errors
                finally:
                    stop_server(server)
    limiter = limiter_bench(cargo)

    median = {name: statistics.median(values) for name, values in samples.items()}
    ratio = median["plain"] / median["rust"]
    sc002 = ratio >= FEATURE_001_RATIO * 0.95 and failed == 0

    def spread(name: str) -> str:
        return ", ".join(f"{v:,.0f}" for v in samples[name])

    limited_cost = median["limited"] / median["plain"] - 1
    buckets_ok = int(limiter["buckets_later"]) <= int(limiter["clients"])
    sc004 = float(limiter["ns_per_check"]) < 10_000 and buckets_ok and limited_cost > -0.1
    rows = [
        f"| SC-002 | hello world, no middleware, 100 clients, median of {rounds}x10 s | "
        f"{median['plain']:,.0f} req/s ({spread('plain')}); {ratio:.0%} of the Rust-only "
        f"baseline ({median['rust']:,.0f} req/s: {spread('rust')}) vs "
        f"{FEATURE_001_RATIO:.0%} in Feature 001; {failed} failed requests in all runs | "
        f"{'PASS' if sc002 else 'FAIL'} |",
        f"| SC-002 | same with one pass-through middleware | {median['middleware']:,.0f} req/s "
        f"({spread('middleware')}; {median['middleware'] / median['plain'] - 1:+.0%}) | "
        "reported |",
        f"| SC-004 | limiter: {limiter['checks']} checks over {limiter['clients']} client "
        f"addresses | {limiter['ns_per_check']} ns per check; after a burst of 100,000 "
        f"addresses {limiter['buckets_after_burst']} buckets, one hour later "
        f"{limiter['buckets_later']} | {'PASS' if sc004 else 'FAIL'} |",
        f"| SC-004 | hello world with a rate limit on (never exceeded), 100 clients | "
        f"{median['limited']:,.0f} req/s ({spread('limited')}; {limited_cost:+.0%} vs no "
        "limit) | reported |",
    ]
    lines = [
        "# Feature 006 results: middleware & security",
        "",
        f"Measured {datetime.now(UTC):%Y-%m-%d %H:%M} UTC on {platform.platform()}, CPython "
        f"{platform.python_version()}, release build, `ferro run --workers 4`, oha on the same "
        "machine.",
        "",
        "| Criterion | Scenario | Result | Verdict |",
        "|---|---|---|---|",
        *rows,
        "",
        f"Feature 001 measured {FEATURE_001_RPS:,} req/s on an earlier day. Single runs on this",
        "machine vary by about 10%, so variants alternate and medians are compared; SC-002 uses",
        "the share of the Rust-only baseline measured in the same session (the baseline runs",
        "through the same engine adapter, including the new security policy).",
    ]
    text = "\n".join(lines) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

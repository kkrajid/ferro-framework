"""Performance suite for Feature 010 (SC-001, SC-002, SC-003).

Requires `oha`, a release build of the native module (``maturin develop --release --uv``) and
the Rust baseline example (built here with ``cargo build --release``). Run from the repo root
on an otherwise idle machine:

    .venv/Scripts/python tests/benchmarks/run_010.py            # the suite
    .venv/Scripts/python tests/benchmarks/run_010.py --profile  # where hello-world Python time goes

Writes tests/benchmarks/results/010-performance.md (``--profile`` only prints).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
RESULTS = Path(__file__).resolve().parent / "results" / "010-performance.md"

from run_001 import failures, stop_server  # noqa: E402

APP = """
import os

from ferro import App, auth, models

variant = os.environ.get("BENCH_VARIANT", "plain")
app = App(database_url="sqlite:///bench.db")


class Item(models.Model):
    name = models.String(max_length=50)

    class Meta:
        table = "bench_item"


if variant == "middleware":

    @app.middleware
    async def passthrough(request, call_next):
        return await call_next(request)


if variant == "auth":
    app.add_auth()


@app.get("/")
async def home():
    return {"message": "Hello from Rust"}


@app.get("/pid")
async def pid():
    return {"pid": os.getpid()}


@app.get("/items/{item_id}")
async def item(item_id: int):
    return {"id": item_id, "name": f"item {item_id}", "tags": ["a", "b"], "price": 9.5}


@app.get("/db/{item_id}")
async def read(item_id: int):
    found = await Item.objects.get(pk=item_id)
    return {"id": found.pk, "name": found.name}


@app.get("/cpu")
def cpu():
    total = 0
    for i in range(20_000):
        total += i * i
    return {"total": total}


@app.get("/me")
async def me(request):
    return {"user": request.user.username}


@app.post("/login", csrf=False)
async def sign_in(request):
    data = await request.json()
    user = await auth.authenticate(data["username"], data["password"])
    await auth.login(request, user)
    return {"ok": True}
"""


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    path: str
    processes: int = 1
    variant: str = "plain"
    workers: int = 4


SCENARIOS = (
    Scenario("hello_1", "hello world, 1 process", "/"),
    Scenario("hello_4", "hello world, 4 processes", "/", processes=4, workers=2),
    Scenario("json_1", "JSON route with a path parameter", "/items/42"),
    Scenario("db_1", "database read by primary key (SQLite)", "/db/42"),
    Scenario("db_4", "database read, 4 processes", "/db/42", processes=4, workers=2),
    Scenario("cpu_1", "CPU-heavy sync route, 1 process", "/cpu"),
    Scenario("cpu_4", "CPU-heavy sync route, 4 processes", "/cpu", processes=4, workers=2),
    Scenario("middleware_1", "hello world + 1 pass-through middleware", "/", variant="middleware"),
    Scenario("auth_1", "authenticated request (session lookup)", "/me", variant="auth"),
)


def run(command: list[str], directory: Path, **env: str) -> None:
    subprocess.run(
        command, cwd=directory, env={**os.environ, **env}, check=True, capture_output=True
    )


def prepare(directory: Path) -> None:
    (directory / "app.py").write_text(APP, encoding="utf-8")
    ferro = [sys.executable, "-m", "ferro"]
    run([*ferro, "db", "create-tables"], directory)
    seed = "await Item.objects.bulk_create([Item(name=f'item {i}') for i in range(1, 101)])"
    run([*ferro, "shell", "-c", seed], directory)
    run(
        [*ferro, "createsuperuser", "--username", "bench", "--no-input"],
        directory,
        FERRO_SUPERUSER_PASSWORD="bench-password-123",
    )


def wait_ready(url: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError(f"{url} did not answer")


def start(directory: Path, port: int, scenario: Scenario) -> subprocess.Popen[bytes]:
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ferro",
            "run",
            "--workers",
            str(scenario.workers),
            "--processes",
            str(scenario.processes),
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        cwd=directory,
        env={**os.environ, "BENCH_VARIANT": scenario.variant},
        creationflags=flags,
    )
    wait_ready(f"http://127.0.0.1:{port}/")
    if scenario.processes > 1:
        time.sleep(2)  # every worker up, not just the first
    return process


def session_cookie(port: int) -> str:
    response = httpx.post(
        f"http://127.0.0.1:{port}/login",
        json={"username": "bench", "password": "bench-password-123"},
        timeout=30,
    )
    response.raise_for_status()
    return f"sessionid={response.cookies['sessionid']}"


def oha(url: str, duration: str, connections: int, cookie: str | None) -> dict:
    command = ["oha", "-z", duration, "-c", str(connections), "--no-tui"]
    command += ["--output-format", "json"]
    if cookie:
        command += ["-H", f"Cookie: {cookie}"]
    result = subprocess.run([*command, url], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@dataclass
class Sample:
    rps: float
    p50_ms: float
    p99_ms: float
    failed: int


def measure(port: int, path: str, cookie: str | None = None, duration: str = "10s") -> Sample:
    url = f"http://127.0.0.1:{port}{path}"
    oha(url, "3s", 50, cookie)
    report = oha(url, duration, 100, cookie)
    latency = report.get("latencyPercentiles", {})
    return Sample(
        report["summary"]["requestsPerSec"],
        (latency.get("p50") or 0) * 1000,
        (latency.get("p99") or 0) * 1000,
        failures(report),
    )


def suite(rounds: int) -> None:
    if shutil.which("oha") is None:
        raise SystemExit("oha not found; install it with `cargo install oha`")
    cargo = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")
    subprocess.run(
        [cargo, "build", "--release", "-p", "ferro-http", "--example", "baseline"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    exe = (
        ROOT
        / "target"
        / "release"
        / "examples"
        / ("baseline.exe" if os.name == "nt" else "baseline")
    )
    samples: dict[str, list[Sample]] = {"rust": [], **{s.key: [] for s in SCENARIOS}}
    port = 8200
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        prepare(directory)
        # Scenarios alternate within each round so slow phases of the machine hit all alike.
        for _ in range(rounds):
            port += 1
            baseline = subprocess.Popen([str(exe), str(port), "4"], stderr=subprocess.DEVNULL)
            try:
                wait_ready(f"http://127.0.0.1:{port}/")
                samples["rust"].append(measure(port, "/"))
            finally:
                baseline.kill()
            for scenario in SCENARIOS:
                port += 1
                server = start(directory, port, scenario)
                try:
                    cookie = session_cookie(port) if scenario.variant == "auth" else None
                    samples[scenario.key].append(measure(port, scenario.path, cookie))
                finally:
                    stop_server(server)
        killed = kill_during_load(directory, port + 1)
    write_results(samples, rounds, killed)


def kill_during_load(directory: Path, port: int) -> tuple[int, int]:
    """SC-003: hello world on 4 processes under load; one worker is killed 3 s into a 10 s
    run. Returns (requests, failed)."""
    scenario = Scenario("kill", "kill", "/", processes=4, workers=2)
    server = start(directory, port, scenario)
    try:
        victim = httpx.get(f"http://127.0.0.1:{port}/pid", timeout=10).json()["pid"]
        result: dict[str, dict] = {}

        def load() -> None:
            result["report"] = oha(f"http://127.0.0.1:{port}/", "10s", 100, None)

        thread = threading.Thread(target=load)
        thread.start()
        time.sleep(3)
        os.kill(victim, getattr(signal, "SIGKILL", signal.SIGTERM))  # a crash, not a stop
        thread.join()
    finally:
        stop_server(server)
    report = result["report"]
    answered = sum(report.get("statusCodeDistribution", {}).values())
    failed = failures(report)
    return answered + failed - _non_ok(report), failed


def _non_ok(report: dict) -> int:
    return sum(
        count
        for status, count in report.get("statusCodeDistribution", {}).items()
        if not status.startswith("2")
    )


def write_results(samples: dict[str, list[Sample]], rounds: int, killed: tuple[int, int]) -> None:
    def median(key: str, attribute: str = "rps") -> float:
        return statistics.median(getattr(s, attribute) for s in samples[key])

    rust = median("rust")
    failed = sum(s.failed for values in samples.values() for s in values)
    labels = {
        "rust": "Rust-only baseline (engine, no Python)",
        **{s.key: s.label for s in SCENARIOS},
    }
    table = [
        "| Scenario | req/s (median) | runs | p50 ms | p99 ms | failed | vs Rust baseline |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, values in samples.items():
        runs = ", ".join(f"{s.rps:,.0f}" for s in values)
        table.append(
            f"| {labels[key]} | {median(key):,.0f} | {runs} | {median(key, 'p50_ms'):.2f} | "
            f"{median(key, 'p99_ms'):.2f} | {sum(s.failed for s in values)} | "
            f"{median(key) / rust:.0%} |"
        )
    cpu_gain = median("cpu_4") / median("cpu_1")
    hello_gain = median("hello_4") / median("hello_1")
    db_gain = median("db_4") / median("db_1")
    verdicts = [
        "| Criterion | Target | Result | Verdict |",
        "|---|---|---|---|",
        f"| SC-001 | CPU-heavy route, 4 processes >= 2.5x of 1 | {cpu_gain:.2f}x | "
        f"{'PASS' if cpu_gain >= 2.5 else 'FAIL'} |",
        f"| SC-002 | hello world, 4 processes >= 1.3x of 1 | {hello_gain:.2f}x | "
        f"{'PASS' if hello_gain >= 1.3 else 'FAIL'} |",
        f"| SC-003 | 0 failed requests in the suite | {failed} | "
        f"{'PASS' if failed == 0 else 'FAIL'} |",
        f"| SC-003 | a worker killed during a 10 s load run (4 processes, 100 clients): "
        f"< 1% failed | {killed[1]:,} of {killed[0]:,} ({killed[1] / killed[0]:.3%}) | "
        f"{'PASS' if killed[1] < killed[0] / 100 else 'FAIL'} |",
        f"| - | database read, 4 processes vs 1 | {db_gain:.2f}x | reported |",
    ]
    lines = [
        "# Feature 010 results: performance suite & multi-process serving",
        "",
        f"Measured {datetime.now(UTC):%Y-%m-%d %H:%M} UTC on {platform.platform()}, "
        f"{os.cpu_count()} logical CPUs, CPython {platform.python_version()}, release build, "
        f"oha (100 connections, 10 s after a 3 s warm-up) on the same machine, {rounds} rounds "
        "with the scenarios alternating; medians.",
        "",
        "1-process scenarios use `--workers 4`; 4-process scenarios use `--workers 2` per process.",
        "",
        *table,
        "",
        *verdicts,
        "",
        f"Headroom: hello world in one process reached {median('hello_1') / rust:.0%} of the "
        "Rust-only baseline, so SC-002 asks 4 processes for "
        f"{1.3 * median('hello_1') / rust:.0%} of what the engine alone serves with the "
        "client on the same machine. With 4 processes instead of one: CPU-heavy route "
        f"{cpu_gain:.2f}x, database read {db_gain:.2f}x, hello world {hello_gain:.2f}x.",
    ]
    text = "\n".join(lines) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    raw = {key: [vars(s) for s in values] for key, values in samples.items()}
    raw["killed"] = {"requests": killed[0], "failed": killed[1]}
    RESULTS.with_suffix(".json").write_text(json.dumps(raw, indent=1), encoding="utf-8")
    print(text)


def profile(seconds: float, path: str) -> None:
    """cProfile of the Python execution thread while oha drives ``path`` in-process."""
    import cProfile
    import importlib
    import pstats
    import threading

    from ferro.config import resolve
    from ferro.server import ServerRunner

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        prepare(directory)
        os.chdir(directory)
        sys.path.insert(0, str(directory))
        app = importlib.import_module("app").app
        cli = {"port": 0, "workers": 4, "log_level": "error"}
        settings = resolve(cli=cli, app=app._settings, mode="run")
        runner = ServerRunner(app, settings)
        _, port = runner.start()
        profiler = cProfile.Profile()
        loop = runner._loop
        enabled = threading.Event()
        loop.call_soon_threadsafe(lambda: (profiler.enable(), enabled.set()))
        enabled.wait()
        threads = [
            threading.Thread(
                target=oha, args=(f"http://127.0.0.1:{port}{path}", f"{seconds}s", 100, None)
            )
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        done = threading.Event()
        loop.call_soon_threadsafe(lambda: (profiler.disable(), done.set()))
        done.wait()
        runner.stop(graceful=True)
        runner.wait(10)
        runner.close()
        os.chdir(ROOT)
    stats = pstats.Stats(profiler)
    stats.sort_stats("tottime").print_stats(25)
    stats.sort_stats("cumulative").print_stats(25)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--path", default="/")
    args = parser.parse_args()
    if args.profile:
        profile(args.seconds, args.path)
    else:
        suite(args.rounds)


if __name__ == "__main__":
    main()

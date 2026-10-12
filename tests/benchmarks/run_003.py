"""Feature 003 SC-003: 100 concurrent clients on a PostgreSQL-backed list endpoint for 15 s with
zero failed requests and at most `pool_size` server connections.

Starts a temporary PostgreSQL cluster (same helper as the tests). Run from the repo root:

    .venv/Scripts/python tests/benchmarks/run_003.py
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.python.orm.conftest import EXE, _free_port, _postgres_bin  # noqa: E402

EXAMPLE = ROOT / "examples" / "products_api"
RESULTS = Path(__file__).resolve().parent / "results" / "003-postgresql-backend.md"
POOL_SIZE = 10


def main() -> int:
    bin_dir = _postgres_bin()
    if bin_dir is None:
        print("PostgreSQL binaries not found", file=sys.stderr)
        return 2
    root = Path(tempfile.mkdtemp(prefix="ferro-pg-bench-"))
    data, pg_port, http_port = root / "data", _free_port(), _free_port()
    pg_ctl = str(bin_dir / f"pg_ctl{EXE}")
    psql = str(bin_dir / f"psql{EXE}")
    quiet = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    subprocess.run(
        [
            str(bin_dir / f"initdb{EXE}"),
            "-D",
            str(data),
            "-U",
            "ferro",
            "-A",
            "trust",
            "-E",
            "UTF8",
            "--no-locale",
        ],
        check=True,
        **quiet,
    )
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data),
            "-o",
            f"-p {pg_port} -c listen_addresses=127.0.0.1",
            "-l",
            str(root / "log"),
            "-w",
            "start",
        ],
        check=True,
        **quiet,
    )
    admin = f"postgresql://ferro@127.0.0.1:{pg_port}/postgres"
    url = f"{admin}?sslmode=disable&pool_size={POOL_SIZE}&application_name=ferro_bench"
    env = {**os.environ, "FERRO_DATABASE_URL": url}
    server = None
    peak = 0
    try:
        subprocess.run(
            [sys.executable, "-m", "ferro", "migrate"],
            cwd=EXAMPLE,
            env=env,
            check=True,
            capture_output=True,
        )
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ferro",
                "run",
                "--port",
                str(http_port),
                "--workers",
                "4",
                "--log-level",
                "error",
            ],
            cwd=EXAMPLE,
            env=env,
            creationflags=flags,
        )
        base = f"http://127.0.0.1:{http_port}"
        for _ in range(200):
            try:
                httpx.get(f"{base}/products", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        with httpx.Client(base_url=base) as client:
            for i in range(20):
                client.post("/products", json={"name": f"p{i}", "price": f"{i}.50", "stock": i})

        stop = threading.Event()

        def sample() -> None:
            nonlocal peak
            while not stop.is_set():
                out = subprocess.run(
                    [
                        psql,
                        "-d",
                        admin,
                        "-A",
                        "-t",
                        "-c",
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE application_name = 'ferro_bench'",
                    ],
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                if out.isdigit():
                    peak = max(peak, int(out))
                time.sleep(0.5)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        report = json.loads(
            subprocess.run(
                [
                    "oha",
                    "-z",
                    "15s",
                    "-c",
                    "100",
                    "--no-tui",
                    "--output-format",
                    "json",
                    f"{base}/products",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        stop.set()
        sampler.join()
    finally:
        if server is not None:
            server.send_signal(
                signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGTERM
            )
            server.wait(60)
        subprocess.run([pg_ctl, "-D", str(data), "-m", "fast", "-w", "stop"], **quiet)
        shutil.rmtree(root, ignore_errors=True)

    non_ok = sum(c for s, c in report["statusCodeDistribution"].items() if not s.startswith("2"))
    errors = sum(
        c for k, c in report.get("errorDistribution", {}).items() if k != "aborted due to deadline"
    )
    failed = non_ok + errors
    ok = failed == 0 and 0 < peak <= POOL_SIZE
    text = "\n".join(
        [
            "# Feature 003 measurements",
            "",
            f"- Date: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
            f"- Machine: {platform.platform()}, {os.cpu_count()} logical CPUs; "
            f"local PostgreSQL ({bin_dir})",
            "",
            "| Criterion | Scenario | Result | Verdict |",
            "|---|---|---|---|",
            f"| SC-003 | 100 clients on list endpoint, 15 s, pool_size={POOL_SIZE} | "
            f"{failed} failed; "
            f"{report['summary']['requestsPerSec']:,.0f} req/s; peak server connections {peak} | "
            f"{'PASS' if ok else 'FAIL'} |",
            "",
        ]
    )
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(text, encoding="utf-8")
    sys.stdout.buffer.write(text.encode("utf-8") + b"\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

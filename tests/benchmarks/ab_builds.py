"""Before/after comparison of two release builds of the native module, interleaved in one session.

Used for Feature 010's measured optimizations (constitution VIII): single runs on one machine
vary too much to compare builds measured at different times, so this swaps
``python/ferro/_ferro.pyd`` (``_ferro.abi3.so`` elsewhere) between the two builds before each
server start and alternates their order every round. Requires `oha` and the Rust baseline
example. Run from the repo root with no other Ferro process running:

    .venv/Scripts/python tests/benchmarks/ab_builds.py BEFORE.pyd AFTER.pyd --rounds 5

The current build is restored at the end. Prints per-round after/before ratios and medians.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

import run_010 as bench

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--scenarios", default="hello_1,json_1,db_1")
    args = parser.parse_args()
    assert "ferro" not in sys.modules, "this process must not load the module it swaps"
    package = ROOT / "python" / "ferro"
    target = next(
        p for p in package.iterdir() if p.name.startswith("_ferro.") and p.suffix in (".pyd", ".so")
    )
    original = target.read_bytes()
    builds = {"before": args.before, "after": args.after}
    keys = args.scenarios.split(",")
    scenarios = [s for s in bench.SCENARIOS if s.key in keys]
    name = "baseline.exe" if sys.platform == "win32" else "baseline"
    exe = ROOT / "target" / "release" / "examples" / name
    samples: dict[str, list[float]] = {"rust": []}
    for scenario in scenarios:
        for build in builds:
            samples[f"{scenario.key}/{build}"] = []
    port = 9300
    try:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            bench.prepare(directory)
            for number in range(args.rounds):
                order = ["before", "after"] if number % 2 == 0 else ["after", "before"]
                port += 1
                baseline = subprocess.Popen([str(exe), str(port), "4"], stderr=subprocess.DEVNULL)
                try:
                    bench.wait_ready(f"http://127.0.0.1:{port}/")
                    samples["rust"].append(bench.measure(port, "/").rps)
                finally:
                    baseline.kill()
                for scenario in scenarios:
                    for build in order:
                        shutil.copyfile(builds[build], target)
                        port += 1
                        server = bench.start(directory, port, scenario)
                        try:
                            sample = bench.measure(port, scenario.path)
                            if sample.failed:
                                raise SystemExit(f"{sample.failed} failed requests: {scenario}")
                            samples[f"{scenario.key}/{build}"].append(sample.rps)
                        finally:
                            bench.stop_server(server)
    finally:
        target.write_bytes(original)
    rust = samples["rust"]
    print(f"Rust baseline: {[round(v) for v in rust]} req/s")
    for scenario in scenarios:
        before = samples[f"{scenario.key}/before"]
        after = samples[f"{scenario.key}/after"]
        ratios = [a / b for a, b in zip(after, before, strict=True)]
        print(
            f"{scenario.key}: before {statistics.median(before):,.0f} req/s "
            f"({', '.join(f'{v:,.0f}' for v in before)}); after {statistics.median(after):,.0f} "
            f"({', '.join(f'{v:,.0f}' for v in after)}); per-round after/before "
            f"{[round(r, 2) for r in ratios]}, median {statistics.median(ratios):.2f}"
        )


if __name__ == "__main__":
    main()

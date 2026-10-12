# Quickstart & Validation: Performance Suite & Multi-Process Serving

```bash
ferro run --processes 4            # banner: "... (production mode, 4 processes, 8 workers each)"
FERRO_PROCESSES=4 ferro run        # same, from the environment
ferro dev --processes 2            # error: development mode is a single process (exit 2)
```

Stop with Ctrl+C: every worker finishes its in-flight requests, then the command exits 0.

Performance (release build, idle machine, `oha` installed):

```bash
maturin develop --release --uv
python tests/benchmarks/run_010.py             # writes tests/benchmarks/results/010-performance.md
python tests/benchmarks/run_010.py --profile   # Python-thread profile of hello world
```

Automated: `pytest tests/integration/test_processes.py tests/python/test_config.py`.

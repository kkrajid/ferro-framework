# Feature 024: cost of signals in `Model.save()`

`tests/benchmarks/run_024_save_overhead.py`, release wheel (`maturin build --release`), Windows
11, Python 3.13, local databases; 2,000 inserts + 2,000 updates per round, 5 interleaved rounds,
median per save.

| Database | No receiver | One sync `post_save` receiver |
|---|---|---|
| SQLite (file, WAL) | 659.5 µs | 687.7 µs |
| PostgreSQL (local cluster) | 1748.8 µs | 1720.8 µs |

The difference is within the round-to-round spread (SQLite 570–847 µs, PostgreSQL
1680–1861 µs): each autocommitted save is dominated by its commit. Without receivers `save()`
checks one list per signal; a receiver adds one Python call.

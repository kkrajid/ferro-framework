"""Measurements for Feature 005 success criteria SC-002, SC-003 and SC-005.

Requires a release build (``maturin develop --release --uv``). PostgreSQL runs use
FERRO_TEST_POSTGRES_URL or a temporary local cluster (like the test suite). Run from the repo
root:

    .venv/Scripts/python tests/benchmarks/run_005.py

Writes tests/benchmarks/results/005-relations-transactions.md.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
RESULTS = Path(__file__).resolve().parent / "results" / "005-relations-transactions.md"

from tests.python.orm.conftest import EXE, _free_port, _postgres_bin  # noqa: E402

from ferro import db, models  # noqa: E402
from ferro.db import transaction  # noqa: E402
from ferro.models import F  # noqa: E402
from ferro.models.queryset import QuerySet  # noqa: E402

BASELINE_READ_MS = 4.0  # Feature 002 SC-004 result
BASELINE_BULK_S = 0.137  # Feature 002 SC-005 (10,000-row bulk insert), same run as the read


class BenchRow(models.Model):
    name = models.String(max_length=100)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer()
    active = models.Boolean()
    created = models.DateTime()
    note = models.Text()


class BenchCategory(models.Model):
    name = models.String(max_length=50)


class BenchProduct(models.Model):
    name = models.String(max_length=50)
    stock = models.Integer()
    category = models.ForeignKey(BenchCategory, on_delete=models.PROTECT, related_name="products")


class BenchOrder(models.Model):
    number = models.String(max_length=20)


class BenchOrderItem(models.Model):
    order = models.ForeignKey(BenchOrder, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(BenchProduct, on_delete=models.PROTECT)
    quantity = models.Integer()


SHOP = (BenchCategory, BenchProduct, BenchOrder, BenchOrderItem)


async def read_rows() -> tuple[float, float]:
    """Median read time (ms) and the 10,000-row bulk insert time (s), the latter as a measure of
    machine speed for comparing with Feature 002's numbers."""
    await db.create_tables([BenchRow])
    now = datetime.now(UTC)
    rows = [
        BenchRow(
            name=f"row {i}",
            price=Decimal(i) / 100,
            stock=i,
            active=i % 2 == 0,
            created=now,
            note="x" * 40,
        )
        for i in range(10_000)
    ]
    started = time.perf_counter()  # the insert only, as in Feature 002
    await BenchRow.objects.bulk_create(rows)
    bulk_seconds = time.perf_counter() - started
    query = BenchRow.objects.order_by("pk")[:1000]
    await query
    timings = []
    for _ in range(20):
        started = time.perf_counter()
        assert len(await query) == 1000
        timings.append(time.perf_counter() - started)
    return statistics.median(timings) * 1000, bulk_seconds


async def query_counts() -> tuple[int, int, float]:
    await db.create_tables(SHOP)
    categories = [await BenchCategory.objects.create(name=f"c{i}") for i in range(10)]
    await BenchProduct.objects.bulk_create(
        BenchProduct(name=f"p{i}", stock=1, category=categories[i % 10]) for i in range(100)
    )
    calls = 0
    original = QuerySet._run

    async def counting(self, query):
        nonlocal calls
        calls += 1
        return await original(self, query)

    QuerySet._run = counting
    try:
        rows = await BenchProduct.objects.select_related("category")
        assert len(rows) == 100 and all(p.category.name for p in rows)
        joined = calls
        calls = 0
        started = time.perf_counter()
        rows = await BenchProduct.objects.prefetch_related("category")
        prefetch_ms = (time.perf_counter() - started) * 1000
        assert all(p.category.name for p in rows)
        return joined, calls, prefetch_ms
    finally:
        QuerySet._run = original


class OutOfStock(Exception):
    pass


async def orders(transactions: int = 200, initial_stock: int = 300) -> dict:
    await db.create_tables(SHOP)
    category = await BenchCategory.objects.create(name="phones")
    phone = await BenchProduct.objects.create(name="X1", stock=initial_stock, category=category)

    async def place(i: int) -> None:
        quantity = 1 + i % 3
        async with transaction():
            order = await BenchOrder.objects.create(number=f"o{i}")
            updated = await BenchProduct.objects.filter(pk=phone.pk, stock__gte=quantity).update(
                stock=F("stock") - quantity
            )
            if not updated:
                raise OutOfStock()
            await BenchOrderItem.objects.create(order=order, product=phone, quantity=quantity)

    started = time.perf_counter()
    outcomes = await asyncio.wait_for(
        asyncio.gather(*(place(i) for i in range(transactions)), return_exceptions=True), 120
    )
    seconds = time.perf_counter() - started
    unexpected = [o for o in outcomes if o is not None and not isinstance(o, OutOfStock)]
    placed = sum(o is None for o in outcomes)
    stock = (await BenchProduct.objects.get(pk=phone.pk)).stock
    sold = (await BenchOrderItem.objects.aggregate(total=models.Sum("quantity", default=0)))[
        "total"
    ]
    return {
        "placed": placed,
        "out_of_stock": sum(isinstance(o, OutOfStock) for o in outcomes),
        "unexpected": len(unexpected),
        "consistent": stock + sold == initial_stock
        and await BenchOrder.objects.count() == placed
        and await BenchOrder.objects.filter(items__isnull=True).count() == 0
        and stock >= 0,
        "seconds": seconds,
    }


@contextmanager
def postgres() -> Iterator[str | None]:
    if url := os.environ.get("FERRO_TEST_POSTGRES_URL"):
        yield url
        return
    bin_dir = _postgres_bin()
    if bin_dir is None:
        yield None
        return
    root = Path(tempfile.mkdtemp(prefix="ferro-bench-pg-"))
    data = root / "data"
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
        ],
        check=True,
        capture_output=True,
    )
    port = _free_port()
    pg_ctl = str(bin_dir / f"pg_ctl{EXE}")
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data),
            "-o",
            f"-p {port} -c listen_addresses=127.0.0.1",
            "-l",
            str(root / "server.log"),
            "-w",
            "start",
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield f"postgresql://ferro@127.0.0.1:{port}/postgres?sslmode=disable"
    finally:
        subprocess.run(
            [pg_ctl, "-D", str(data), "-m", "fast", "-w", "stop"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(root, ignore_errors=True)


async def run_on(url: str, step) -> object:
    await db.connect(url, pool_size=4)
    try:
        return await step()
    finally:
        await db.disconnect()


def sqlite_url(directory: Path) -> str:
    return f"sqlite:///{(directory / f'bench-{uuid.uuid4().hex[:8]}.db').as_posix()}"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        rounds = [asyncio.run(run_on(sqlite_url(directory), read_rows)) for _ in range(3)]
        read_ms = statistics.median(r[0] for r in rounds)
        bulk_s = statistics.median(r[1] for r in rounds)
        normalized_ms = read_ms * BASELINE_BULK_S / bulk_s
        joined, prefetched, prefetch_ms = asyncio.run(run_on(sqlite_url(directory), query_counts))
        sqlite_orders = asyncio.run(run_on(sqlite_url(directory), orders))
    with postgres() as url:
        pg_orders = asyncio.run(run_on(url, orders)) if url else None

    def verdict(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    def order_row(name: str, result: dict | None) -> str:
        if result is None:
            return f"| SC-003 | 200 concurrent order transactions ({name}) | not run | — |"
        ok = result["consistent"] and result["unexpected"] == 0
        return (
            f"| SC-003 | 200 concurrent order transactions ({name}) | {result['placed']} placed, "
            f"{result['out_of_stock']} out of stock, {result['unexpected']} errors, consistent="
            f"{result['consistent']}, {result['seconds']:.2f} s | {verdict(ok)} |"
        )

    lines = [
        "# Feature 005 results: relations, transactions & aggregation",
        "",
        f"Measured {datetime.now(UTC):%Y-%m-%d %H:%M} UTC on {platform.platform()}, "
        f"CPython {platform.python_version()}, release build.",
        "",
        "| Criterion | Measurement | Result | Verdict |",
        "|---|---|---|---|",
        f"| SC-002 | 100 products with categories via select_related | {joined} query | "
        f"{verdict(joined == 1)} |",
        f"| SC-002 | same via prefetch_related | {prefetched} queries ({prefetch_ms:.1f} ms) | "
        f"{verdict(prefetched == 2)} |",
        order_row("SQLite file, pool 4", sqlite_orders),
        order_row("PostgreSQL, pool 4", pg_orders),
        f"| SC-005 | read 1,000 rows (6 fields), median of 20 (3 rounds); Feature 002: "
        f"{BASELINE_READ_MS} ms | {read_ms:.1f} ms raw; {normalized_ms:.1f} ms scaled to "
        f"Feature 002's machine speed (bulk insert {bulk_s:.3f} s now vs {BASELINE_BULK_S} s) | "
        f"{verdict(normalized_ms <= BASELINE_READ_MS * 1.1)} |",
        "",
        "Stock starts at 300 and the 200 orders ask for 400 units, so some orders are refused by",
        "the `stock__gte` guard; consistency means remaining stock + sold units = 300, one order",
        "per placed transaction and no order without items.",
        "",
        "SC-005 compares against a number measured on an earlier day; the unchanged 10,000-row",
        "bulk insert is timed in the same run to scale for the machine's current speed.",
    ]
    RESULTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

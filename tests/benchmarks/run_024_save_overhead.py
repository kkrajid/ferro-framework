"""Feature 024: the cost of signals in Model.save() (run with a release build).

    python tests/benchmarks/run_024_save_overhead.py [postgresql://...]

Saves N new rows and updates them, without receivers, then with one sync `post_save` receiver,
interleaved over several rounds; prints the median per-save time of each.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import tempfile
import time

from ferro import _ferro, db, models
from ferro.signals import post_save

N = 2000
ROUNDS = 5


class BenchItem(models.Model):
    name = models.String(max_length=40)
    count = models.Integer(default=0)

    class Meta:
        table = "bench024_item"


def receiver(sender, instance, created, **kwargs):
    instance.seen = created


async def one_round() -> float:
    await BenchItem.objects.all().delete()
    started = time.perf_counter()
    items = []
    for i in range(N):
        items.append(await BenchItem.objects.create(name=f"i{i}"))
    for item in items:
        item.count += 1
        await item.save()
    return (time.perf_counter() - started) / (2 * N) * 1e6  # microseconds per save


async def main(url: str) -> None:
    await db.connect(url)
    try:
        await db.create_tables([BenchItem])
        plain, signalled = [], []
        for _ in range(ROUNDS):
            plain.append(await one_round())
            post_save.connect(receiver, sender=BenchItem, weak=False)
            signalled.append(await one_round())
            post_save.disconnect(receiver, sender=BenchItem)
        print(f"build={_ferro.BUILD_PROFILE} database={url.split(':')[0]} saves/round={2 * N}")
        print(f"no receiver:   median {statistics.median(plain):8.1f} µs/save  {plain}")
        print(f"one receiver:  median {statistics.median(signalled):8.1f} µs/save  {signalled}")
    finally:
        await db.disconnect()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        asyncio.run(main(sys.argv[1]))
    else:
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(main(f"sqlite:///{folder.replace(chr(92), '/')}/bench.db"))

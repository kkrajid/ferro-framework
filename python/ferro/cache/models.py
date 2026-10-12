"""The table behind the database cache backend (``ferro_cache``)."""

from __future__ import annotations

from ferro import models

__all__ = ["CacheEntry"]


class CacheEntry(models.Model):
    """One cached value: JSON text and its expiry (seconds since the epoch; NULL never
    expires)."""

    key = models.String(max_length=250, primary_key=True)
    value = models.Text()
    expires = models.Float(null=True, index=True)

    class Meta:
        table = "ferro_cache"

# app/cache.py
"""Caché en memoria con expiración TTL y protección asíncrona."""

import time
from dataclasses import dataclass
from typing import Generic, TypeVar

from asyncio import Lock

T = TypeVar("T")


@dataclass(slots=True)
class _Entry(Generic[T]):
    value: T
    expires_at: float


class MemoryCache(Generic[T]):
    """Caché simple por proceso; no debe usarse como almacenamiento distribuido."""

    def __init__(self, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds debe ser mayor que cero")
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, _Entry[T]] = {}
        self._lock = Lock()

    async def get(self, key: str) -> T | None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if time.monotonic() >= entry.expires_at:
                del self._entries[key]
                return None
            return entry.value

    async def set(self, key: str, value: T) -> None:
        async with self._lock:
            self._entries[key] = _Entry(value=value, expires_at=time.monotonic() + self._ttl_seconds)


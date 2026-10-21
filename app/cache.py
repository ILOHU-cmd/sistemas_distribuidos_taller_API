# app/cache.py
"""Caché en memoria con expiración TTL y protección asíncrona."""

import time
import asyncio
from asyncio import Lock
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar, cast

from app.config import TTL_CACHE

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


@dataclass(slots=True)
class _CachedValue(Generic[T]):
    value: T


_cache_compartida: MemoryCache[object] = MemoryCache(TTL_CACHE)
_lock_single_flight = asyncio.Lock()
_tareas_en_vuelo: dict[tuple[int, str], asyncio.Task[_CachedValue[object]]] = {}


async def _calcular_y_guardar(
    cache: MemoryCache[object],
    clave: str,
    fabrica: Callable[[], Awaitable[T]],
) -> _CachedValue[T]:
    tarea = asyncio.current_task()
    llave_tarea = (id(cache), clave)
    try:
        valor = await fabrica()
        resultado = _CachedValue(valor)
        await cache.set(clave, resultado)
        return resultado
    finally:
        async with _lock_single_flight:
            if _tareas_en_vuelo.get(llave_tarea) is tarea:
                del _tareas_en_vuelo[llave_tarea]


async def obtener_o_calcular(
    clave: str,
    fabrica: Callable[[], Awaitable[T]],
    *,
    cache: MemoryCache[object] | None = None,
) -> T:
    """Devuelve el valor en caché o calcula una sola vez por clave concurrente."""

    cache_usada = cache if cache is not None else _cache_compartida
    valor_guardado = await cache_usada.get(clave)
    if isinstance(valor_guardado, _CachedValue):
        return cast(T, valor_guardado.value)

    llave_tarea = (id(cache_usada), clave)
    async with _lock_single_flight:
        valor_guardado = await cache_usada.get(clave)
        if isinstance(valor_guardado, _CachedValue):
            return cast(T, valor_guardado.value)

        tarea = _tareas_en_vuelo.get(llave_tarea)
        if tarea is None:
            tarea = asyncio.create_task(
                _calcular_y_guardar(cache_usada, clave, fabrica)
            )
            _tareas_en_vuelo[llave_tarea] = tarea

    resultado = await asyncio.shield(tarea)
    return cast(T, resultado.value)

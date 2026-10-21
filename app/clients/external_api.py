# app/clients/external_api.py
"""Cliente de la API pública de terremotos de USGS.

Encadena DOS llamadas:

1. El feed ``all_hour`` (``UsgsFeed``) para encontrar el evento más reciente.
2. El endpoint ``detail`` de ese evento (``UsgsFeature``) para traer su detalle.

El resultado se guarda en una caché en memoria con TTL (``app.cache.MemoryCache``)
para no repetir las llamadas dentro del periodo de vigencia. Toda respuesta que
no cumpla los modelos Pydantic se rechaza con ``RespuestaInvalida``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from app.cache import MemoryCache
from app.clients import (
    ErrorDependencia,
    ExternalApiError,
    NoEncontrado,
    RespuestaInvalida,
    TimeoutDependencia,
)
from app.models import UsgsFeature, UsgsFeed

TIMEOUT_CONEXION = 2.0
TIMEOUT_LECTURA = 5.0  # USGS sale a internet: damos algo más de margen
TTL_CACHE = 300


def _tiempo_timeout(timeout_seconds: float) -> httpx.Timeout:
    """Timeout explícito con conexión acotada y lectura configurable."""

    return httpx.Timeout(
        connect=min(TIMEOUT_CONEXION, timeout_seconds),
        read=timeout_seconds,
        write=timeout_seconds,
        pool=timeout_seconds,
    )


@dataclass(frozen=True, slots=True)
class ExternalResult:
    """Resultado de la consulta encadenada a USGS."""

    feature: UsgsFeature | None
    cache_hit: bool
    latency_ms: float


async def _encadenar_usgs(
    client: httpx.AsyncClient, feed_url: str, timeout: httpx.Timeout
) -> UsgsFeature | None:
    """Hace las dos llamadas encadenadas: feed -> detalle del primer evento."""

    try:
        respuesta_feed = await client.get(feed_url, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise TimeoutDependencia("USGS excedió el tiempo de espera al pedir el feed") from exc

    if respuesta_feed.status_code == 404:
        raise NoEncontrado("USGS no encontró el feed solicitado")
    if respuesta_feed.is_error:
        raise ErrorDependencia(f"USGS devolvió {respuesta_feed.status_code} en el feed")

    try:
        feed = UsgsFeed.model_validate(respuesta_feed.json())
    except (ValidationError, ValueError) as exc:
        raise RespuestaInvalida("El feed de USGS no cumple el modelo esperado") from exc

    if not feed.features:
        return None

    evento = feed.features[0]
    url_detalle = evento.properties.detail
    if not url_detalle:
        return evento

    try:
        respuesta_detalle = await client.get(url_detalle, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise TimeoutDependencia(
            "USGS excedió el tiempo de espera al pedir el detalle del evento"
        ) from exc

    if respuesta_detalle.status_code == 404:
        raise NoEncontrado("El evento ya no existe en USGS")
    if respuesta_detalle.is_error:
        raise ErrorDependencia(
            f"USGS devolvió {respuesta_detalle.status_code} en el detalle del evento"
        )

    try:
        return UsgsFeature.model_validate(respuesta_detalle.json())
    except (ValidationError, ValueError) as exc:
        raise RespuestaInvalida(
            "El detalle del evento no cumple el modelo esperado"
        ) from exc


class ExternalEarthquakeClient:
    """Consulta encadenada con caché propia.

    Mantiene la firma que ya usa ``main.py``:
    ``ExternalEarthquakeClient(client, feed_url, timeout_seconds, ttl_seconds)``.
    """

    def __init__(
        self, client: httpx.AsyncClient, feed_url: str, timeout_seconds: float, ttl_seconds: int
    ) -> None:
        self._client = client
        self._feed_url = feed_url
        self._timeout = _tiempo_timeout(timeout_seconds)
        self._cache: MemoryCache[UsgsFeature | None] = MemoryCache(ttl_seconds)

    async def get_latest_event(self) -> ExternalResult:
        """Devuelve el último sismo con detalle, usando la caché con TTL."""

        inicio = time.perf_counter()
        guardado = await self._cache.get(self._feed_url)
        if guardado is not None:
            return ExternalResult(
                guardado, True, (time.perf_counter() - inicio) * 1000
            )

        sismo = await _encadenar_usgs(self._client, self._feed_url, self._timeout)
        if sismo is not None:
            await self._cache.set(self._feed_url, sismo)
        return ExternalResult(sismo, False, (time.perf_counter() - inicio) * 1000)


# Caché de módulo para la función de conveniencia del contrato.
_cache_externa: MemoryCache[UsgsFeature | None] | None = None


def _cache_de_modulo(ttl_segundos: int) -> MemoryCache[UsgsFeature | None]:
    """Crea (una sola vez) la caché compartida por ``obtener_ultimo_sismo``."""

    global _cache_externa
    if _cache_externa is None:
        _cache_externa = MemoryCache(ttl_segundos)
    return _cache_externa


async def obtener_ultimo_sismo(
    client: httpx.AsyncClient,
    feed_url: str,
    *,
    timeout_seconds: float = TIMEOUT_LECTURA,
    ttl_segundos: int = TTL_CACHE,
) -> tuple[UsgsFeature | None, bool]:
    """Igual que la clase pero sin instanciarla: devuelve ``(sismo, fue_hit)``."""

    cache = _cache_de_modulo(ttl_segundos)
    guardado = await cache.get(feed_url)
    if guardado is not None:
        return guardado, True

    sismo = await _encadenar_usgs(client, feed_url, _tiempo_timeout(timeout_seconds))
    if sismo is not None:
        await cache.set(feed_url, sismo)
    return sismo, False


__all__ = [
    "ExternalApiError",
    "ExternalEarthquakeClient",
    "ExternalResult",
    "obtener_ultimo_sismo",
]

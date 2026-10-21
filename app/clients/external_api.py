# app/clients/external_api.py
"""Cliente asíncrono para la API pública de terremotos de USGS."""

import time
from dataclasses import dataclass

import httpx

from app.cache import MemoryCache
from app.models import UsgsFeature, UsgsFeed


class ExternalApiError(Exception):
    """Error no recuperable producido al consultar USGS."""


@dataclass(frozen=True, slots=True)
class ExternalResult:
    feature: UsgsFeature | None
    cache_hit: bool
    latency_ms: float


class ExternalEarthquakeClient:
    """Realiza la consulta de feed y luego la consulta de detalle del evento."""

    def __init__(self, client: httpx.AsyncClient, feed_url: str, timeout_seconds: float, ttl_seconds: int) -> None:
        self._client = client
        self._feed_url = feed_url
        self._timeout = httpx.Timeout(timeout_seconds)
        self._cache: MemoryCache[UsgsFeature | None] = MemoryCache(ttl_seconds)

    async def get_latest_event(self) -> ExternalResult:
        """Obtiene el primer sismo del feed y encadena la llamada a su endpoint `detail`.

        El resultado final se almacena durante el TTL configurado. Cualquier fallo de
        red, timeout, HTTP no exitoso o JSON incompatible se expone como error de
        dependencia para que el router lo convierta en 502/504.
        """

        started = time.perf_counter()
        cached = await self._cache.get("latest-event")
        if cached is not None:
            return ExternalResult(cached, True, (time.perf_counter() - started) * 1000)

        try:
            feed_response = await self._client.get(self._feed_url, timeout=self._timeout)
            feed_response.raise_for_status()
            feed = UsgsFeed.model_validate(feed_response.json())
            feature = feed.features[0] if feed.features else None
            if feature is not None and feature.properties.detail:
                detail_response = await self._client.get(feature.properties.detail, timeout=self._timeout)
                detail_response.raise_for_status()
                detail_data = detail_response.json()
                if not isinstance(detail_data, dict) or "properties" not in detail_data:
                    raise ExternalApiError("El detalle de USGS no tiene formato GeoJSON Feature")
                feature = UsgsFeature.model_validate(detail_data)
        except httpx.TimeoutException as exc:
            raise TimeoutError("USGS excedió el tiempo de espera") from exc
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise ExternalApiError("USGS devolvió una respuesta inválida o no disponible") from exc

        await self._cache.set("latest-event", feature)
        return ExternalResult(feature, False, (time.perf_counter() - started) * 1000)


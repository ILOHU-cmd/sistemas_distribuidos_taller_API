# app/clients/unstable_service.py
"""Cliente resiliente para el servicio local inestable."""

import asyncio
import random

import httpx

from app.models import Sensor, SensorPage


class UnstableServiceError(Exception):
    """Error HTTP no recuperable del servicio inestable."""


class UnstableServiceClient:
    """Implementa reintentos selectivos, Retry-After y paginación por cursor.

    503 y timeouts usan backoff exponencial con jitter. 429 respeta exactamente
    `Retry-After`; 400 y 404 fallan inmediatamente. El deadline total se aplica
    externamente con `asyncio.wait_for` para evitar esperas indefinidas.
    """

    def __init__(self, client: httpx.AsyncClient, base_url: str, timeout_seconds: float, max_retries: int, backoff_base: float) -> None:
        self._client = client
        self._url = f"{base_url.rstrip('/')}/sensores"
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def _request_page(self, cursor: str | None) -> SensorPage:
        """Solicita una página y reintenta únicamente los estados permitidos."""

        params = {"cursor": cursor} if cursor is not None else None
        attempt = 0
        while True:
            try:
                response = await self._client.get(self._url, params=params, timeout=self._timeout)
            except httpx.TimeoutException as exc:
                if attempt >= self._max_retries:
                    raise TimeoutError("El servicio inestable agotó sus reintentos") from exc
                await asyncio.sleep((self._backoff_base**attempt) + random.uniform(0, 1))
                attempt += 1
                continue

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                if retry_after is None:
                    raise UnstableServiceError("429 sin cabecera Retry-After")
                try:
                    delay = float(retry_after)
                except ValueError as exc:
                    raise UnstableServiceError("Retry-After no es numérico") from exc
                await asyncio.sleep(max(0.0, delay))
                continue

            if response.status_code == 503:
                if attempt >= self._max_retries:
                    raise UnstableServiceError("El servicio inestable continúa devolviendo 503")
                await asyncio.sleep((self._backoff_base**attempt) + random.uniform(0, 1))
                attempt += 1
                continue

            if response.status_code in (400, 404):
                raise UnstableServiceError(f"Servicio inestable devolvió {response.status_code}")
            if response.is_error:
                raise UnstableServiceError(f"Servicio inestable devolvió {response.status_code}")
            try:
                return SensorPage.model_validate(response.json())
            except (ValueError, TypeError) as exc:
                raise UnstableServiceError("Respuesta inválida del servicio inestable") from exc

    async def get_all_sensors(self) -> list[Sensor]:
        """Recorre secuencialmente todas las páginas hasta que `siguiente` sea nulo."""

        sensors: list[Sensor] = []
        cursor: str | None = None
        while True:
            page = await self._request_page(cursor)
            sensors.extend(page.sensores)
            if page.siguiente is None:
                return sensors
            cursor = page.siguiente


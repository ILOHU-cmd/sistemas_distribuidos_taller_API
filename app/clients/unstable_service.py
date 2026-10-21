# app/clients/unstable_service.py
"""Cliente resiliente para el servicio local inestable (registro de sensores).

Cubre los puntos de resiliencia del taller:

1. Timeout EXPLÍCITO en cada llamada (conexión y lectura por separado). La
   lectura (~2 s) es menor que la lentitud simulada del servicio (4 s), así que
   una respuesta lenta cuenta como timeout reintentable.
2. Reintentos con backoff exponencial y *full jitter*.
3. Solo se reintentan 503, 429 y timeouts; 400/404 y otros 4xx se devuelven
   tal cual para que la capa superior los traduzca. Los errores de conexión
   (servicio caído) no se reintentan: se traducen a ``UnstableServiceError`` (502).
4. Si llega la cabecera ``Retry-After``, esa espera manda sobre el backoff.
5. Tras un 429 el servicio bloquea por IP: se guarda un ``no_antes_de``
   compartido (``asyncio.Lock`` + ``time.monotonic``) que TODAS las tareas
   respetan, y se limita la concurrencia con ``asyncio.Semaphore(3)``.
6. La paginación sigue ``siguiente`` hasta ``null`` y reintenta POR PÁGINA.
7. ``obtener_lecturas`` degrada parcialmente: un sensor que falla no tumba al resto.
8. Cada intento se registra con ``logging`` (causa, espera calculada y origen).
9. La espera pasa por ``_dormir`` para poder reemplazarla en las pruebas.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.clients import (
    ErrorDependencia,
    NoEncontrado,
    RespuestaInvalida,
    TimeoutDependencia,
    UnstableServiceError,
)
from app.models import Sensor

# ``UnstableServiceError`` se importa para reexportarlo: ``main.py`` y el router
# de Daniela lo importan desde este submódulo.
__all__ = ["UnstableServiceClient", "UnstableServiceError", "get_con_reintentos"]

_log = logging.getLogger("app.clients.unstable_service")

# --- Parámetros por defecto (los del contrato) -----------------------------
MAX_INTENTOS = 5
BACKOFF_BASE = 0.5
BACKOFF_TOPE = 8.0
TIMEOUT_CONEXION = 2.0
TIMEOUT_LECTURA = 2.0  # < 4 s de lentitud simulada: lo lento es un timeout
TTL_CACHE = 300

# ``_dormir`` es una variable de módulo a propósito: en las pruebas se reemplaza
# por un doble para medir las esperas sin dormir de verdad.
_dormir = asyncio.sleep

# Máximo de peticiones simultáneas al servicio inestable.
_semaforo = asyncio.Semaphore(3)

# Bloqueo por IP tras un 429. ``_no_antes_de`` es un instante de
# ``time.monotonic`` que todas las tareas consultan antes de llamar; el lock
# protege la lectura y la escritura para que no haya carreras.
_lock_espera = asyncio.Lock()
_no_antes_de = 0.0


class SensorInestable(BaseModel):
    """Sensor crudo tal como lo devuelve el servicio (campo ``tipo``)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    tipo: str | None = None
    ubicacion: str | None = None


class PaginaSensores(BaseModel):
    """Página de sensores; ``siguiente`` es ``None`` en la última página."""

    model_config = ConfigDict(extra="ignore")

    sensores: list[SensorInestable]
    siguiente: str | None = None


class Lectura(BaseModel):
    """Lectura actual de un sensor."""

    model_config = ConfigDict(extra="ignore")

    sensor_id: str
    tipo: str
    valor: float
    unidad: str
    marca_tiempo: str


# ---------------------------------------------------------------------------
# Utilidades de tiempo y espera
# ---------------------------------------------------------------------------


def _tiempo_timeout(timeout_seconds: float) -> httpx.Timeout:
    """Timeout explícito con conexión y lectura acotadas por separado."""

    return httpx.Timeout(
        connect=TIMEOUT_CONEXION,
        read=min(timeout_seconds, TIMEOUT_LECTURA),
        write=timeout_seconds,
        pool=timeout_seconds,
    )


def _espera_backoff(intento: int, base: float) -> tuple[float, str]:
    """Full jitter: ``uniform(0, min(BACKOFF_TOPE, base * 2 ** (intento - 1)))``."""

    tope = min(BACKOFF_TOPE, base * (2 ** (intento - 1)))
    return random.uniform(0.0, tope), "backoff"


def _leer_retry_after(respuesta: httpx.Response) -> float | None:
    """Devuelve los segundos de ``Retry-After`` o ``None`` si no es utilizable."""

    valor = respuesta.headers.get("Retry-After")
    if valor is None:
        return None
    try:
        return max(0.0, float(valor))
    except ValueError:
        return None


def _registrar(
    intento: int, max_intentos: int, causa: str, espera: float, origen: str
) -> None:
    """Evidencia legible de cada intento (sirve para el README)."""

    _log.info(
        "intento %d/%d, causa=%s, espera=%.3fs, origen=%s",
        intento,
        max_intentos,
        causa,
        espera,
        origen,
    )


async def _marcar_bloqueo(segundos: float) -> None:
    """Registra que no se puede volver a llamar hasta dentro de ``segundos``."""

    global _no_antes_de
    async with _lock_espera:
        _no_antes_de = max(_no_antes_de, time.monotonic() + segundos)


async def _esperar_turno() -> None:
    """Espera (fuera del lock) si el bloqueo compartido sigue vigente."""

    async with _lock_espera:
        restante = _no_antes_de - time.monotonic()
    if restante > 0:
        await _dormir(restante)


# ---------------------------------------------------------------------------
# Reintentos selectivos (función del contrato)
# ---------------------------------------------------------------------------


async def get_con_reintentos(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any] | None = None,
    *,
    max_intentos: int = MAX_INTENTOS,
    backoff_base: float = BACKOFF_BASE,
    timeout: httpx.Timeout | None = None,
) -> httpx.Response:
    """GET con reintentos selectivos.

    Devuelve la respuesta de cualquier estado NO reintentable (400, 404 u otros)
    para que la capa superior la traduzca. Solo reintenta 503, 429 y timeouts, y
    lanza ``UnstableServiceError``/``TimeoutDependencia`` al agotar los intentos.
    """

    timeout = timeout or _tiempo_timeout(TIMEOUT_LECTURA)

    for intento in range(1, max_intentos + 1):
        # Todo intento respeta el bloqueo compartido tras un 429.
        await _esperar_turno()
        try:
            async with _semaforo:  # como máximo 3 peticiones simultáneas
                respuesta = await client.get(url, params=params, timeout=timeout)
        except httpx.TimeoutException as exc:
            if intento >= max_intentos:
                raise TimeoutDependencia(
                    f"El servicio inestable agotó {max_intentos} intentos por timeout en {url}"
                ) from exc
            espera, origen = _espera_backoff(intento, backoff_base)
            _registrar(intento, max_intentos, "timeout", espera, origen)
            await _dormir(espera)
            continue

        except httpx.TransportError as exc:
            # Conexión rechazada o cortada: reintentar no ayuda, así que se falla
            # rápido con un error de dependencia (el router lo traduce a 502).
            raise UnstableServiceError(
                f"No se pudo conectar con el servicio inestable en {url}"
            ) from exc

        if respuesta.status_code == 429:
            retry_after = _leer_retry_after(respuesta)
            if retry_after is not None:
                # Retry-After MANDA: se usa tal cual y se marca el bloqueo por IP.
                espera, origen = retry_after, "Retry-After"
                await _marcar_bloqueo(retry_after)
            else:
                espera, origen = _espera_backoff(intento, backoff_base)
            if intento >= max_intentos:
                raise UnstableServiceError(
                    f"El servicio inestable sigue devolviendo 429 tras {max_intentos} intentos"
                )
            _registrar(intento, max_intentos, "429", espera, origen)
            await _dormir(espera)
            continue

        if respuesta.status_code == 503:
            if intento >= max_intentos:
                raise UnstableServiceError(
                    f"El servicio inestable sigue devolviendo 503 tras {max_intentos} intentos"
                )
            espera, origen = _espera_backoff(intento, backoff_base)
            _registrar(intento, max_intentos, "503", espera, origen)
            await _dormir(espera)
            continue

        # 400, 404 y demás: NO se reintentan, se devuelven a quien llamó.
        return respuesta

    # Inalcanzable: el bucle siempre retorna o lanza en el último intento.
    raise UnstableServiceError("Reintentos agotados sin respuesta")


# ---------------------------------------------------------------------------
# Cliente de alto nivel (lo que consume el router de Daniela)
# ---------------------------------------------------------------------------


class UnstableServiceClient:
    """Cliente con paginación, lecturas, degradación parcial y salud.

    Mantiene la firma que ya usa ``main.py`` para no romper el arranque:
    ``UnstableServiceClient(client, base_url, timeout_seconds, max_retries, backoff_base)``.
    ``max_retries`` se interpreta como número máximo de INTENTOS.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        backoff_base: float,
    ) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self._timeout = _tiempo_timeout(timeout_seconds)
        self._max_intentos = max_retries
        self._backoff_base = backoff_base

    async def get_con_reintentos(
        self, url: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        """Reintenta una URL relativa o absoluta con los parámetros del cliente."""

        return await get_con_reintentos(
            self._client,
            url,
            params,
            max_intentos=self._max_intentos,
            backoff_base=self._backoff_base,
            timeout=self._timeout,
        )

    async def _pedir_pagina(self, cursor: str | None) -> PaginaSensores:
        """Pide UNA página y la valida; el reintento ocurre dentro de esta página."""

        params = {"cursor": cursor} if cursor else None
        respuesta = await self.get_con_reintentos(f"{self._base}/sensores", params)
        if respuesta.status_code == 404:
            raise NoEncontrado("El servicio inestable no encontró el listado de sensores")
        if respuesta.is_error:
            raise UnstableServiceError(
                f"El servicio inestable devolvió {respuesta.status_code} al listar sensores"
            )
        try:
            return PaginaSensores.model_validate(respuesta.json())
        except (ValidationError, ValueError) as exc:
            raise RespuestaInvalida(
                "La página de sensores no cumple el modelo esperado"
            ) from exc

    async def get_all_sensors(self) -> list[Sensor]:
        """Recorre TODA la paginación siguiendo ``siguiente`` hasta ``None``."""

        sensores: list[Sensor] = []
        cursor: str | None = None
        while True:
            pagina = await self._pedir_pagina(cursor)
            sensores.extend(
                Sensor(id=crudo.id, ubicacion=crudo.ubicacion)
                for crudo in pagina.sensores
            )
            if pagina.siguiente is None:
                return sensores
            cursor = pagina.siguiente

    async def listar_sensores(self) -> list[Sensor]:
        """Alias del contrato para ``get_all_sensors``."""

        return await self.get_all_sensors()

    async def obtener_lectura(self, sensor_id: str) -> Lectura:
        """Lectura de un sensor; un 404 se traduce a ``NoEncontrado``."""

        respuesta = await self.get_con_reintentos(
            f"{self._base}/sensores/{sensor_id}/lectura"
        )
        if respuesta.status_code == 404:
            raise NoEncontrado(f"No existe el sensor {sensor_id}")
        if respuesta.is_error:
            raise UnstableServiceError(
                f"El servicio inestable devolvió {respuesta.status_code} "
                f"al pedir la lectura de {sensor_id}"
            )
        try:
            return Lectura.model_validate(respuesta.json())
        except (ValidationError, ValueError) as exc:
            raise RespuestaInvalida(
                f"La lectura del sensor {sensor_id} no cumple el modelo esperado"
            ) from exc

    async def sensor_existe(self, sensor_id: str) -> bool:
        """``True`` si el sensor existe; ``False`` si el servicio responde 404."""

        try:
            await self.obtener_lectura(sensor_id)
        except NoEncontrado:
            return False
        return True

    async def obtener_lecturas(
        self, sensor_ids: list[str]
    ) -> tuple[list[Lectura], list[dict[str, str]]]:
        """Degradación parcial: devuelve (lecturas ok, fallidos con motivo).

        Los sensores se piden en paralelo; un 404 o un fallo definitivo de uno
        no tumba a los demás.
        """

        resultados = await asyncio.gather(
            *(self.obtener_lectura(sensor_id) for sensor_id in sensor_ids),
            return_exceptions=True,
        )
        lecturas: list[Lectura] = []
        fallidos: list[dict[str, str]] = []
        for sensor_id, resultado in zip(sensor_ids, resultados):
            if isinstance(resultado, Lectura):
                lecturas.append(resultado)
            else:
                fallidos.append({"sensor_id": sensor_id, "motivo": str(resultado)})
        return lecturas, fallidos

    async def salud(self) -> bool:
        """``True`` si ``/salud`` responde 200; nunca propaga la excepción."""

        try:
            respuesta = await self.get_con_reintentos(f"{self._base}/salud")
        except ErrorDependencia:
            return False
        return respuesta.status_code == 200

# tests/clients/test_unstable_service.py
"""Pruebas de resiliencia del cliente del servicio inestable (respx, sin red)."""

import asyncio
import time

import httpx
import pytest
import respx

import app.clients.unstable_service as us
from app.clients import (
    ErrorDependencia,
    NoEncontrado,
    RespuestaInvalida,
    TimeoutDependencia,
    UnstableServiceError,
)
from app.clients.unstable_service import UnstableServiceClient

BASE = "http://servicio.local"
SENSORES_URL = f"{BASE}/sensores"

PAGINA = {
    "sensores": [{"id": "S-001", "tipo": "temperatura", "ubicacion": "Edificio A"}],
    "siguiente": None,
}
LECTURA = {
    "sensor_id": "S-001",
    "tipo": "temperatura",
    "valor": 21.5,
    "unidad": "°C",
    "marca_tiempo": "2026-10-21T10:00:00+00:00",
}


def _cliente(client: httpx.AsyncClient, max_intentos: int = 5, backoff: float = 0.0):
    return UnstableServiceClient(client, BASE, 2.0, max_intentos, backoff)


@pytest.mark.asyncio
async def test_503_luego_200_reintenta(dormidas):
    """Un 503 transitorio se reintenta y la segunda vez responde 200."""

    async with httpx.AsyncClient() as client:
        servicio = _cliente(client)
        with respx.mock:
            ruta = respx.get(SENSORES_URL).mock(
                side_effect=[httpx.Response(503), httpx.Response(200, json=PAGINA)]
            )
            sensores = await servicio.listar_sensores()

    assert ruta.call_count == 2
    assert [s.id for s in sensores] == ["S-001"]
    assert len(dormidas) == 1  # se calculó exactamente una espera


@pytest.mark.asyncio
async def test_429_respeta_retry_after(dormidas):
    """La espera de un 429 es >= Retry-After y marca el bloqueo compartido."""

    async with httpx.AsyncClient() as client:
        servicio = _cliente(client)
        with respx.mock:
            respx.get(SENSORES_URL).mock(
                side_effect=[
                    httpx.Response(429, headers={"Retry-After": "2"}),
                    httpx.Response(200, json=PAGINA),
                ]
            )
            await servicio.listar_sensores()

    assert max(dormidas) >= 2.0
    # El bloqueo por IP queda registrado para las demás tareas.
    assert us._no_antes_de >= time.monotonic() + 1.5


@pytest.mark.asyncio
async def test_400_no_se_reintenta(dormidas):
    """Un 400 es un error legítimo: exactamente un intento."""

    async with httpx.AsyncClient() as client:
        servicio = _cliente(client)
        with respx.mock:
            ruta = respx.get(SENSORES_URL).mock(
                return_value=httpx.Response(400, json={"detalle": "Cursor inválido"})
            )
            with pytest.raises(ErrorDependencia):
                await servicio.listar_sensores()

    assert ruta.call_count == 1
    assert dormidas == []


@pytest.mark.asyncio
async def test_404_lectura_no_se_reintenta(dormidas):
    """Un 404 al pedir una lectura se traduce a NoEncontrado sin reintentar."""

    async with httpx.AsyncClient() as client:
        servicio = _cliente(client)
        with respx.mock:
            ruta = respx.get(f"{BASE}/sensores/S-999/lectura").mock(
                return_value=httpx.Response(404, json={"detalle": "No existe"})
            )
            with pytest.raises(NoEncontrado):
                await servicio.obtener_lectura("S-999")

    assert ruta.call_count == 1
    assert dormidas == []


@pytest.mark.asyncio
async def test_503_persistente_agota_reintentos(dormidas):
    """Si el 503 es persistente, se agotan los intentos y se lanza ErrorDependencia."""

    async with httpx.AsyncClient() as client:
        servicio = _cliente(client, max_intentos=3)
        with respx.mock:
            ruta = respx.get(SENSORES_URL).mock(
                return_value=httpx.Response(503, json={"detalle": "no disponible"})
            )
            with pytest.raises(ErrorDependencia):
                await servicio.listar_sensores()

    assert ruta.call_count == 3
    assert len(dormidas) == 2  # esperas entre intentos, no tras el último


@pytest.mark.asyncio
async def test_timeout_persistente_es_timeoutdependencia(dormidas):
    """Un timeout que se repite se convierte en TimeoutDependencia."""

    async with httpx.AsyncClient() as client:
        servicio = _cliente(client, max_intentos=3)
        with respx.mock:
            ruta = respx.get(SENSORES_URL).mock(
                side_effect=httpx.ReadTimeout("demasiado lento")
            )
            with pytest.raises(TimeoutDependencia):
                await servicio.listar_sensores()

    assert ruta.call_count == 3


@pytest.mark.asyncio
async def test_paginacion_con_fallo_en_una_pagina(dormidas):
    """Se recorre toda la paginación y solo la página que falla se reintenta."""

    paginas = {
        None: {"sensores": [{"id": "S-001", "ubicacion": "A"}], "siguiente": "cur1"},
        "cur1": {"sensores": [{"id": "S-002", "ubicacion": "B"}], "siguiente": "cur2"},
        "cur2": {"sensores": [{"id": "S-003", "ubicacion": "C"}], "siguiente": None},
    }
    intentos_cur1 = {"n": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        if cursor == "cur1":
            intentos_cur1["n"] += 1
            if intentos_cur1["n"] == 1:
                return httpx.Response(503)  # solo esta página falla una vez
        return httpx.Response(200, json=paginas[cursor])

    async with httpx.AsyncClient() as client:
        servicio = _cliente(client)
        with respx.mock:
            ruta = respx.get(SENSORES_URL).mock(side_effect=responder)
            sensores = await servicio.listar_sensores()

    assert [s.id for s in sensores] == ["S-001", "S-002", "S-003"]
    assert ruta.call_count == 4  # S-001, cur1(falla), cur1(ok), cur2
    assert intentos_cur1["n"] == 2


@pytest.mark.asyncio
async def test_respuesta_invalida_listado(dormidas):
    """Una página que no cumple el modelo lanza RespuestaInvalida."""

    async with httpx.AsyncClient() as client:
        servicio = _cliente(client)
        with respx.mock:
            respx.get(SENSORES_URL).mock(
                return_value=httpx.Response(200, json={"sensores": "no-es-lista"})
            )
            with pytest.raises(RespuestaInvalida):
                await servicio.listar_sensores()


@pytest.mark.asyncio
async def test_respuesta_invalida_lectura(dormidas):
    """Una lectura a la que le falta un campo lanza RespuestaInvalida."""

    async with httpx.AsyncClient() as client:
        servicio = _cliente(client)
        with respx.mock:
            respx.get(f"{BASE}/sensores/S-001/lectura").mock(
                return_value=httpx.Response(200, json={"sensor_id": "S-001"})
            )
            with pytest.raises(RespuestaInvalida):
                await servicio.obtener_lectura("S-001")


@pytest.mark.asyncio
async def test_degradacion_parcial_lecturas(dormidas):
    """Un sensor que falla no tumba a los demás: (ok, fallidos)."""

    def responder(request: httpx.Request) -> httpx.Response:
        sensor_id = request.url.path.split("/")[2]
        if sensor_id == "S-002":
            return httpx.Response(404, json={"detalle": "No existe"})
        return httpx.Response(200, json={**LECTURA, "sensor_id": sensor_id})

    async with httpx.AsyncClient() as client:
        servicio = _cliente(client)
        with respx.mock:
            respx.get(url__regex=rf"{BASE}/sensores/.+/lectura").mock(
                side_effect=responder
            )
            lecturas, fallidos = await servicio.obtener_lecturas(
                ["S-001", "S-002", "S-003"]
            )

    assert sorted(lec.sensor_id for lec in lecturas) == ["S-001", "S-003"]
    assert len(fallidos) == 1
    assert fallidos[0]["sensor_id"] == "S-002"
    assert "S-002" in fallidos[0]["motivo"]


@pytest.mark.asyncio
async def test_salud_y_sensor_existe(dormidas):
    """``salud`` responde True y ``sensor_existe`` distingue 200 de 404."""

    async with httpx.AsyncClient() as client:
        servicio = _cliente(client)
        with respx.mock:
            respx.get(f"{BASE}/salud").mock(
                return_value=httpx.Response(200, json={"estado": "ok"})
            )
            respx.get(f"{BASE}/sensores/S-001/lectura").mock(
                return_value=httpx.Response(200, json=LECTURA)
            )
            respx.get(f"{BASE}/sensores/S-999/lectura").mock(
                return_value=httpx.Response(404)
            )
            assert await servicio.salud() is True
            assert await servicio.sensor_existe("S-001") is True
            assert await servicio.sensor_existe("S-999") is False


@pytest.mark.asyncio
async def test_no_antes_de_compartido(dormidas):
    """Tras un bloqueo, todas las tareas esperan el ``no_antes_de`` compartido."""

    await us._marcar_bloqueo(1.0)

    async def una_tarea() -> None:
        await us._esperar_turno()

    await asyncio.gather(*(una_tarea() for _ in range(3)))

    assert len(dormidas) == 3
    assert all(espera >= 0.5 for espera in dormidas)


@pytest.mark.asyncio
async def test_lecturas_concurrentes_respetan_bloqueo(dormidas):
    """Varias lecturas a la vez esperan el bloqueo vigente antes de llamar."""

    await us._marcar_bloqueo(1.0)

    def responder(request: httpx.Request) -> httpx.Response:
        sensor_id = request.url.path.split("/")[2]
        return httpx.Response(200, json={**LECTURA, "sensor_id": sensor_id})

    async with httpx.AsyncClient() as client:
        servicio = _cliente(client)
        with respx.mock:
            respx.get(url__regex=rf"{BASE}/sensores/.+/lectura").mock(
                side_effect=responder
            )
            resultados = await asyncio.gather(
                *(servicio.obtener_lectura(f"S-00{i}") for i in range(1, 4))
            )

    assert len(resultados) == 3
    assert len(dormidas) >= 3
    assert all(espera >= 0.5 for espera in dormidas)


@pytest.mark.asyncio
async def test_error_de_conexion_es_errordependencia(dormidas):
    """Servicio caído: se traduce a ErrorDependencia (router -> 502) sin reintentar."""

    async with httpx.AsyncClient() as client:
        with respx.mock:
            respx.get(SENSORES_URL).mock(
                side_effect=httpx.ConnectError("servicio caído")
            )
            with pytest.raises(UnstableServiceError):
                await _cliente(client).get_all_sensors()

    # Falla rápido: reintentar una conexión rechazada no aporta nada.
    assert dormidas == []

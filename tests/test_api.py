# tests/test_api.py
"""Pruebas de los endpoints HTTP y de la idempotencia de alertas.

No salen a la red: reemplazan los clientes de ``app.state`` por dobles.
"""

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.clients import ExternalApiError, NoEncontrado
from app.clients.external_api import ExternalResult
from app.models import Sensor
from main import app


class ExternalOk:
    """Devuelve un resultado válido sin consultar USGS."""

    async def get_latest_event(self, usar_cache: bool = True) -> ExternalResult:
        return ExternalResult(feature=None, cache_hit=True, latency_ms=1.0)


class ExternalError:
    """Simula una dependencia externa no disponible."""

    async def get_latest_event(self, usar_cache: bool = True) -> ExternalResult:
        raise ExternalApiError("USGS no disponible")


class ExternalNotFound:
    """Simula un 404 de la dependencia externa."""

    async def get_latest_event(self, usar_cache: bool = True) -> ExternalResult:
        raise NoEncontrado("USGS no encontró el feed")


class ExternalSlow:
    """Tarda más que el deadline total configurado en la prueba."""

    async def get_latest_event(self, usar_cache: bool = True) -> ExternalResult:
        await asyncio.sleep(1.0)
        return ExternalResult(feature=None, cache_hit=False, latency_ms=1000.0)


class UnstableOk:
    """Listado de sensores válido, sin consultar el servicio inestable."""

    async def get_all_sensors(self) -> list[Sensor]:
        return [Sensor(id="S-001", tipo="temperatura", ubicacion="Edificio A")]


def _cliente(external: object, timeout: float = 5.0) -> TestClient:
    """Prepara el estado de la app y devuelve un cliente de pruebas."""

    app.state.idempotency_store = {}
    app.state.idempotency_locks = {}
    app.state.settings = SimpleNamespace(unstable_total_timeout_seconds=timeout)
    app.state.external_client = external
    app.state.unstable_client = UnstableOk()
    return TestClient(app)


CUERPO = {
    "sensor_id": "S-001",
    "tipo": "temperatura",
    "mensaje": "Umbral",
    "severidad": "alta",
}


def test_salud() -> None:
    respuesta = _cliente(ExternalOk()).get("/salud")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok"}


def test_monitoreo_ok_y_conserva_tipo() -> None:
    respuesta = _cliente(ExternalOk()).get("/v1/monitoreo-riesgo")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["cache_hit"] is True
    assert cuerpo["sismo"] is None
    assert cuerpo["sensores"][0]["tipo"] == "temperatura"


def test_monitoreo_502_si_dependencia_falla() -> None:
    respuesta = _cliente(ExternalError()).get("/v1/monitoreo-riesgo")
    assert respuesta.status_code == 502
    assert "USGS no disponible" in respuesta.json()["detail"]


def test_monitoreo_404_si_no_existe() -> None:
    respuesta = _cliente(ExternalNotFound()).get("/v1/monitoreo-riesgo")
    assert respuesta.status_code == 404
    assert "no encontró" in respuesta.json()["detail"]


def test_monitoreo_504_si_supera_deadline() -> None:
    respuesta = _cliente(ExternalSlow(), timeout=0.01).get("/v1/monitoreo-riesgo")
    assert respuesta.status_code == 504
    assert "tiempo total" in respuesta.json()["detail"]


def test_idempotencia_misma_key_y_cuerpo_devuelve_mismo_id() -> None:
    cliente = _cliente(ExternalOk())
    cabeceras = {"Idempotency-Key": "k-1"}
    primera = cliente.post("/v1/alertas", json=CUERPO, headers=cabeceras)
    segunda = cliente.post("/v1/alertas", json=CUERPO, headers=cabeceras)
    assert primera.status_code == 201
    assert segunda.status_code == 201
    assert primera.json()["id"] == segunda.json()["id"]
    assert primera.json()["creada_en"] == segunda.json()["creada_en"]


def test_idempotencia_misma_key_y_cuerpo_distinto_es_422() -> None:
    cliente = _cliente(ExternalOk())
    cabeceras = {"Idempotency-Key": "k-2"}
    cliente.post("/v1/alertas", json=CUERPO, headers=cabeceras)
    respuesta = cliente.post(
        "/v1/alertas", json={**CUERPO, "sensor_id": "S-002"}, headers=cabeceras
    )
    assert respuesta.status_code == 422
    assert "cuerpo diferente" in respuesta.json()["detail"]


def test_idempotencia_sin_cabecera_es_422() -> None:
    respuesta = _cliente(ExternalOk()).post("/v1/alertas", json=CUERPO)
    assert respuesta.status_code == 422


def test_claves_distintas_crean_alertas_distintas() -> None:
    cliente = _cliente(ExternalOk())
    primera = cliente.post(
        "/v1/alertas", json=CUERPO, headers={"Idempotency-Key": "k-a"}
    )
    segunda = cliente.post(
        "/v1/alertas", json=CUERPO, headers={"Idempotency-Key": "k-b"}
    )
    assert primera.status_code == segunda.status_code == 201
    assert primera.json()["id"] != segunda.json()["id"]

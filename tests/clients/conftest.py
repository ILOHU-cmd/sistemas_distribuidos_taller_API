# tests/clients/conftest.py
"""Utilidades compartidas por las pruebas de los clientes."""

import app.clients.external_api as ex
import app.clients.unstable_service as us
import pytest


@pytest.fixture(autouse=True)
def limpiar_estado_global():
    """Deja limpios los bloqueos y cachés de módulo antes de cada prueba."""

    us._no_antes_de = 0.0
    ex._cache_externa = None
    yield
    us._no_antes_de = 0.0
    ex._cache_externa = None


@pytest.fixture
def dormidas(monkeypatch):
    """Reemplaza ``_dormir`` por un doble y registra los segundos pedidos."""

    registro: list[float] = []

    async def falso_dormir(segundos: float) -> None:
        registro.append(segundos)

    monkeypatch.setattr(us, "_dormir", falso_dormir)
    return registro

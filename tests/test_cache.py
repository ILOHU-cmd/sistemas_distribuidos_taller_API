"""Pruebas de la caché TTL y la deduplicación single-flight."""

import asyncio
from uuid import uuid4

import pytest

from app.cache import obtener_o_calcular


@pytest.mark.asyncio
async def test_obtener_o_calcular_comparte_calculo_concurrente():
    clave = f"single-flight-{uuid4()}"
    llamadas = 0

    async def fabrica() -> str:
        nonlocal llamadas
        llamadas += 1
        await asyncio.sleep(0.01)
        return "resultado"

    resultados = await asyncio.gather(
        *(obtener_o_calcular(clave, fabrica) for _ in range(10))
    )

    assert llamadas == 1
    assert resultados == ["resultado"] * 10


@pytest.mark.asyncio
async def test_obtener_o_calcular_no_guarda_fallos():
    clave = f"single-flight-error-{uuid4()}"
    llamadas = 0

    async def fabrica() -> str:
        nonlocal llamadas
        llamadas += 1
        if llamadas == 1:
            raise ValueError("fallo temporal")
        return "recuperado"

    with pytest.raises(ValueError, match="fallo temporal"):
        await obtener_o_calcular(clave, fabrica)

    assert await obtener_o_calcular(clave, fabrica) == "recuperado"
    assert llamadas == 2

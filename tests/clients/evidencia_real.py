# tests/clients/evidencia_real.py
"""Prueba real contra ``servicio_inestable.py`` (evidencia para el README).

No es una prueba de pytest: se ejecuta a mano con el servicio corriendo, p. ej.
``python servicio_inestable.py --semilla 42 --tasa-fallos 0.4``.
"""

import asyncio
import logging
import os

import httpx

from app.clients import ErrorDependencia
from app.clients.unstable_service import UnstableServiceClient

URL = os.environ.get("URL_INESTABLE", "http://127.0.0.1:9000")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    async with httpx.AsyncClient() as client:
        servicio = UnstableServiceClient(
            client,
            URL,
            timeout_seconds=2.0,
            max_retries=5,
            backoff_base=0.5,
        )

        print("== /salud ==")
        print("salud:", await servicio.salud())

        print("\n== obtener_lecturas (degradacion parcial con un sensor inexistente) ==")
        ids = ["S-001", "S-002", "S-003", "S-999"]
        try:
            lecturas, fallidos = await servicio.obtener_lecturas(ids)
            print(f"  lecturas ok: {len(lecturas)}  fallidos: {len(fallidos)}")
            for lectura in lecturas:
                print("  ok:", lectura.model_dump())
            for fallo in fallidos:
                print("  fallido:", fallo)
        except ErrorDependencia as exc:
            print("  la operacion completa fallo:", exc)

        print("\n== listar_sensores (recorre toda la paginacion) ==")
        try:
            sensores = await servicio.listar_sensores()
            print(f"total sensores: {len(sensores)}")
            for sensor in sensores:
                print(" ", sensor.id, sensor.ubicacion)
        except ErrorDependencia as exc:
            print("  la paginacion no termino:", exc)

        print("\n== obtener_lectura (un sensor puntual) ==")
        try:
            lectura = await servicio.obtener_lectura("S-001")
            print("  lectura:", lectura.model_dump())
        except ErrorDependencia as exc:
            print("  no se pudo leer S-001:", exc)


if __name__ == "__main__":
    asyncio.run(main())

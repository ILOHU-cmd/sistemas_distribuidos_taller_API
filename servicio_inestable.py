"""
servicio_inestable.py — Registro de sensores que falla a propósito.

Sistemas Distribuidos · Evaluación-taller "Agregador resiliente de APIs"
Escuela de Ingeniería de Sistemas e Informática — UIS

Uso:
    pip install fastapi uvicorn
    python servicio_inestable.py                      # valores por defecto
    python servicio_inestable.py --tasa-fallos 0.4 --latencia 5 --semilla 42
    python servicio_inestable.py --host 0.0.0.0       # accesible desde otros equipos

Endpoints:
    GET /salud                    Comprobación de vida (nunca falla)
    GET /config                   Parámetros con los que se arrancó el servicio
    GET /sensores?cursor=...      Lista paginada de sensores
    GET /sensores/{id}/lectura    Lectura actual de un sensor

Fallos inyectados (solo en /sensores...):
    - 429 Too Many Requests con cabecera Retry-After. Si el cliente vuelve a
      llamar antes de que pase ese tiempo, recibe otro 429.
    - 503 Service Unavailable aleatorios.
    - Respuestas lentas (se duerme --latencia segundos antes de responder).
Errores legítimos (NO deben reintentarse):
    - 400 si el cursor es inválido.
    - 404 si el sensor no existe.
"""

import argparse
import asyncio
import base64
import random
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Configuración por línea de comandos
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Servicio de sensores inestable")
parser.add_argument("--host", default="127.0.0.1",
                    help="Interfaz de escucha (0.0.0.0 para aceptar otros equipos)")
parser.add_argument("--puerto", type=int, default=9000)
parser.add_argument("--tasa-fallos", type=float, default=0.30,
                    help="Probabilidad de responder 503 (0 a 1)")
parser.add_argument("--tasa-429", type=float, default=0.10,
                    help="Probabilidad de responder 429 con Retry-After (0 a 1)")
parser.add_argument("--tasa-lentitud", type=float, default=0.20,
                    help="Probabilidad de que una respuesta sea lenta (0 a 1)")
parser.add_argument("--latencia", type=float, default=4.0,
                    help="Segundos de espera en las respuestas lentas")
parser.add_argument("--retry-after", type=int, default=2,
                    help="Segundos indicados en la cabecera Retry-After")
parser.add_argument("--semilla", type=int, default=None,
                    help="Semilla aleatoria para hacer reproducibles los fallos")
parser.add_argument("--tam-pagina", type=int, default=5,
                    help="Sensores por página")
parser.add_argument("--num-sensores", type=int, default=23)
ARGS = parser.parse_args()

if ARGS.tasa_fallos + ARGS.tasa_429 > 1:
    parser.error("--tasa-fallos + --tasa-429 no puede superar 1")

rng = random.Random(ARGS.semilla)

# ---------------------------------------------------------------------------
# Datos simulados
# ---------------------------------------------------------------------------

TIPOS = {
    "temperatura": ("°C", 18.0, 32.0),
    "humedad": ("%", 40.0, 95.0),
    "ruido": ("dB", 35.0, 90.0),
    "co2": ("ppm", 400.0, 1500.0),
}
UBICACIONES = ["Edificio A", "Edificio B", "Laboratorio 1", "Laboratorio 2",
               "Biblioteca", "Cafetería", "Auditorio"]

_datos = random.Random(ARGS.semilla)
SENSORES: List[Dict] = []
for i in range(1, ARGS.num_sensores + 1):
    tipo = _datos.choice(list(TIPOS))
    SENSORES.append({
        "id": f"S-{i:03d}",
        "tipo": tipo,
        "ubicacion": _datos.choice(UBICACIONES),
    })
POR_ID = {s["id"]: s for s in SENSORES}

# Bloqueos por cliente tras un 429: ip -> instante en que se levanta el bloqueo
bloqueados: Dict[str, float] = {}

app = FastAPI(
    title="Servicio inestable de sensores",
    description="Dependencia poco fiable para practicar timeouts, reintentos "
                "y respeto de Retry-After.",
    version="1.0",
)


def log(ip: str, metodo: str, ruta: str, resultado: str) -> None:
    hora = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{hora}] {ip:>15} {metodo} {ruta} -> {resultado}", flush=True)


# ---------------------------------------------------------------------------
# Inyección de fallos
# ---------------------------------------------------------------------------

@app.middleware("http")
async def inyectar_fallos(request: Request, call_next):
    ruta = request.url.path
    ip = request.client.host if request.client else "desconocido"
    metodo = request.method

    if not ruta.startswith("/sensores"):
        return await call_next(request)

    ahora = time.monotonic()

    # 1. Cliente que no respetó un Retry-After anterior
    hasta = bloqueados.get(ip)
    if hasta is not None and ahora < hasta:
        restante = max(1, int(hasta - ahora + 0.999))
        log(ip, metodo, ruta, f"429 (no respetó Retry-After, faltan {restante} s)")
        return JSONResponse(
            status_code=429,
            content={"detalle": "Demasiadas peticiones; respete Retry-After"},
            headers={"Retry-After": str(restante)},
        )

    sorteo = rng.random()

    # 2. Límite de peticiones simulado
    if sorteo < ARGS.tasa_429:
        bloqueados[ip] = ahora + ARGS.retry_after
        log(ip, metodo, ruta, f"429 (Retry-After: {ARGS.retry_after})")
        return JSONResponse(
            status_code=429,
            content={"detalle": "Demasiadas peticiones"},
            headers={"Retry-After": str(ARGS.retry_after)},
        )

    # 3. Fallo transitorio del servidor
    if sorteo < ARGS.tasa_429 + ARGS.tasa_fallos:
        log(ip, metodo, ruta, "503")
        return JSONResponse(
            status_code=503,
            content={"detalle": "Servicio no disponible temporalmente"},
        )

    # 4. Respuesta lenta
    if rng.random() < ARGS.tasa_lentitud:
        log(ip, metodo, ruta, f"lenta ({ARGS.latencia} s)")
        await asyncio.sleep(ARGS.latencia)

    respuesta = await call_next(request)
    log(ip, metodo, ruta, str(respuesta.status_code))
    return respuesta


# ---------------------------------------------------------------------------
# Cursores opacos para la paginación
# ---------------------------------------------------------------------------

def codificar_cursor(desplazamiento: int) -> str:
    return base64.urlsafe_b64encode(f"o:{desplazamiento}".encode()).decode()


def decodificar_cursor(cursor: str) -> int:
    try:
        texto = base64.urlsafe_b64decode(cursor.encode()).decode()
        prefijo, valor = texto.split(":")
        desplazamiento = int(valor)
        if prefijo != "o" or not 0 <= desplazamiento < len(SENSORES):
            raise ValueError
        return desplazamiento
    except Exception:
        raise HTTPException(status_code=400, detail="Cursor inválido")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/salud")
def salud():
    return {"estado": "ok"}


@app.get("/config")
def config():
    return {
        "tasa_fallos": ARGS.tasa_fallos,
        "tasa_429": ARGS.tasa_429,
        "tasa_lentitud": ARGS.tasa_lentitud,
        "latencia_s": ARGS.latencia,
        "retry_after_s": ARGS.retry_after,
        "semilla": ARGS.semilla,
        "tam_pagina": ARGS.tam_pagina,
        "num_sensores": len(SENSORES),
    }


@app.get("/sensores")
def listar_sensores(cursor: Optional[str] = None):
    inicio = 0 if cursor is None else decodificar_cursor(cursor)
    fin = inicio + ARGS.tam_pagina
    siguiente = codificar_cursor(fin) if fin < len(SENSORES) else None
    return {"sensores": SENSORES[inicio:fin], "siguiente": siguiente}


@app.get("/sensores/{sensor_id}/lectura")
def lectura(sensor_id: str):
    sensor = POR_ID.get(sensor_id)
    if sensor is None:
        raise HTTPException(status_code=404, detail=f"No existe el sensor {sensor_id}")
    unidad, minimo, maximo = TIPOS[sensor["tipo"]]
    return {
        "sensor_id": sensor_id,
        "tipo": sensor["tipo"],
        "valor": round(random.uniform(minimo, maximo), 1),
        "unidad": unidad,
        "marca_tiempo": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    print(f"Servicio inestable en http://{ARGS.host}:{ARGS.puerto}  "
          f"(documentación en /docs)")
    print(f"Parámetros: {config()}")
    uvicorn.run(app, host=ARGS.host, port=ARGS.puerto, log_level="warning")

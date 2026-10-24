"""Demostracion reproducible de POST /v1/alertas con idempotencia."""

import json
import os

import httpx


BASE_URL = os.environ.get("API_URL", "http://127.0.0.1:8000")
KEY = "evidencia-idempotencia-001"
BODY = {
    "sensor_id": "S-001",
    "tipo": "temperatura",
    "mensaje": "Umbral",
    "severidad": "alta",
}


def imprimir(nombre: str, respuesta: httpx.Response) -> None:
    print(f"{nombre}: HTTP {respuesta.status_code}")
    print(json.dumps(respuesta.json(), ensure_ascii=False, sort_keys=True))


def main() -> None:
    headers = {"Idempotency-Key": KEY}
    with httpx.Client(base_url=BASE_URL) as client:
        primera = client.post("/v1/alertas", json=BODY, headers=headers)
        segunda = client.post("/v1/alertas", json=BODY, headers=headers)
        diferente = client.post(
            "/v1/alertas",
            json={**BODY, "sensor_id": "S-002"},
            headers=headers,
        )

    imprimir("primera", primera)
    imprimir("segunda", segunda)
    imprimir("misma clave y cuerpo diferente", diferente)
    assert primera.status_code == segunda.status_code == 201
    assert primera.json() == segunda.json()
    assert diferente.status_code == 422
    print("RESULTADO: idempotencia verificada")


if __name__ == "__main__":
    main()

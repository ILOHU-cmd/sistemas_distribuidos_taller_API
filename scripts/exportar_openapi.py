"""Exporta el contrato OpenAPI a docs/openapi.json para compartirlo con otros grupos."""

import json
from pathlib import Path

from main import app

destino = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
destino.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Contrato exportado en {destino}")

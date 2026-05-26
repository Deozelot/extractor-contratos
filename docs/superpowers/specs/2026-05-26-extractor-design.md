# Design: Extractor de Obligaciones Contractuales — Demo Interkont
**Fecha:** 2026-05-26  
**Estado:** Aprobado  
**Contexto:** Demo técnico para entrevista laboral en Interkont. Live demo, fin de semana 2026-05-31/06-01. ~16 horas totales.

---

## Alcance

Este documento captura las **decisiones de diseño que difieren o completan el SPEC.md** existente. No duplica lo que ya está especificado ahí.

Scope del demo (confirmado alcanzable en 2 días):
- Upload PDF → extracción metadata + obligaciones via Claude → tabla editable → exportar JSON
- Sin autenticación, sin OCR, sin async (Celery/Redis), sin historial

---

## Arquitectura

```
PDF → pdfplumber (por página, preservando número)
    → Chunk(text, start_page, end_page)[]  [2000 tokens, overlap 200]
    → metadata extraction (primeras 3–5 páginas)
    → obligations extraction (todos los chunks, en paralelo conceptual)
    → parse_llm_response() con regex fallback
    → deduplicación simple
    → SQLite (extraction + contract_metadata + obligation)
    → FastAPI endpoints
    → React frontend (4 pantallas)
```

### Estructura de archivos (delta vs SPEC.md)

```
backend/
├── main.py           # FastAPI app + endpoints
├── extractor.py      # chunker + pipeline de extracción
├── llm_client.py     # NEW: Claude API calls aisladas (testeable sin API)
├── models.py         # Pydantic models + SQLite schema
├── prompts.py        # METADATA_SYSTEM_PROMPT + EXTRACTION_SYSTEM_PROMPT
└── requirements.txt

fixtures/
└── contrato_demo_result.json  # NEW: fallback de emergencia para demo en vivo

sample_contracts/
└── contrato_demo.pdf
```

`llm_client.py` separado de `extractor.py` para poder testear el pipeline sin llamadas reales a la API.

---

## Endpoints FastAPI

```
POST   /api/extractions                  # upload PDF, lanza extracción, retorna extraction_id
GET    /api/extractions/{id}             # estado + metadatos del contrato (polling cada 2s desde frontend)
PUT    /api/extractions/{id}/metadata    # actualizar metadatos confirmados por usuario
GET    /api/extractions/{id}/obligations # listar obligaciones
PUT    /api/obligations/{id}             # editar una obligación
DELETE /api/obligations/{id}             # rechazar/eliminar
POST   /api/extractions/{id}/export      # devuelve JSON descargable
GET    /api/demo                         # carga fixtures/contrato_demo_result.json (fallback)
```

---

## Prompts (backend/prompts.py)

### METADATA_SYSTEM_PROMPT

```python
METADATA_SYSTEM_PROMPT = """Eres un asistente especializado en contratos públicos colombianos (SECOP).

Extrae los metadatos del encabezado y cláusulas iniciales del contrato.

Responde ÚNICAMENTE con un objeto JSON con esta estructura exacta:
{
  "contract_number": "número o código del contrato, o null",
  "contracting_entity": "nombre de la entidad contratante",
  "contractor_name": "nombre o razón social del contratista",
  "contract_object": "objeto del contrato, máximo 200 caracteres",
  "total_value": "valor total con unidad monetaria (ej: $450.000.000 COP), o null",
  "start_date": "fecha de inicio o de firma, o null",
  "duration": "duración tal como aparece (ej: 6 meses, 180 días calendario), o null",
  "supervisor": "nombre del supervisor o interventor, o null"
}

Si un campo no aparece en el texto, usa null. No inventes información.
Responde ÚNICAMENTE con el objeto JSON. Sin texto antes ni después."""
```

### EXTRACTION_SYSTEM_PROMPT

Cambios vs el prompt original en SPEC.md:
- "Solo CONTRATISTA" movido al inicio (mayor peso)
- Contexto de páginas en el user message (no en el system prompt → cacheable)
- `{contractor_name}` inyectado en runtime
- Un ejemplo few-shot
- Instrucción explícita para cláusulas parciales

```python
EXTRACTION_SYSTEM_PROMPT = """Eres un asistente especializado en contratos públicos colombianos (SECOP).

REGLA PRINCIPAL: Extrae ÚNICAMENTE obligaciones del CONTRATISTA ({contractor_name}).
No extraigas obligaciones de la entidad contratante, el interventor, ni la supervisión.

EJEMPLO DE ENTRADA Y SALIDA ESPERADA:
Fragmento: "CLÁUSULA 12. El contratista deberá presentar un informe mensual de avance \
dentro de los primeros cinco (5) días hábiles de cada mes."

Salida:
[{{
  "obligation_type": "report",
  "description": "Presentar informe mensual de avance dentro de los primeros 5 días hábiles de cada mes",
  "responsible_party": "contratista",
  "deadline": "primeros 5 días hábiles de cada mes",
  "periodicity": "mensual",
  "source_clause": "Cláusula 12",
  "source_page": 8,
  "source_fragment": "El contratista deberá presentar un informe mensual de avance dentro de los primeros cinco (5) días hábiles de cada mes.",
  "confidence": "high"
}}]

TIPOS DE OBLIGACIÓN:
- deliverable: entrega de producto, bien, o resultado tangible
- report: informe, acta, certificado, o documento a presentar
- notification: comunicación o aviso requerido
- compliance: cumplimiento de norma, requisito legal, o estándar técnico
- penalty: multa, sanción, o consecuencia por incumplimiento

CRITERIOS DE CONFIANZA:
- high: obligación explícita con plazo y responsable claros
- medium: obligación clara pero con plazo o responsable ambiguo
- low: obligación inferida o texto ambiguo

INSTRUCCIONES:
1. Si una obligación parece comenzar antes del inicio de este fragmento (cláusula incompleta), extráela igual con confidence "medium".
2. Si el fragmento no contiene obligaciones del contratista, responde exactamente: []
3. Responde ÚNICAMENTE con el array JSON. Sin texto antes ni después del JSON."""
```

**User message por chunk** (lleva el contexto de páginas, varía por llamada):
```python
user_message = f"Fragmento del contrato (páginas {chunk.start_page}–{chunk.end_page}):\n\n{chunk.text}"
```

### Prompt caching

Como el system prompt es idéntico en todas las llamadas de un contrato, marcar como cacheable. El primer chunk paga tokens completos; los demás solo pagan el chunk text. Reduce latencia ~30% y costo ~80%.

```python
messages_payload = {
    "system": [{"type": "text", "text": prompt,
                 "cache_control": {"type": "ephemeral"}}],
    "messages": [{"role": "user", "content": user_message}]
}
```

---

## Parser LLM robusto

Nunca lanzar excepción desde el parser. Siempre retornar lista (vacía si falla).

```python
import re, json

def parse_llm_response(text: str) -> list[dict]:
    text = text.strip()
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                return result if isinstance(result, list) else []
            except json.JSONDecodeError:
                pass
    return []
```

---

## Deduplicación (simplificada vs SPEC)

El SPEC propone "80% similitud de tokens" — underspecified y costoso de implementar correctamente. Para el demo:

Deduplicar cuando dos obligaciones tienen `source_clause` idéntica (normalizada) Y los primeros 100 caracteres de `source_fragment` son iguales. Conservar la de mayor `confidence`.

```python
def deduplicate(obligations: list[dict]) -> list[dict]:
    seen = {}
    for ob in obligations:
        key = (
            (ob.get("source_clause") or "").strip().lower(),
            (ob.get("source_fragment") or "")[:100].strip()
        )
        existing = seen.get(key)
        if not existing:
            seen[key] = ob
        else:
            confidence_rank = {"high": 3, "medium": 2, "low": 1}
            if confidence_rank.get(ob["confidence"], 0) > confidence_rank.get(existing["confidence"], 0):
                seen[key] = ob
    return list(seen.values())
```

---

## Fixture de emergencia

Después de la primera extracción exitosa del PDF demo, guardar:

```python
import json
with open("fixtures/contrato_demo_result.json", "w") as f:
    json.dump({
        "metadata": metadata_dict,
        "obligations": obligations_list
    }, f, ensure_ascii=False, indent=2)
```

El endpoint `GET /api/demo` carga este fixture directamente, bypaseando el pipeline LLM. Si la API de Claude falla o tarda >45s durante el demo en vivo, se usa este endpoint.

---

## Frontend — pantallas y prioridades

4 pantallas: Upload → Metadatos → Revisión → Exportar.

Progress tracking: polling cada 2 segundos contra `GET /api/extractions/{id}`. El SPEC original menciona SSE pero polling tiene el mismo resultado visual y elimina complejidad de EventSource en el frontend. El backend expone un campo `chunks_processed` / `chunks_total` en el response para mostrar barra de progreso.

### Pantalla 3 — Tabla de obligaciones: prioridades

| Feature | Prioridad |
|---------|-----------|
| Tabla con columnas del SPEC | MUST |
| Chips confianza (verde/naranja/rojo) | MUST |
| Fila amarilla para `low` | MUST |
| Aprobar / rechazar por fila | MUST |
| "Aprobar todo" | MUST |
| Edición inline | MUST |
| Counter "X de Y aprobadas" | MUST |
| Tooltip `source_fragment` en hover | NICE |
| Filtros por tipo/confianza | CUT si falta tiempo |
| Agregar obligación manual | CUT si falta tiempo |

---

## Secuencia de implementación

### Día 1 (Sábado) — Backend y pipeline

| Hora | Tarea | Checkpoint |
|------|-------|------------|
| 1 | Setup: venv, deps, carpetas, test pdfplumber con PDF real | Texto legible en consola |
| 2 | `models.py` + `prompts.py` | — |
| 3 | `extractor.py`: chunker con `Chunk(text, start_page, end_page)` | Chunks del PDF en consola |
| 4 | `llm_client.py` + test real: metadata + primeros 3 chunks | **Extracción coherente confirmada** |
| 5 | Pipeline completo + deduplicación + guardar fixture | `fixtures/contrato_demo_result.json` existe |
| 6 | `main.py`: todos los endpoints | — |
| 7 | Test API completa con curl/httpie | Flujo upload→export funciona |
| 8 | Buffer: CORS, error PDF escaneado, CSV si sobra tiempo | — |

**Decisión crítica en Hora 4:** Si la extracción no produce obligaciones coherentes, diagnosticar aquí. No avanzar al frontend con un pipeline no validado.

### Día 2 (Domingo) — Frontend

| Hora | Tarea |
|------|-------|
| 1 | Vite + React + TS + Tailwind, `api.ts` tipado, routing |
| 2 | Pantalla 1: Upload + polling de estado |
| 3 | Pantalla 2: Metadatos editables |
| 4–5 | Pantalla 3: Tabla de obligaciones (2h) |
| 6 | Pantalla 4: Exportar |
| 7 | Test end-to-end 3 veces con PDF real |
| 8 | Buffer: fixes, preparación del demo |

---

## Scope mínimo para demo exitoso

Upload → extracción con cláusulas y páginas reales → editar una obligación inline → exportar JSON con `source_fragment` visible.

Todo lo demás (CSV, filtros, tooltip, agregar manual) es bonus.

---

## Riesgos mitigados

| Riesgo | Mitigación |
|--------|-----------|
| LLM retorna JSON malformado | `parse_llm_response()` con regex fallback, nunca lanza excepción |
| `source_page` incorrecto | `start_page`/`end_page` en cada user message |
| API lenta o caída en demo en vivo | `GET /api/demo` carga fixture pre-generado |
| PDF escaneado | Error claro en Hora 1, antes de escribir código |
| Latencia alta (>30s) | Prompt caching reduce ~30% latencia; polling muestra progreso |

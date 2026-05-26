# Extractor de Obligaciones Contractuales — Interkont
**Propósito de este proyecto:** Demo técnico para entrevista laboral en Interkont.  
**Objetivo del demo:** Demostrar la Propuesta E del portafolio de IA de Interkont implementada como prototipo funcional.

---

## Contexto de negocio

**Interkont** es una empresa colombiana de GovTech (~20 personas) que vende COBRA, una plataforma de monitoreo y supervisión de contratos públicos. Su producto COBRA BPM permite gestionar flujos de procesos contractuales.

**El problema que resuelve este sistema:**
Los contratos del Estado colombiano (publicados en SECOP) son PDFs de 50–200 páginas con estructura heterogénea. Cuando un cliente vincula un contrato a COBRA BPM, alguien debe leerlo completo, identificar manualmente cada obligación y registrarla como tarea. Eso toma entre 3 y 8 horas por contrato, genera omisiones y retrasa el inicio del monitoreo.

**La solución:** Un extractor inteligente que recibe el PDF, extrae todas las obligaciones contractuales con IA, las presenta para revisión humana, y las importa como tareas a COBRA BPM con trazabilidad completa al contrato fuente.

---

## Alcance del demo (NO el sistema completo)

Este es un demo para entrevista. El scope está deliberadamente reducido para ser construible en un fin de semana. Lo que SÍ debe funcionar:

| Feature | Incluido en demo |
|---------|-----------------|
| Carga de PDF (drag & drop) | ✅ |
| Extracción de texto del PDF (solo PDFs digitales) | ✅ |
| Extracción de obligaciones via LLM (Claude API) | ✅ |
| Tabla de revisión editable | ✅ |
| "Exportar" a JSON (simula importación a COBRA BPM) | ✅ |
| OCR para PDFs escaneados | ❌ (fuera del demo) |
| PostgreSQL + pgvector | ❌ (usar SQLite) |
| Celery + Redis (procesamiento async) | ❌ (todo síncrono) |
| Autenticación de usuarios | ❌ |
| Historial de extracciones | ❌ |

---

## Stack del demo

| Capa | Tecnología | Razón |
|------|-----------|-------|
| Backend | FastAPI (Python) | Stack nativo para IA, matching con la spec real |
| Base de datos | SQLite | Sin infraestructura, zero-config para demo |
| Extracción PDF | pdfplumber | Mejor que PyPDF2 para contratos con tablas |
| LLM | Claude claude-sonnet-4-20250514 via Anthropic SDK | Mejor en español jurídico colombiano |
| Frontend | React + TypeScript + Tailwind CSS | Stack moderno, limpio visualmente |
| Chunking | Implementación propia (solapamiento 200 tokens) | Crítico para contratos largos |

---

## Estructura de carpetas esperada

```
interkont-extractor/
├── CLAUDE.md              ← este archivo
├── SPEC.md                ← especificación del demo
├── TASKS.md               ← checklist de implementación (generado por /write-plan)
├── backend/
│   ├── main.py            ← FastAPI app
│   ├── extractor.py       ← lógica de extracción (pdfplumber + chunking + LLM)
│   ├── models.py          ← modelos Pydantic y SQLite schema
│   ├── prompts.py         ← system prompt del extractor (separado del código)
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── UploadZone.tsx      ← drag & drop PDF
│   │   │   ├── MetadataReview.tsx  ← confirmación de metadatos del contrato
│   │   │   ├── ObligationsTable.tsx ← tabla editable de obligaciones
│   │   │   └── ExportButton.tsx    ← exportar a JSON
│   │   └── types.ts
│   ├── package.json
│   └── vite.config.ts
└── sample_contracts/      ← contratos SECOP reales para demo
    └── README.md          ← instrucciones para descargar contratos de SECOP
```

---

## Modelo de datos (SQLite para el demo)

### `extraction` (sesión de extracción)
```sql
id TEXT PRIMARY KEY,
file_name TEXT,
file_size_kb INTEGER,
page_count INTEGER,
status TEXT,  -- processing | pending_review | completed | failed
model_used TEXT,
tokens_consumed INTEGER,
created_at TEXT,
completed_at TEXT
```

### `contract_metadata` (metadatos del contrato)
```sql
extraction_id TEXT,
contract_number TEXT,
contracting_entity TEXT,
contractor_name TEXT,
contract_object TEXT,
total_value TEXT,
start_date TEXT,
duration TEXT,
supervisor TEXT,
confirmed_by_user INTEGER DEFAULT 0
```

### `obligation` (obligaciones extraídas)
```sql
id TEXT PRIMARY KEY,
extraction_id TEXT,
obligation_type TEXT,  -- deliverable | report | notification | compliance | penalty
description TEXT,
responsible_party TEXT,
deadline TEXT,
periodicity TEXT,
source_clause TEXT,
source_page INTEGER,
source_fragment TEXT,
confidence TEXT,  -- high | medium | low
review_status TEXT  -- pending | approved | edited | rejected
```

---

## Decisiones de diseño críticas

1. **Chunking con solapamiento de 200 tokens:** No enviar el contrato completo al LLM. Dividir en chunks de ~2000 tokens con 200 de solapamiento. Un contrato de 100 páginas enviado en un solo prompt costaría ~10x más y degradaría precisión.

2. **System prompt separado del código:** El prompt de extracción vive en `prompts.py`, no hardcodeado en la lógica. Esto permite ajustarlo sin tocar el pipeline.

3. **Respuesta del LLM siempre en JSON:** El LLM debe retornar un array JSON de obligaciones. Usar `response_format` de la API o instrucción explícita en el prompt. Parsear con try/except robusto.

4. **Nivel de confianza obligatorio:** Cada obligación debe tener `confidence: high | medium | low`. Las de confianza baja se destacan visualmente en la tabla para que el revisor las priorice.

5. **Fragmento original en la obligación:** Siempre incluir el fragmento literal del contrato del que se extrajo la obligación (máx 300 chars). Es el diferenciador clave de trazabilidad.

---

## Variables de entorno requeridas

```bash
ANTHROPIC_API_KEY=sk-ant-...   # Requerida — obtenida de console.anthropic.com
```

---

## Lo que hace impresionante al demo en la entrevista

1. Subir un contrato real de SECOP (descargable públicamente en secop.gov.co)
2. Ver la tabla de obligaciones generada automáticamente con cláusulas, plazos y responsables reales
3. Editar una obligación inline y mostrar que el cambio persiste
4. Exportar el JSON y mostrar que tiene el fragmento original del contrato
5. Señalar la arquitectura en SPEC.md y explicar cómo escalaría a producción

---

## Comandos de inicio

```bash
# Backend
cd backend && pip install -r requirements.txt && uvicorn main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

---

## Convenciones de código

- Python: type hints en todas las funciones, docstrings en funciones públicas
- TypeScript: interfaces explícitas para todos los tipos de datos
- Commits: `feat:`, `fix:`, `refactor:` prefijos
- No usar `any` en TypeScript
- Manejo de errores explícito — no `except: pass` en Python

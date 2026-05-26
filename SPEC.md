# SPEC.md — Extractor de Obligaciones Contractuales (Demo)
**Versión:** Demo v1.0  
**Fecha:** Mayo 2026  
**Contexto:** Ver CLAUDE.md para contexto completo del proyecto

---

## Problema

Los contratos del Estado colombiano son PDFs de 50–200 páginas con estructura heterogénea. La carga manual de obligaciones a COBRA BPM tarda 3–8 horas por contrato, genera omisiones, y retrasa el inicio del monitoreo.

---

## Solución

Sistema de extracción automática con IA que:
1. Recibe un contrato PDF
2. Extrae el texto por chunks
3. Usa Claude para identificar y clasificar obligaciones
4. Presenta los resultados en una tabla editable para revisión humana
5. Exporta las obligaciones aprobadas a JSON (simula importación a COBRA BPM)

---

## Flujo de usuario (pantalla a pantalla)

### Pantalla 1: Upload
- Zona de drag & drop para PDF
- Validación: solo PDF, máx 50MB
- Al soltar el archivo → spinner con estado "Extrayendo texto..."  → "Analizando con IA..." → "Listo"
- Si el procesamiento tarda > 10s, mostrar barra de progreso con chunks procesados

### Pantalla 2: Confirmar metadatos
- Tabla con los metadatos detectados automáticamente:
  - Número de contrato
  - Entidad contratante
  - Contratista
  - Objeto del contrato
  - Valor total
  - Fecha de inicio / duración
  - Supervisor/interventor
- El usuario puede editar cualquier campo
- Botón "Confirmar y continuar"

### Pantalla 3: Revisión de obligaciones
- Tabla con todas las obligaciones extraídas
- Columnas: Tipo | Descripción | Responsable | Plazo | Cláusula | Confianza | Acciones
- Confianza `low` → fila destacada en amarillo
- Confianza `high` → chip verde, `medium` → chip naranja, `low` → chip rojo
- Acciones por fila: Editar (inline) | Aprobar | Rechazar
- Botón "Aprobar todo" para aprobar en bloque
- Botón "Agregar obligación" para agregar manualmente
- Contador: "X de Y obligaciones aprobadas"
- Panel lateral (o tooltip en hover) que muestra el `source_fragment` del contrato

### Pantalla 4: Exportar
- Resumen: N obligaciones aprobadas, N rechazadas
- Botón "Exportar JSON (COBRA BPM)" → descarga `obligaciones_{contrato}.json`
- Botón "Exportar CSV" → para mostrar versatilidad
- Mensaje de éxito: "Listo para importar a COBRA BPM"

---

## Requerimientos funcionales del demo

### RF-01 — Carga de PDF
- Drag & drop + click para seleccionar
- Validación de tipo (solo PDF) y tamaño (máx 50MB)
- Feedback visual durante procesamiento

### RF-02 — Extracción de texto
- Usar `pdfplumber` para extraer texto página por página
- Preservar número de página para cada fragmento
- Si el PDF no tiene texto extraíble, mostrar error claro: "Este PDF parece escaneado. El demo solo soporta PDFs digitales."

### RF-03 — Identificación de metadatos
- Llamada al LLM con el primer chunk del contrato (primeras 3–5 páginas)
- Extraer: número de contrato, entidad, contratista, objeto, valor, fechas, supervisor
- Presentar para confirmación del usuario antes de continuar

### RF-04 — Extracción de obligaciones
**Chunking:**
- Dividir el texto en chunks de 2000 tokens con solapamiento de 200 tokens
- Procesar cada chunk con una llamada separada al LLM
- Consolidar y deduplicar resultados de todos los chunks

**Por cada obligación extraer:**
| Campo | Descripción |
|-------|-------------|
| `id` | UUID generado |
| `obligation_type` | `deliverable` / `report` / `notification` / `compliance` / `penalty` |
| `description` | Descripción resumida en lenguaje natural |
| `responsible_party` | `contratista` / `interventor` / `entidad` |
| `deadline` | Texto libre (ej: "30 días hábiles desde inicio") |
| `periodicity` | Si aplica (mensual, trimestral, etc.) |
| `source_clause` | Número de cláusula |
| `source_page` | Número de página |
| `source_fragment` | Texto literal del contrato, máx 300 chars |
| `confidence` | `high` / `medium` / `low` |

**Deduplicación simple para el demo:**
- Si dos obligaciones extraídas de chunks diferentes tienen `source_clause` idéntica y `description` con >80% similitud de tokens, conservar la de mayor `confidence`

### RF-05 — Tabla de revisión
- Edición inline de todos los campos
- Aprobar / rechazar por fila
- Aprobar todo en bloque
- Agregar obligación manual
- Filtrar por tipo, confianza, estado de revisión
- Highlight de confianza baja

### RF-06 — Exportación
- JSON con array de obligaciones aprobadas + metadatos del contrato
- CSV de obligaciones aprobadas
- Nombre del archivo: `obligaciones_{numero_contrato}_{fecha}.json`

---

## System prompt del extractor (base)

Este prompt vive en `backend/prompts.py`. Claude Code debe usarlo exactamente como está, solo puede mejorarlo iterando en los tests.

```
Eres un asistente especializado en contratos públicos colombianos. Tu tarea es identificar y extraer TODAS las obligaciones del contratista del fragmento de contrato que se te proporciona.

Para cada obligación encontrada, responde ÚNICAMENTE con un array JSON válido. No incluyas texto antes ni después del JSON.

Cada obligación debe tener esta estructura exacta:
{
  "obligation_type": "deliverable|report|notification|compliance|penalty",
  "description": "descripción clara y concisa de la obligación",
  "responsible_party": "contratista|interventor|entidad",
  "deadline": "plazo exacto como aparece en el contrato, o null",
  "periodicity": "periodicidad si aplica, o null",
  "source_clause": "número o nombre de la cláusula de origen",
  "source_page": número de página (integer),
  "source_fragment": "fragmento literal del contrato, máximo 300 caracteres",
  "confidence": "high|medium|low"
}

Criterios de confianza:
- high: obligación explícita con plazo y responsable claros
- medium: obligación clara pero con plazo o responsable ambiguo
- low: obligación inferida o texto ambiguo

Si el fragmento no contiene obligaciones del contratista, responde con un array vacío: []

IMPORTANTE: Solo extrae obligaciones del CONTRATISTA, no de la entidad contratante.
```

---

## Criterios de aceptación del demo

| ID | Criterio |
|----|----------|
| CA-01 | PDF de 20 páginas procesado en menos de 30 segundos |
| CA-02 | Cada obligación tiene `source_clause` y `source_page` poblados |
| CA-03 | Obligaciones de confianza `low` se destacan visualmente |
| CA-04 | El usuario puede editar, aprobar y rechazar obligaciones |
| CA-05 | El JSON exportado incluye el `source_fragment` de cada obligación |
| CA-06 | El sistema maneja gracefully un PDF sin texto extraíble |
| CA-07 | La UI funciona sin errores de consola en Chrome |

---

## Contrato de prueba recomendado

Para el demo, usar un contrato real de SECOP II. Instrucciones para descargarlo:

1. Ir a https://community.secop.gov.co/Public/Tendering/ContractNoticeManagement/Index
2. Buscar por objeto: "interventoría" o "supervisión de obras"  
3. Filtrar por estado: "Celebrado"
4. Descargar el PDF del contrato (típicamente en la pestaña "Documentos")
5. Colocar en `sample_contracts/contrato_demo.pdf`

Alternativamente, cualquier contrato público del SECOP sirve para el demo.

---

## Lo que NO implementar en el demo

Para no perder tiempo del fin de semana:
- ❌ Autenticación / login
- ❌ Múltiples usuarios
- ❌ OCR (Tesseract) — solo PDFs digitales
- ❌ Procesamiento asíncrono (Celery/Redis) — todo síncrono con streaming de progreso via SSE
- ❌ PostgreSQL — usar SQLite
- ❌ Historial de extracciones (más de una sesión)
- ❌ Tests unitarios extensos — solo los críticos del pipeline de extracción

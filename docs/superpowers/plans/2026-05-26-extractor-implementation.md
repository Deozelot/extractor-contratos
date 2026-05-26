# Extractor de Obligaciones Contractuales — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working demo that uploads a Colombian government contract PDF, extracts obligations via Claude API, presents them in an editable table, and exports them as JSON.

**Architecture:** Pipeline-first. Day 1 builds and validates the full extraction pipeline against the real `contrato_demo.pdf` before writing a single line of frontend code. Day 2 builds the React UI on top of a verified backend. Critical checkpoint at Task 7: if extraction doesn't produce coherent obligations, stop and diagnose before continuing.

**Tech Stack:** FastAPI, SQLite (sqlite3), pdfplumber, anthropic SDK (prompt caching), React 18, TypeScript, Tailwind CSS, Vite, axios.

---

## File Map

```
backend/
├── requirements.txt
├── database.py       # SQLite connection + schema init + CRUD helpers
├── models.py         # Pydantic request/response schemas
├── prompts.py        # METADATA_SYSTEM_PROMPT + EXTRACTION_SYSTEM_PROMPT
├── llm_client.py     # Claude API calls + parse_llm_response + parse_metadata_response
├── extractor.py      # extract_pages, make_chunks, deduplicate, run_extraction pipeline
└── main.py           # FastAPI app + all endpoints

tests/
├── test_parsers.py   # parse_llm_response + parse_metadata_response (pure functions)
└── test_chunker.py   # make_chunks (pure function)

fixtures/
└── contrato_demo_result.json   # generated during Task 8, used as live-demo fallback

sample_contracts/
└── contrato_demo.pdf           # already exists

frontend/
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── index.html
└── src/
    ├── types.ts                # TypeScript interfaces
    ├── api.ts                  # typed axios client
    ├── App.tsx                 # routing (react-router-dom)
    └── components/
        ├── UploadZone.tsx      # Pantalla 1: drag & drop + polling progress
        ├── MetadataReview.tsx  # Pantalla 2: editable metadata fields
        ├── ObligationsTable.tsx # Pantalla 3: editable table, approve/reject
        └── ExportScreen.tsx    # Pantalla 4: export JSON + CSV
```

---

## DAY 1 — Backend

---

### Task 1: Environment setup + PDF validation

**Files:**
- Create: `backend/requirements.txt`
- Create: `.env`
- Create: `sample_contracts/` (if missing)

- [ ] **Step 1: Create requirements.txt**

```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
pdfplumber>=0.11.0
anthropic>=0.27.0
python-dotenv>=1.0.0
python-multipart>=0.0.9
```

- [ ] **Step 2: Create virtualenv and install**

```bash
cd /home/deozelot/Dev/Laboral/Interkont/extractor-contratos
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
```

Expected: All packages install without errors. `anthropic` version ≥ 0.27.0 required for prompt caching.

- [ ] **Step 3: Create .env**

```bash
cat > .env << 'EOF'
ANTHROPIC_API_KEY=your-key-here
DEMO_MODE=false
EOF
```

Add `.env` to `.gitignore`:
```bash
echo ".env" >> .gitignore
echo "extractor.db" >> .gitignore
echo "/tmp/*.pdf" >> .gitignore
```

- [ ] **Step 4: Validate pdfplumber against the real PDF**

```bash
python - << 'EOF'
import pdfplumber
with pdfplumber.open("sample_contracts/contrato_demo.pdf") as pdf:
    print(f"Páginas: {len(pdf.pages)}")
    text = pdf.pages[0].extract_text()
    print(f"Primeros 500 chars de página 1:\n{text[:500] if text else 'VACÍO — PDF puede ser escaneado'}")
EOF
```

**Expected:** Página count > 0, texto legible en español visible. If output is "VACÍO", the PDF is scanned — stop here, download a different contract from SECOP.

- [ ] **Step 5: Commit setup**

```bash
git add backend/requirements.txt .gitignore
git commit -m "chore: backend setup and deps"
```

---

### Task 2: database.py

**Files:**
- Create: `backend/database.py`

- [ ] **Step 1: Write database.py**

```python
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Generator

DATABASE_PATH = "extractor.db"


def init_db() -> None:
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS extraction (
                id TEXT PRIMARY KEY,
                file_name TEXT,
                file_size_kb INTEGER,
                page_count INTEGER DEFAULT 0,
                status TEXT,
                model_used TEXT,
                tokens_consumed INTEGER DEFAULT 0,
                chunks_processed INTEGER DEFAULT 0,
                chunks_total INTEGER DEFAULT 0,
                created_at TEXT,
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contract_metadata (
                extraction_id TEXT PRIMARY KEY,
                contract_number TEXT,
                contracting_entity TEXT,
                contractor_name TEXT,
                contract_object TEXT,
                total_value TEXT,
                start_date TEXT,
                duration TEXT,
                supervisor TEXT,
                confirmed_by_user INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS obligation (
                id TEXT PRIMARY KEY,
                extraction_id TEXT,
                obligation_type TEXT,
                description TEXT,
                responsible_party TEXT,
                deadline TEXT,
                periodicity TEXT,
                source_clause TEXT,
                source_page INTEGER,
                source_fragment TEXT,
                confidence TEXT,
                review_status TEXT DEFAULT 'pending'
            )
        """)
        conn.commit()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_extraction(conn: sqlite3.Connection, extraction_id: str, file_name: str, file_size_kb: int) -> None:
    conn.execute(
        """INSERT INTO extraction (id, file_name, file_size_kb, status, model_used, created_at)
           VALUES (?, ?, ?, 'processing', 'claude-sonnet-4-20250514', ?)""",
        (extraction_id, file_name, file_size_kb, datetime.utcnow().isoformat()),
    )


def update_extraction(conn: sqlite3.Connection, extraction_id: str, **kwargs) -> None:
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [extraction_id]
    conn.execute(f"UPDATE extraction SET {sets} WHERE id = ?", values)


def get_extraction(conn: sqlite3.Connection, extraction_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM extraction WHERE id = ?", (extraction_id,)).fetchone()


def save_metadata(conn: sqlite3.Connection, extraction_id: str, data: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO contract_metadata
           (extraction_id, contract_number, contracting_entity, contractor_name,
            contract_object, total_value, start_date, duration, supervisor)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            extraction_id,
            data.get("contract_number"),
            data.get("contracting_entity"),
            data.get("contractor_name"),
            data.get("contract_object"),
            data.get("total_value"),
            data.get("start_date"),
            data.get("duration"),
            data.get("supervisor"),
        ),
    )


def get_metadata(conn: sqlite3.Connection, extraction_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM contract_metadata WHERE extraction_id = ?", (extraction_id,)
    ).fetchone()


def save_obligations(conn: sqlite3.Connection, extraction_id: str, obligations: list[dict]) -> None:
    import uuid
    for ob in obligations:
        conn.execute(
            """INSERT INTO obligation
               (id, extraction_id, obligation_type, description, responsible_party,
                deadline, periodicity, source_clause, source_page, source_fragment, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                extraction_id,
                ob.get("obligation_type", "compliance"),
                ob.get("description", ""),
                ob.get("responsible_party", "contratista"),
                ob.get("deadline"),
                ob.get("periodicity"),
                ob.get("source_clause"),
                ob.get("source_page"),
                ob.get("source_fragment"),
                ob.get("confidence", "medium"),
            ),
        )


def get_obligations(conn: sqlite3.Connection, extraction_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM obligation WHERE extraction_id = ? ORDER BY source_page, source_clause",
        (extraction_id,),
    ).fetchall()


def update_obligation(conn: sqlite3.Connection, obligation_id: str, **kwargs) -> None:
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [obligation_id]
    conn.execute(f"UPDATE obligation SET {sets} WHERE id = ?", values)


def delete_obligation(conn: sqlite3.Connection, obligation_id: str) -> None:
    conn.execute("DELETE FROM obligation WHERE id = ?", (obligation_id,))
```

- [ ] **Step 2: Smoke test DB init**

```bash
cd /home/deozelot/Dev/Laboral/Interkont/extractor-contratos
python - << 'EOF'
import sys; sys.path.insert(0, "backend")
from database import init_db, get_db, create_extraction, get_extraction
init_db()
with get_db() as conn:
    create_extraction(conn, "test-id", "test.pdf", 100)
    row = get_extraction(conn, "test-id")
    print(f"Created: {dict(row)}")
print("DB OK")
EOF
```

Expected: prints dict with status='processing', then "DB OK".

- [ ] **Step 3: Commit**

```bash
git add backend/database.py
git commit -m "feat: SQLite schema and CRUD helpers"
```

---

### Task 3: models.py and prompts.py

**Files:**
- Create: `backend/models.py`
- Create: `backend/prompts.py`

- [ ] **Step 1: Write models.py**

```python
from pydantic import BaseModel
from typing import Literal, Optional


class ExtractionResponse(BaseModel):
    id: str
    file_name: str
    status: Literal["processing", "pending_review", "completed", "failed"]
    page_count: int
    chunks_processed: int
    chunks_total: int
    created_at: str
    completed_at: Optional[str]
    metadata: Optional["MetadataResponse"] = None


class MetadataResponse(BaseModel):
    extraction_id: str
    contract_number: Optional[str]
    contracting_entity: Optional[str]
    contractor_name: Optional[str]
    contract_object: Optional[str]
    total_value: Optional[str]
    start_date: Optional[str]
    duration: Optional[str]
    supervisor: Optional[str]
    confirmed_by_user: bool


class MetadataUpdate(BaseModel):
    contract_number: Optional[str] = None
    contracting_entity: Optional[str] = None
    contractor_name: Optional[str] = None
    contract_object: Optional[str] = None
    total_value: Optional[str] = None
    start_date: Optional[str] = None
    duration: Optional[str] = None
    supervisor: Optional[str] = None
    confirmed_by_user: Optional[bool] = None


class ObligationResponse(BaseModel):
    id: str
    extraction_id: str
    obligation_type: Literal["deliverable", "report", "notification", "compliance", "penalty"]
    description: str
    responsible_party: Literal["contratista", "interventor", "entidad"]
    deadline: Optional[str]
    periodicity: Optional[str]
    source_clause: Optional[str]
    source_page: Optional[int]
    source_fragment: Optional[str]
    confidence: Literal["high", "medium", "low"]
    review_status: Literal["pending", "approved", "edited", "rejected"]


class ObligationUpdate(BaseModel):
    obligation_type: Optional[Literal["deliverable", "report", "notification", "compliance", "penalty"]] = None
    description: Optional[str] = None
    responsible_party: Optional[Literal["contratista", "interventor", "entidad"]] = None
    deadline: Optional[str] = None
    periodicity: Optional[str] = None
    source_clause: Optional[str] = None
    source_page: Optional[int] = None
    source_fragment: Optional[str] = None
    confidence: Optional[Literal["high", "medium", "low"]] = None
    review_status: Optional[Literal["pending", "approved", "edited", "rejected"]] = None
```

- [ ] **Step 2: Write prompts.py**

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

- [ ] **Step 3: Commit**

```bash
git add backend/models.py backend/prompts.py
git commit -m "feat: Pydantic models and LLM system prompts"
```

---

### Task 4: llm_client.py + parser tests

**Files:**
- Create: `backend/llm_client.py`
- Create: `tests/test_parsers.py`

- [ ] **Step 1: Write llm_client.py**

```python
import json
import os
import re
from dataclasses import dataclass

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-20250514"
_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


@dataclass
class Chunk:
    text: str
    start_page: int
    end_page: int


def parse_llm_response(text: str) -> list[dict]:
    """Parse LLM output to list of obligation dicts. Never raises — returns [] on failure."""
    text = text.strip()
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                return result if isinstance(result, list) else []
            except json.JSONDecodeError:
                pass
    return []


def parse_metadata_response(text: str) -> dict:
    """Parse LLM output to metadata dict. Never raises — returns {} on failure."""
    text = text.strip()
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                return result if isinstance(result, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


def extract_metadata(text: str) -> dict:
    """Call Claude to extract contract metadata from first pages text."""
    from prompts import METADATA_SYSTEM_PROMPT

    response = get_client().messages.create(
        model=MODEL,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": METADATA_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": text}],
    )
    return parse_metadata_response(response.content[0].text)


def extract_obligations(chunk: Chunk, prompt: str) -> list[dict]:
    """Call Claude to extract obligations from one chunk. Returns list of obligation dicts."""
    user_message = (
        f"Fragmento del contrato (páginas {chunk.start_page}–{chunk.end_page}):\n\n{chunk.text}"
    )
    response = get_client().messages.create(
        model=MODEL,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    )
    obligations = parse_llm_response(response.content[0].text)
    for ob in obligations:
        if not ob.get("source_page"):
            ob["source_page"] = chunk.start_page
    return obligations
```

- [ ] **Step 2: Write tests/test_parsers.py**

```python
import sys
sys.path.insert(0, "backend")

from llm_client import parse_llm_response, parse_metadata_response


def test_parse_valid_json_array():
    text = '[{"obligation_type": "report", "description": "Presentar informe"}]'
    result = parse_llm_response(text)
    assert len(result) == 1
    assert result[0]["obligation_type"] == "report"


def test_parse_json_with_preamble():
    text = 'Aquí están las obligaciones:\n[{"obligation_type": "compliance", "description": "Cumplir norma"}]'
    result = parse_llm_response(text)
    assert len(result) == 1


def test_parse_empty_array():
    result = parse_llm_response("[]")
    assert result == []


def test_parse_empty_array_with_text():
    result = parse_llm_response("No encontré obligaciones en este fragmento: []")
    assert result == []


def test_parse_invalid_json_returns_empty():
    result = parse_llm_response("Esto no es JSON válido {broken")
    assert result == []


def test_parse_dict_instead_of_list_returns_empty():
    result = parse_llm_response('{"obligation_type": "report"}')
    assert result == []


def test_parse_metadata_valid():
    text = '{"contract_number": "CT-001", "contracting_entity": "Alcaldía", "contractor_name": "Empresa SAS"}'
    result = parse_metadata_response(text)
    assert result["contract_number"] == "CT-001"
    assert result["contractor_name"] == "Empresa SAS"


def test_parse_metadata_with_preamble():
    text = 'Los metadatos son:\n{"contract_number": "CT-002", "contracting_entity": "Gobernación"}'
    result = parse_metadata_response(text)
    assert result["contract_number"] == "CT-002"


def test_parse_metadata_invalid_returns_empty():
    result = parse_metadata_response("texto sin JSON")
    assert result == {}
```

- [ ] **Step 3: Run parser tests**

```bash
cd /home/deozelot/Dev/Laboral/Interkont/extractor-contratos
python -m pytest tests/test_parsers.py -v
```

Expected:
```
tests/test_parsers.py::test_parse_valid_json_array PASSED
tests/test_parsers.py::test_parse_json_with_preamble PASSED
tests/test_parsers.py::test_parse_empty_array PASSED
tests/test_parsers.py::test_parse_empty_array_with_text PASSED
tests/test_parsers.py::test_parse_invalid_json_returns_empty PASSED
tests/test_parsers.py::test_parse_dict_instead_of_list_returns_empty PASSED
tests/test_parsers.py::test_parse_metadata_valid PASSED
tests/test_parsers.py::test_parse_metadata_with_preamble PASSED
tests/test_parsers.py::test_parse_metadata_invalid_returns_empty PASSED
9 passed
```

- [ ] **Step 4: Commit**

```bash
git add backend/llm_client.py tests/test_parsers.py
git commit -m "feat: LLM client with robust JSON parsers"
```

---

### Task 5: extractor.py — chunker + tests

**Files:**
- Create: `backend/extractor.py` (chunker only for now)
- Create: `tests/test_chunker.py`

- [ ] **Step 1: Write the chunker portion of extractor.py**

```python
import os
import json
import pdfplumber
from dataclasses import dataclass
from datetime import datetime
from llm_client import Chunk


@dataclass
class PageText:
    page_num: int
    text: str


def extract_pages(pdf_path: str) -> list[PageText]:
    """Extract text from each page. Returns only pages with non-empty text."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(PageText(page_num=i, text=text))
    return pages


def make_chunks(pages: list[PageText], chunk_tokens: int = 2000, overlap_tokens: int = 200) -> list[Chunk]:
    """Split pages into overlapping chunks. Token approximation: 1 token ≈ 4 chars."""
    if not pages:
        return []

    chunk_chars = chunk_tokens * 4
    overlap_chars = overlap_tokens * 4

    # Build flat text with page boundary tracking
    full_text = ""
    page_boundaries: list[tuple[int, int, int]] = []  # (start_char, end_char, page_num)
    pos = 0
    for page in pages:
        text = page.text + "\n"
        page_boundaries.append((pos, pos + len(text), page.page_num))
        full_text += text
        pos += len(text)

    def char_to_page(char_idx: int) -> int:
        char_idx = max(0, min(char_idx, len(full_text) - 1))
        for start, end, page_num in page_boundaries:
            if start <= char_idx < end:
                return page_num
        return page_boundaries[-1][2]

    chunks: list[Chunk] = []
    start = 0
    while start < len(full_text):
        end = min(start + chunk_chars, len(full_text))

        # Try to break at paragraph boundary (avoid splitting mid-sentence)
        if end < len(full_text):
            break_pos = full_text.rfind("\n\n", start, end)
            if break_pos > start + overlap_chars:
                end = break_pos + 2

        chunk_text = full_text[start:end].strip()
        if chunk_text:
            chunks.append(
                Chunk(
                    text=chunk_text,
                    start_page=char_to_page(start),
                    end_page=char_to_page(end - 1),
                )
            )

        next_start = end - overlap_chars
        if next_start <= start:
            next_start = end
        start = next_start

    return chunks
```

- [ ] **Step 2: Write tests/test_chunker.py**

```python
import sys
sys.path.insert(0, "backend")

from extractor import PageText, make_chunks
from llm_client import Chunk


def _make_pages(texts: list[str]) -> list[PageText]:
    return [PageText(page_num=i + 1, text=t) for i, t in enumerate(texts)]


def test_single_short_page_is_one_chunk():
    pages = _make_pages(["Texto corto de prueba."])
    chunks = make_chunks(pages, chunk_tokens=500)
    assert len(chunks) == 1
    assert "Texto corto" in chunks[0].text
    assert chunks[0].start_page == 1
    assert chunks[0].end_page == 1


def test_chunk_count_scales_with_text_length():
    # 3000-token text with 1000-token chunks and 100-token overlap → 3-4 chunks
    long_text = "A" * (3000 * 4)
    pages = _make_pages([long_text])
    chunks = make_chunks(pages, chunk_tokens=1000, overlap_tokens=100)
    assert len(chunks) >= 3


def test_overlap_means_adjacent_chunks_share_text():
    long_text = "palabra " * 3000  # ~12000 chars
    pages = _make_pages([long_text])
    chunks = make_chunks(pages, chunk_tokens=500, overlap_tokens=100)
    assert len(chunks) >= 2
    # End of chunk 0 should appear in start of chunk 1
    end_of_first = chunks[0].text[-200:]
    start_of_second = chunks[1].text[:200]
    # They share some content
    shared = set(end_of_first.split()) & set(start_of_second.split())
    assert len(shared) > 0


def test_page_numbers_tracked_across_pages():
    pages = _make_pages(["Página uno. " * 200, "Página dos. " * 200, "Página tres. " * 200])
    chunks = make_chunks(pages, chunk_tokens=500, overlap_tokens=50)
    assert chunks[0].start_page == 1
    assert chunks[-1].end_page == 3


def test_empty_pages_returns_no_chunks():
    pages = _make_pages([])
    assert make_chunks(pages) == []


def test_pages_with_only_whitespace_excluded():
    from extractor import extract_pages
    # We can't test extract_pages without a real PDF, but we can verify
    # that make_chunks handles PageText with empty text gracefully
    pages = [PageText(page_num=1, text="   \n  ")]
    # make_chunks strips text, empty chunk not added
    chunks = make_chunks(pages)
    assert all(c.text.strip() != "" for c in chunks)
```

- [ ] **Step 3: Run chunker tests**

```bash
python -m pytest tests/test_chunker.py -v
```

Expected: 6 passed.

- [ ] **Step 4: Verify chunker on real PDF**

```bash
python - << 'EOF'
import sys; sys.path.insert(0, "backend")
from extractor import extract_pages, make_chunks

pages = extract_pages("sample_contracts/contrato_demo.pdf")
print(f"Páginas con texto: {len(pages)}")

chunks = make_chunks(pages)
print(f"Chunks generados: {len(chunks)}")
for i, c in enumerate(chunks[:3]):
    print(f"\nChunk {i+1} (páginas {c.start_page}–{c.end_page}, ~{len(c.text)//4} tokens):")
    print(c.text[:200])
    print("...")
EOF
```

Expected: chunks list with pages tracked, Spanish legal text visible in first 200 chars.

- [ ] **Step 5: Commit**

```bash
git add backend/extractor.py tests/test_chunker.py
git commit -m "feat: PDF extractor and chunker with page tracking"
```

---

### Task 6: extractor.py — deduplication + pipeline

**Files:**
- Modify: `backend/extractor.py` (add deduplicate + run_extraction)

- [ ] **Step 1: Add deduplicate to extractor.py**

Add this function after `make_chunks`:

```python
def deduplicate(obligations: list[dict]) -> list[dict]:
    """Remove duplicate obligations. Keep highest-confidence version."""
    CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}
    seen: dict[tuple, dict] = {}
    for ob in obligations:
        key = (
            (ob.get("source_clause") or "").strip().lower(),
            (ob.get("source_fragment") or "")[:100].strip(),
        )
        existing = seen.get(key)
        if not existing:
            seen[key] = ob
        else:
            if CONFIDENCE_RANK.get(ob.get("confidence", "low"), 0) > CONFIDENCE_RANK.get(
                existing.get("confidence", "low"), 0
            ):
                seen[key] = ob
    return list(seen.values())
```

- [ ] **Step 2: Add run_extraction to extractor.py**

Add these imports at the top of extractor.py:

```python
import json
import os
from datetime import datetime
```

Add this function at the bottom of extractor.py:

```python
def run_extraction(extraction_id: str, pdf_path: str) -> None:
    """Full extraction pipeline. Runs in background thread. Updates DB throughout."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from database import get_db, update_extraction, save_metadata, save_obligations
    from llm_client import extract_metadata as llm_extract_metadata
    from llm_client import extract_obligations as llm_extract_obligations
    from prompts import EXTRACTION_SYSTEM_PROMPT

    try:
        pages = extract_pages(pdf_path)
        if not pages:
            with get_db() as conn:
                update_extraction(conn, extraction_id, status="failed")
            return

        chunks = make_chunks(pages)
        with get_db() as conn:
            update_extraction(
                conn,
                extraction_id,
                page_count=len(pages),
                chunks_total=len(chunks),
                status="processing",
            )

        # Extract metadata from first 5 pages
        metadata_text = "\n\n".join(p.text for p in pages[:5])
        metadata_dict = llm_extract_metadata(metadata_text)
        with get_db() as conn:
            save_metadata(conn, extraction_id, metadata_dict)

        contractor_name = metadata_dict.get("contractor_name") or "el contratista"
        prompt = EXTRACTION_SYSTEM_PROMPT.format(contractor_name=contractor_name)

        # Extract obligations chunk by chunk
        all_obligations: list[dict] = []
        for i, chunk in enumerate(chunks):
            obligations = llm_extract_obligations(chunk, prompt)
            all_obligations.extend(obligations)
            with get_db() as conn:
                update_extraction(conn, extraction_id, chunks_processed=i + 1)

        deduplicated = deduplicate(all_obligations)

        with get_db() as conn:
            save_obligations(conn, extraction_id, deduplicated)
            update_extraction(
                conn,
                extraction_id,
                status="pending_review",
                tokens_consumed=0,  # not tracking for demo
                completed_at=datetime.utcnow().isoformat(),
            )

    except Exception as exc:
        with get_db() as conn:
            update_extraction(conn, extraction_id, status="failed")
        raise exc
```

- [ ] **Step 3: ⚠️ CHECKPOINT — Run full pipeline against real PDF**

```bash
python - << 'EOF'
import sys, os
sys.path.insert(0, "backend")
os.environ.setdefault("ANTHROPIC_API_KEY", open(".env").read().split("ANTHROPIC_API_KEY=")[1].split("\n")[0].strip())

from database import init_db, get_db, create_extraction, get_extraction, get_metadata, get_obligations
from extractor import run_extraction

init_db()
EXTRACTION_ID = "demo-test-001"

with get_db() as conn:
    create_extraction(conn, EXTRACTION_ID, "contrato_demo.pdf", 500)

print("Running extraction pipeline (may take 20-60 seconds)...")
run_extraction(EXTRACTION_ID, "sample_contracts/contrato_demo.pdf")

with get_db() as conn:
    extraction = get_extraction(conn, EXTRACTION_ID)
    metadata = get_metadata(conn, EXTRACTION_ID)
    obligations = get_obligations(conn, EXTRACTION_ID)

print(f"\n=== RESULTADO ===")
print(f"Status: {extraction['status']}")
print(f"Páginas: {extraction['page_count']}, Chunks: {extraction['chunks_total']}")
print(f"\nMetadatos:")
if metadata:
    for key in ["contract_number", "contracting_entity", "contractor_name", "contract_object"]:
        print(f"  {key}: {metadata[key]}")
print(f"\nObligaciones extraídas: {len(obligations)}")
for ob in list(obligations)[:5]:
    print(f"  [{ob['confidence']}] {ob['obligation_type']}: {ob['description'][:80]}...")
    print(f"    Cláusula: {ob['source_clause']}, Página: {ob['source_page']}")
EOF
```

**Expected (success):** status=pending_review, >0 obligations with Spanish descriptions, clause numbers and page numbers populated.

**If status=failed or 0 obligations:** Do NOT continue to Task 8. Debug:
1. Check ANTHROPIC_API_KEY is set correctly
2. Print raw LLM responses by adding `print(response.content[0].text)` in `llm_client.py`
3. Verify the prompt is producing JSON by testing a single chunk manually

- [ ] **Step 4: Commit**

```bash
git add backend/extractor.py
git commit -m "feat: full extraction pipeline with deduplication"
```

---

### Task 7: Save emergency fixture

**Files:**
- Create: `fixtures/contrato_demo_result.json` (generated, not hand-written)

- [ ] **Step 1: Generate and save fixture**

```bash
python - << 'EOF'
import sys, json
sys.path.insert(0, "backend")
from database import get_db, get_metadata, get_obligations

EXTRACTION_ID = "demo-test-001"

with get_db() as conn:
    metadata = get_metadata(conn, EXTRACTION_ID)
    obligations = get_obligations(conn, EXTRACTION_ID)

result = {
    "metadata": dict(metadata) if metadata else {},
    "obligations": [dict(ob) for ob in obligations],
}

import os
os.makedirs("fixtures", exist_ok=True)
with open("fixtures/contrato_demo_result.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"Saved {len(result['obligations'])} obligations to fixtures/contrato_demo_result.json")
EOF
```

Expected: prints "Saved N obligations..." with N > 0.

- [ ] **Step 2: Verify fixture**

```bash
python - << 'EOF'
import json
with open("fixtures/contrato_demo_result.json") as f:
    data = json.load(f)
print(f"Metadata keys: {list(data['metadata'].keys())}")
print(f"Obligations: {len(data['obligations'])}")
print(f"First obligation: {data['obligations'][0]['description'][:100]}")
print(f"source_fragment present: {bool(data['obligations'][0].get('source_fragment'))}")
EOF
```

Expected: metadata keys populated, obligations count > 0, source_fragment not empty.

- [ ] **Step 3: Commit fixture**

```bash
git add fixtures/contrato_demo_result.json
git commit -m "chore: add extraction fixture for demo fallback"
```

---

### Task 8: main.py — FastAPI endpoints

**Files:**
- Create: `backend/main.py`

- [ ] **Step 1: Write main.py**

```python
import asyncio
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

load_dotenv()

import sys
sys.path.insert(0, os.path.dirname(__file__))

from database import (
    create_extraction,
    delete_obligation,
    get_db,
    get_extraction,
    get_metadata,
    get_obligations,
    init_db,
    update_extraction,
    update_obligation,
)
from extractor import run_extraction
from models import (
    ExtractionResponse,
    MetadataResponse,
    MetadataUpdate,
    ObligationResponse,
    ObligationUpdate,
)

app = FastAPI(title="Extractor de Obligaciones Contractuales")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("/tmp/extractor_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
FIXTURE_PATH = Path("fixtures/contrato_demo_result.json")


@app.on_event("startup")
def startup() -> None:
    init_db()


def _row_to_extraction(row: Any, metadata_row: Any = None) -> dict:
    d = dict(row)
    d["metadata"] = dict(metadata_row) if metadata_row else None
    if d.get("metadata") and d["metadata"].get("confirmed_by_user") is not None:
        d["metadata"]["confirmed_by_user"] = bool(d["metadata"]["confirmed_by_user"])
    return d


@app.post("/api/extractions")
async def create_extraction_endpoint(file: UploadFile = File(...)) -> dict:
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos PDF.")

    contents = await file.read()
    if len(contents) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="El archivo excede el límite de 50MB.")

    extraction_id = str(uuid.uuid4())
    pdf_path = str(UPLOAD_DIR / f"{extraction_id}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(contents)

    with get_db() as conn:
        create_extraction(conn, extraction_id, file.filename, len(contents) // 1024)

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, run_extraction, extraction_id, pdf_path)

    return {"extraction_id": extraction_id, "status": "processing"}


@app.get("/api/extractions/{extraction_id}")
async def get_extraction_endpoint(extraction_id: str) -> dict:
    with get_db() as conn:
        row = get_extraction(conn, extraction_id)
        if not row:
            raise HTTPException(status_code=404, detail="Extracción no encontrada.")
        meta = get_metadata(conn, extraction_id)
        return _row_to_extraction(row, meta)


@app.put("/api/extractions/{extraction_id}/metadata")
async def update_metadata_endpoint(extraction_id: str, body: MetadataUpdate) -> dict:
    with get_db() as conn:
        row = get_extraction(conn, extraction_id)
        if not row:
            raise HTTPException(status_code=404, detail="Extracción no encontrada.")
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        if "confirmed_by_user" in updates:
            updates["confirmed_by_user"] = int(updates["confirmed_by_user"])
        if updates:
            conn.execute(
                "UPDATE contract_metadata SET " + ", ".join(f"{k}=?" for k in updates) + " WHERE extraction_id=?",
                [*updates.values(), extraction_id],
            )
        return dict(get_metadata(conn, extraction_id) or {})


@app.get("/api/extractions/{extraction_id}/obligations")
async def list_obligations_endpoint(extraction_id: str) -> list[dict]:
    with get_db() as conn:
        rows = get_obligations(conn, extraction_id)
        return [dict(r) for r in rows]


@app.put("/api/obligations/{obligation_id}")
async def update_obligation_endpoint(obligation_id: str, body: ObligationUpdate) -> dict:
    with get_db() as conn:
        updates = {k: v for k, v in body.model_dump().items() if v is not None}
        if not updates:
            raise HTTPException(status_code=400, detail="Nada que actualizar.")
        update_obligation(conn, obligation_id, **updates)
        row = conn.execute("SELECT * FROM obligation WHERE id=?", (obligation_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Obligación no encontrada.")
        return dict(row)


@app.delete("/api/obligations/{obligation_id}")
async def delete_obligation_endpoint(obligation_id: str) -> dict:
    with get_db() as conn:
        delete_obligation(conn, obligation_id)
    return {"deleted": obligation_id}


@app.post("/api/extractions/{extraction_id}/export")
async def export_endpoint(extraction_id: str) -> Response:
    with get_db() as conn:
        extraction = get_extraction(conn, extraction_id)
        if not extraction:
            raise HTTPException(status_code=404, detail="Extracción no encontrada.")
        meta = get_metadata(conn, extraction_id)
        obligations = conn.execute(
            "SELECT * FROM obligation WHERE extraction_id=? AND review_status='approved'",
            (extraction_id,),
        ).fetchall()

    export_data = {
        "extraction_id": extraction_id,
        "exported_at": datetime.utcnow().isoformat(),
        "contract_metadata": dict(meta) if meta else {},
        "obligations": [dict(ob) for ob in obligations],
    }
    filename = f"obligaciones_{meta['contract_number'] or extraction_id}_{datetime.utcnow().strftime('%Y%m%d')}.json"
    return Response(
        content=json.dumps(export_data, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/demo")
async def load_demo_fixture() -> dict:
    """Load pre-extracted fixture. Use if API is slow or unavailable during demo."""
    if not FIXTURE_PATH.exists():
        raise HTTPException(status_code=404, detail="Fixture no encontrado. Ejecuta el pipeline primero.")

    with open(FIXTURE_PATH, encoding="utf-8") as f:
        data = json.load(f)

    extraction_id = "demo-fixture-" + str(uuid.uuid4())[:8]
    with get_db() as conn:
        create_extraction(conn, extraction_id, "contrato_demo.pdf", 500)
        update_extraction(conn, extraction_id, status="pending_review", completed_at=datetime.utcnow().isoformat())
        from database import save_metadata, save_obligations
        save_metadata(conn, extraction_id, data["metadata"])
        save_obligations(conn, extraction_id, data["obligations"])

    return {"extraction_id": extraction_id, "status": "pending_review", "source": "fixture"}
```

- [ ] **Step 2: Start server and verify it runs**

```bash
cd /home/deozelot/Dev/Laboral/Interkont/extractor-contratos
PYTHONPATH=backend uvicorn backend.main:app --reload --port 8000
```

Expected: "Application startup complete." with no import errors.

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: FastAPI endpoints for extraction, review, and export"
```

---

### Task 9: Backend smoke test

**Files:** none (testing only)

- [ ] **Step 1: Test full backend flow with curl**

Open a new terminal (keep uvicorn running). Run these commands in order:

```bash
# Upload PDF
curl -s -X POST http://localhost:8000/api/extractions \
  -F "file=@sample_contracts/contrato_demo.pdf" | python -m json.tool
```

Expected: `{"extraction_id": "some-uuid", "status": "processing"}`

```bash
# Poll until pending_review (replace EXTRACTION_ID)
EXTRACTION_ID="paste-uuid-here"
curl -s http://localhost:8000/api/extractions/$EXTRACTION_ID | python -m json.tool
```

Expected after ~30-60s: `"status": "pending_review"`, metadata fields populated.

```bash
# List obligations
curl -s http://localhost:8000/api/extractions/$EXTRACTION_ID/obligations | python -m json.tool | head -100
```

Expected: JSON array with obligation objects, descriptions in Spanish, source_clause and source_page present.

```bash
# Approve first obligation (replace OBLIGATION_ID)
OBLIGATION_ID="paste-obligation-id-here"
curl -s -X PUT http://localhost:8000/api/obligations/$OBLIGATION_ID \
  -H "Content-Type: application/json" \
  -d '{"review_status": "approved"}' | python -m json.tool
```

Expected: obligation with `review_status: "approved"`.

```bash
# Export JSON
curl -s -X POST http://localhost:8000/api/extractions/$EXTRACTION_ID/export \
  -o test_export.json
cat test_export.json | python -m json.tool | grep -A2 "source_fragment" | head -20
```

Expected: JSON file with at least one obligation, `source_fragment` field non-null.

```bash
# Test demo fixture endpoint
curl -s http://localhost:8000/api/demo | python -m json.tool
```

Expected: `{"extraction_id": "demo-fixture-XXXXXX", "status": "pending_review", "source": "fixture"}`

- [ ] **Step 2: Commit test cleanup**

```bash
rm -f test_export.json
git add -A && git commit -m "chore: end of Day 1 — backend complete and validated"
```

---

## DAY 2 — Frontend

---

### Task 10: Frontend scaffold + types + API client

**Files:**
- Create: `frontend/` (Vite project)
- Create: `frontend/src/types.ts`
- Create: `frontend/src/api.ts`

- [ ] **Step 1: Create Vite React TypeScript project**

```bash
cd /home/deozelot/Dev/Laboral/Interkont/extractor-contratos
npm create vite@latest frontend -- --template react-ts
cd frontend && npm install
npm install axios react-router-dom
npm install -D tailwindcss postcss autoprefixer @types/react-router-dom
npx tailwindcss init -p
```

- [ ] **Step 2: Configure tailwind.config.js**

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: { extend: {} },
  plugins: [],
}
```

- [ ] **Step 3: Add Tailwind to frontend/src/index.css**

Replace the full contents of `frontend/src/index.css` with:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 4: Write frontend/src/types.ts**

```typescript
export type ExtractionStatus = 'processing' | 'pending_review' | 'completed' | 'failed';
export type ObligationType = 'deliverable' | 'report' | 'notification' | 'compliance' | 'penalty';
export type ResponsibleParty = 'contratista' | 'interventor' | 'entidad';
export type Confidence = 'high' | 'medium' | 'low';
export type ReviewStatus = 'pending' | 'approved' | 'edited' | 'rejected';

export interface ContractMetadata {
  extraction_id: string;
  contract_number: string | null;
  contracting_entity: string | null;
  contractor_name: string | null;
  contract_object: string | null;
  total_value: string | null;
  start_date: string | null;
  duration: string | null;
  supervisor: string | null;
  confirmed_by_user: boolean;
}

export interface Extraction {
  id: string;
  file_name: string;
  status: ExtractionStatus;
  page_count: number;
  chunks_processed: number;
  chunks_total: number;
  created_at: string;
  completed_at: string | null;
  metadata: ContractMetadata | null;
}

export interface Obligation {
  id: string;
  extraction_id: string;
  obligation_type: ObligationType;
  description: string;
  responsible_party: ResponsibleParty;
  deadline: string | null;
  periodicity: string | null;
  source_clause: string | null;
  source_page: number | null;
  source_fragment: string | null;
  confidence: Confidence;
  review_status: ReviewStatus;
}
```

- [ ] **Step 5: Write frontend/src/api.ts**

```typescript
import axios from 'axios';
import type { Extraction, ContractMetadata, Obligation } from './types';

const BASE = 'http://localhost:8000/api';

export const api = {
  uploadPDF: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return axios.post<{ extraction_id: string; status: string }>(`${BASE}/extractions`, form);
  },

  getExtraction: (id: string) =>
    axios.get<Extraction>(`${BASE}/extractions/${id}`),

  updateMetadata: (id: string, data: Partial<ContractMetadata>) =>
    axios.put<ContractMetadata>(`${BASE}/extractions/${id}/metadata`, data),

  getObligations: (id: string) =>
    axios.get<Obligation[]>(`${BASE}/extractions/${id}/obligations`),

  updateObligation: (id: string, data: Partial<Obligation>) =>
    axios.put<Obligation>(`${BASE}/obligations/${id}`, data),

  deleteObligation: (id: string) =>
    axios.delete(`${BASE}/obligations/${id}`),

  exportJSON: (id: string) =>
    axios.post(`${BASE}/extractions/${id}/export`, {}, { responseType: 'blob' }),

  loadDemoFixture: () =>
    axios.get<{ extraction_id: string; status: string; source: string }>(`${BASE}/demo`),
};
```

- [ ] **Step 6: Verify frontend starts**

```bash
cd /home/deozelot/Dev/Laboral/Interkont/extractor-contratos/frontend
npm run dev
```

Expected: Vite dev server at http://localhost:5173 with no errors.

- [ ] **Step 7: Commit**

```bash
cd /home/deozelot/Dev/Laboral/Interkont/extractor-contratos
git add frontend/
git commit -m "feat: frontend scaffold with types and API client"
```

---

### Task 11: App.tsx routing

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Write App.tsx with routing**

```tsx
import { BrowserRouter, Route, Routes, Navigate } from 'react-router-dom';
import UploadZone from './components/UploadZone';
import MetadataReview from './components/MetadataReview';
import ObligationsTable from './components/ObligationsTable';
import ExportScreen from './components/ExportScreen';

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-50">
        <header className="bg-white border-b border-gray-200 px-6 py-4">
          <h1 className="text-xl font-semibold text-gray-900">
            Extractor de Obligaciones — COBRA BPM
          </h1>
        </header>
        <main className="max-w-6xl mx-auto px-6 py-8">
          <Routes>
            <Route path="/" element={<UploadZone />} />
            <Route path="/metadata/:id" element={<MetadataReview />} />
            <Route path="/review/:id" element={<ObligationsTable />} />
            <Route path="/export/:id" element={<ExportScreen />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
```

- [ ] **Step 2: Create empty component stubs so routing compiles**

Create `frontend/src/components/UploadZone.tsx`:
```tsx
export default function UploadZone() { return <div>Upload</div>; }
```

Create `frontend/src/components/MetadataReview.tsx`:
```tsx
export default function MetadataReview() { return <div>Metadata</div>; }
```

Create `frontend/src/components/ObligationsTable.tsx`:
```tsx
export default function ObligationsTable() { return <div>Obligations</div>; }
```

Create `frontend/src/components/ExportScreen.tsx`:
```tsx
export default function ExportScreen() { return <div>Export</div>; }
```

- [ ] **Step 3: Verify app loads without errors**

Open http://localhost:5173 — should see "Upload" text and no console errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/
git commit -m "feat: frontend routing scaffold"
```

---

### Task 12: UploadZone (Pantalla 1)

**Files:**
- Modify: `frontend/src/components/UploadZone.tsx`

- [ ] **Step 1: Write UploadZone.tsx**

```tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';

type UploadState = 'idle' | 'uploading' | 'processing' | 'error';

export default function UploadZone() {
  const navigate = useNavigate();
  const [state, setState] = useState<UploadState>('idle');
  const [errorMsg, setErrorMsg] = useState('');
  const [progress, setProgress] = useState({ processed: 0, total: 0 });
  const [dragOver, setDragOver] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const stopPolling = () => {
    if (pollRef.current) clearInterval(pollRef.current);
  };

  useEffect(() => () => stopPolling(), []);

  const pollExtraction = (extractionId: string) => {
    setState('processing');
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await api.getExtraction(extractionId);
        setProgress({ processed: data.chunks_processed, total: data.chunks_total });
        if (data.status === 'pending_review') {
          stopPolling();
          navigate(`/metadata/${extractionId}`);
        } else if (data.status === 'failed') {
          stopPolling();
          setState('error');
          setErrorMsg('La extracción falló. Verifica que el PDF no esté escaneado.');
        }
      } catch {
        stopPolling();
        setState('error');
        setErrorMsg('Error de conexión con el servidor.');
      }
    }, 2000);
  };

  const handleFile = async (file: File) => {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setErrorMsg('Solo se aceptan archivos PDF.');
      setState('error');
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      setErrorMsg('El archivo excede 50MB.');
      setState('error');
      return;
    }
    setState('uploading');
    try {
      const { data } = await api.uploadPDF(file);
      pollExtraction(data.extraction_id);
    } catch {
      setState('error');
      setErrorMsg('Error al subir el archivo.');
    }
  };

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }, []);

  const loadDemo = async () => {
    setState('uploading');
    try {
      const { data } = await api.loadDemoFixture();
      navigate(`/metadata/${data.extraction_id}`);
    } catch {
      setState('error');
      setErrorMsg('No se encontró el fixture del demo. Ejecuta el pipeline primero.');
    }
  };

  const progressPct = progress.total > 0 ? Math.round((progress.processed / progress.total) * 100) : 0;

  return (
    <div className="flex flex-col items-center gap-6">
      <div
        onDrop={onDrop}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onClick={() => state === 'idle' && inputRef.current?.click()}
        className={`
          w-full max-w-xl border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-colors
          ${dragOver ? 'border-blue-500 bg-blue-50' : 'border-gray-300 bg-white hover:border-gray-400'}
          ${state !== 'idle' ? 'pointer-events-none opacity-60' : ''}
        `}
      >
        <input ref={inputRef} type="file" accept=".pdf" className="hidden"
          onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])} />

        {state === 'idle' && (
          <>
            <p className="text-2xl mb-2">📄</p>
            <p className="text-gray-700 font-medium">Arrastra el contrato PDF aquí</p>
            <p className="text-gray-400 text-sm mt-1">o haz clic para seleccionar</p>
            <p className="text-gray-400 text-xs mt-2">PDF digital, máx. 50MB</p>
          </>
        )}

        {state === 'uploading' && (
          <p className="text-gray-600">Subiendo archivo...</p>
        )}

        {state === 'processing' && (
          <div>
            <p className="text-gray-700 font-medium mb-3">Analizando con IA...</p>
            {progress.total > 0 && (
              <>
                <div className="w-full bg-gray-200 rounded-full h-2 mb-2">
                  <div className="bg-blue-600 h-2 rounded-full transition-all" style={{ width: `${progressPct}%` }} />
                </div>
                <p className="text-gray-500 text-sm">{progress.processed} / {progress.total} fragmentos</p>
              </>
            )}
          </div>
        )}

        {state === 'error' && (
          <div>
            <p className="text-red-600 font-medium">{errorMsg}</p>
            <button onClick={(e) => { e.stopPropagation(); setState('idle'); setErrorMsg(''); }}
              className="mt-3 text-blue-600 text-sm underline">
              Intentar de nuevo
            </button>
          </div>
        )}
      </div>

      <button onClick={loadDemo}
        className="text-sm text-gray-500 underline hover:text-gray-700">
        Cargar demo precargado (fixture)
      </button>
    </div>
  );
}
```

- [ ] **Step 2: Test in browser**

Navigate to http://localhost:5173. Verify:
- Drop zone renders correctly
- "Cargar demo precargado" button visible
- Clicking the zone opens file picker

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/UploadZone.tsx
git commit -m "feat: upload screen with drag & drop and polling progress"
```

---

### Task 13: MetadataReview (Pantalla 2)

**Files:**
- Modify: `frontend/src/components/MetadataReview.tsx`

- [ ] **Step 1: Write MetadataReview.tsx**

```tsx
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import type { ContractMetadata } from '../types';

const LABELS: Record<keyof Omit<ContractMetadata, 'extraction_id' | 'confirmed_by_user'>, string> = {
  contract_number: 'Número de contrato',
  contracting_entity: 'Entidad contratante',
  contractor_name: 'Contratista',
  contract_object: 'Objeto del contrato',
  total_value: 'Valor total',
  start_date: 'Fecha de inicio',
  duration: 'Duración',
  supervisor: 'Supervisor / Interventor',
};

export default function MetadataReview() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [metadata, setMetadata] = useState<ContractMetadata | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!id) return;
    api.getExtraction(id).then(({ data }) => {
      if (data.metadata) setMetadata(data.metadata);
    });
  }, [id]);

  const handleChange = (field: keyof ContractMetadata, value: string) => {
    setMetadata((prev) => prev ? { ...prev, [field]: value } : prev);
  };

  const handleConfirm = async () => {
    if (!id || !metadata) return;
    setSaving(true);
    await api.updateMetadata(id, { ...metadata, confirmed_by_user: true });
    navigate(`/review/${id}`);
  };

  if (!metadata) return <div className="text-gray-500">Cargando metadatos...</div>;

  return (
    <div className="max-w-2xl mx-auto">
      <h2 className="text-lg font-semibold text-gray-900 mb-6">Confirmar metadatos del contrato</h2>
      <div className="bg-white rounded-xl border border-gray-200 divide-y">
        {(Object.keys(LABELS) as Array<keyof typeof LABELS>).map((field) => (
          <div key={field} className="flex items-start px-5 py-3 gap-4">
            <span className="w-48 text-sm text-gray-500 pt-2 flex-shrink-0">{LABELS[field]}</span>
            <input
              className="flex-1 text-sm text-gray-900 border-0 border-b border-transparent hover:border-gray-300 focus:border-blue-500 focus:outline-none bg-transparent py-1 transition-colors"
              value={(metadata[field] as string) ?? ''}
              onChange={(e) => handleChange(field, e.target.value)}
              placeholder="—"
            />
          </div>
        ))}
      </div>
      <div className="mt-6 flex justify-end">
        <button
          onClick={handleConfirm}
          disabled={saving}
          className="bg-blue-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {saving ? 'Guardando...' : 'Confirmar y continuar →'}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Test the full upload → metadata flow**

1. Upload `contrato_demo.pdf` via the drop zone
2. Wait for extraction to complete (progress bar)
3. Verify metadata fields auto-populated with real contract data
4. Edit one field manually
5. Click "Confirmar y continuar" — should navigate to `/review/:id`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MetadataReview.tsx
git commit -m "feat: metadata review screen with inline editing"
```

---

### Task 14: ObligationsTable (Pantalla 3)

**Files:**
- Modify: `frontend/src/components/ObligationsTable.tsx`

- [ ] **Step 1: Write ObligationsTable.tsx**

```tsx
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import type { Obligation, ObligationType, Confidence } from '../types';

const CONFIDENCE_CHIP: Record<Confidence, string> = {
  high: 'bg-green-100 text-green-800',
  medium: 'bg-orange-100 text-orange-800',
  low: 'bg-red-100 text-red-800',
};

const TYPE_LABELS: Record<ObligationType, string> = {
  deliverable: 'Entregable',
  report: 'Informe',
  notification: 'Notificación',
  compliance: 'Cumplimiento',
  penalty: 'Sanción',
};

export default function ObligationsTable() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [obligations, setObligations] = useState<Obligation[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingData, setEditingData] = useState<Partial<Obligation>>({});

  useEffect(() => {
    if (!id) return;
    api.getObligations(id).then(({ data }) => setObligations(data));
  }, [id]);

  const approved = obligations.filter((o) => o.review_status === 'approved' || o.review_status === 'edited').length;

  const startEdit = (ob: Obligation) => {
    setEditingId(ob.id);
    setEditingData({ ...ob });
  };

  const saveEdit = async () => {
    if (!editingId) return;
    await api.updateObligation(editingId, { ...editingData, review_status: 'edited' });
    setObligations((prev) =>
      prev.map((ob) => ob.id === editingId ? { ...ob, ...editingData, review_status: 'edited' } : ob)
    );
    setEditingId(null);
  };

  const setStatus = async (obId: string, status: 'approved' | 'rejected') => {
    await api.updateObligation(obId, { review_status: status });
    setObligations((prev) =>
      prev.map((ob) => ob.id === obId ? { ...ob, review_status: status } : ob)
    );
  };

  const approveAll = async () => {
    const pending = obligations.filter((o) => o.review_status === 'pending');
    await Promise.all(pending.map((ob) => api.updateObligation(ob.id, { review_status: 'approved' })));
    setObligations((prev) =>
      prev.map((ob) => ob.review_status === 'pending' ? { ...ob, review_status: 'approved' } : ob)
    );
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-gray-900">
          Revisión de obligaciones
          <span className="ml-3 text-sm font-normal text-gray-500">
            {approved} de {obligations.length} aprobadas
          </span>
        </h2>
        <div className="flex gap-3">
          <button onClick={approveAll}
            className="text-sm px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">
            Aprobar todo
          </button>
          <button onClick={() => navigate(`/export/${id}`)}
            className="text-sm px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
            Exportar ({approved}) →
          </button>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              {['Tipo', 'Descripción', 'Responsable', 'Plazo', 'Cláusula', 'Confianza', 'Acciones'].map((h) => (
                <th key={h} className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wide">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {obligations.map((ob) => {
              const isEditing = editingId === ob.id;
              const isLow = ob.confidence === 'low';
              const isRejected = ob.review_status === 'rejected';

              return (
                <tr key={ob.id}
                  className={`
                    ${isLow && !isRejected ? 'bg-yellow-50' : 'bg-white'}
                    ${isRejected ? 'opacity-40 line-through' : ''}
                  `}
                >
                  <td className="px-4 py-3 whitespace-nowrap">
                    {isEditing ? (
                      <select className="text-xs border rounded px-1 py-0.5"
                        value={editingData.obligation_type}
                        onChange={(e) => setEditingData((d) => ({ ...d, obligation_type: e.target.value as ObligationType }))}>
                        {Object.entries(TYPE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select>
                    ) : (
                      <span className="text-xs text-gray-600">{TYPE_LABELS[ob.obligation_type]}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 max-w-xs">
                    {isEditing ? (
                      <textarea className="w-full text-xs border rounded p-1 resize-none" rows={2}
                        value={editingData.description}
                        onChange={(e) => setEditingData((d) => ({ ...d, description: e.target.value }))} />
                    ) : (
                      <span className="text-gray-900">{ob.description}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap text-gray-600 text-xs">{ob.responsible_party}</td>
                  <td className="px-4 py-3 text-gray-600 text-xs max-w-[120px]">
                    {isEditing ? (
                      <input className="w-full text-xs border rounded px-1 py-0.5"
                        value={editingData.deadline ?? ''}
                        onChange={(e) => setEditingData((d) => ({ ...d, deadline: e.target.value }))} />
                    ) : ob.deadline}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    <span title={ob.source_fragment ?? ''} className="text-xs text-blue-600 cursor-help">
                      {ob.source_clause} p.{ob.source_page}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${CONFIDENCE_CHIP[ob.confidence]}`}>
                      {ob.confidence}
                    </span>
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    {isEditing ? (
                      <div className="flex gap-1">
                        <button onClick={saveEdit} className="text-xs text-green-700 hover:underline">Guardar</button>
                        <button onClick={() => setEditingId(null)} className="text-xs text-gray-500 hover:underline">Cancelar</button>
                      </div>
                    ) : (
                      <div className="flex gap-1">
                        {ob.review_status !== 'approved' && ob.review_status !== 'edited' && (
                          <button onClick={() => setStatus(ob.id, 'approved')}
                            className="text-xs text-green-700 hover:underline">Aprobar</button>
                        )}
                        {ob.review_status !== 'rejected' && (
                          <button onClick={() => setStatus(ob.id, 'rejected')}
                            className="text-xs text-red-600 hover:underline">Rechazar</button>
                        )}
                        <button onClick={() => startEdit(ob)}
                          className="text-xs text-blue-600 hover:underline">Editar</button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {obligations.length === 0 && (
          <div className="text-center py-12 text-gray-400">No se encontraron obligaciones.</div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Test obligations table**

Navigate to `/review/:id` (use the ID from the upload flow or the demo fixture). Verify:
- Obligations list with Spanish descriptions
- Yellow highlight on `low` confidence rows
- Confidence chips (green/orange/red)
- Clause + page numbers in the Cláusula column (hover shows source_fragment tooltip)
- Approve button marks row (no more yellow)
- Edit button enables inline editing of description and deadline
- "Aprobar todo" approves all pending rows
- Counter updates correctly

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ObligationsTable.tsx
git commit -m "feat: obligations review table with inline editing and approve/reject"
```

---

### Task 15: ExportScreen (Pantalla 4)

**Files:**
- Modify: `frontend/src/components/ExportScreen.tsx`

- [ ] **Step 1: Write ExportScreen.tsx**

```tsx
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import type { Obligation } from '../types';

export default function ExportScreen() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [obligations, setObligations] = useState<Obligation[]>([]);
  const [exported, setExported] = useState(false);

  useEffect(() => {
    if (!id) return;
    api.getObligations(id).then(({ data }) => setObligations(data));
  }, [id]);

  const approved = obligations.filter((o) => o.review_status === 'approved' || o.review_status === 'edited');
  const rejected = obligations.filter((o) => o.review_status === 'rejected');

  const handleExportJSON = async () => {
    if (!id) return;
    const { data } = await api.exportJSON(id);
    const url = URL.createObjectURL(data as Blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `obligaciones_${id.slice(0, 8)}.json`;
    a.click();
    URL.revokeObjectURL(url);
    setExported(true);
  };

  return (
    <div className="max-w-xl mx-auto text-center">
      <h2 className="text-lg font-semibold text-gray-900 mb-2">Exportar a COBRA BPM</h2>

      <div className="bg-white rounded-xl border border-gray-200 p-8 mb-6">
        <div className="flex justify-center gap-12 mb-8">
          <div>
            <p className="text-4xl font-bold text-green-600">{approved.length}</p>
            <p className="text-sm text-gray-500 mt-1">Aprobadas</p>
          </div>
          <div>
            <p className="text-4xl font-bold text-red-500">{rejected.length}</p>
            <p className="text-sm text-gray-500 mt-1">Rechazadas</p>
          </div>
        </div>

        <button onClick={handleExportJSON}
          className="w-full bg-blue-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-blue-700 transition-colors mb-3">
          Exportar JSON (COBRA BPM)
        </button>

        {exported && (
          <div className="mt-4 text-sm text-green-700 bg-green-50 rounded-lg py-3 px-4">
            ✓ Listo para importar a COBRA BPM
          </div>
        )}
      </div>

      <button onClick={() => navigate(`/review/${id}`)}
        className="text-sm text-gray-500 hover:text-gray-700 underline">
        ← Volver a revisión
      </button>
    </div>
  );
}
```

- [ ] **Step 2: Test export flow**

1. Navigate to export screen via the obligations table "Exportar" button
2. Verify approved/rejected counts are correct
3. Click "Exportar JSON" — file should download
4. Open the downloaded JSON — verify `source_fragment` field is present in each obligation

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ExportScreen.tsx
git commit -m "feat: export screen with JSON download"
```

---

### Task 16: End-to-end validation

**Files:** none (testing only)

- [ ] **Step 1: Full demo flow — run 3 times**

For each run:
1. Go to http://localhost:5173
2. Drop `sample_contracts/contrato_demo.pdf` onto the upload zone
3. Watch progress bar count chunks
4. Confirm metadata (edit at least one field)
5. Review obligations table:
   - Verify ≥5 obligations extracted
   - Verify at least one `low` confidence row highlighted yellow
   - Edit one obligation description inline
   - Approve 3–5 specific obligations
   - Use "Aprobar todo" to approve all remaining
6. Export JSON — open file and verify:
   - `source_fragment` is non-empty in each obligation
   - `source_clause` and `source_page` are populated
   - Only approved/edited obligations are included

- [ ] **Step 2: Verify no console errors**

Open browser DevTools → Console. Run the full flow again. Expected: zero red errors (CA-07).

- [ ] **Step 3: Test demo fixture fallback**

1. Go to http://localhost:5173
2. Click "Cargar demo precargado (fixture)"
3. Verify it skips the upload/processing screen and goes directly to metadata review
4. Continue through the full review → export flow

- [ ] **Step 4: Final commit**

```bash
cd /home/deozelot/Dev/Laboral/Interkont/extractor-contratos
git add -A
git commit -m "feat: complete demo — upload, extract, review, export working end-to-end"
```

---

## Acceptance Criteria Checklist

| ID | Criterio | Tarea |
|----|----------|-------|
| CA-01 | PDF de 20 páginas procesado en <30s | Task 6 checkpoint — medir tiempo |
| CA-02 | Cada obligación tiene `source_clause` y `source_page` | Task 9 smoke test + Task 16 |
| CA-03 | Obligaciones `low` destacadas en amarillo | Task 14 |
| CA-04 | Editar, aprobar y rechazar obligaciones | Task 14 |
| CA-05 | JSON exportado incluye `source_fragment` | Task 15 Step 2 |
| CA-06 | Error claro para PDF escaneado | Task 8 (main.py) + Task 1 Step 4 |
| CA-07 | Sin errores de consola en Chrome | Task 16 Step 2 |

---

## Si algo falla en el Checkpoint de Task 6

1. Imprimir la respuesta raw del LLM: añadir `print(response.content[0].text[:500])` en `llm_client.extract_obligations`
2. Verificar que el JSON retornado es parseable: probar `json.loads(response.content[0].text.strip())`
3. Si el modelo retorna texto antes del JSON: `parse_llm_response` debe capturarlo con regex — verificar test_parsers
4. Si 0 obligaciones en todos los chunks: revisar que `{contractor_name}` en el prompt se resuelve correctamente
5. Si error de API key: verificar `echo $ANTHROPIC_API_KEY` en el terminal donde corre uvicorn

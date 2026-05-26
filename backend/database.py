import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

_EXTRACTION_UPDATABLE = frozenset({
    "status", "page_count", "chunks_processed", "chunks_total",
    "tokens_consumed", "completed_at", "model_used",
})

_OBLIGATION_UPDATABLE = frozenset({
    "obligation_type", "description", "responsible_party", "deadline",
    "periodicity", "source_clause", "source_page", "source_fragment",
    "confidence", "review_status",
})

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
    """Insert a new extraction record with status 'processing'."""
    conn.execute(
        """INSERT INTO extraction (id, file_name, file_size_kb, status, model_used, created_at)
           VALUES (?, ?, ?, 'processing', 'claude-sonnet-4-6', ?)""",
        (extraction_id, file_name, file_size_kb, datetime.now(timezone.utc).isoformat()),
    )


def update_extraction(conn: sqlite3.Connection, extraction_id: str, **kwargs) -> None:
    """Update fields on an extraction row. Only known columns are allowed."""
    if not kwargs:
        return
    invalid = set(kwargs) - _EXTRACTION_UPDATABLE
    if invalid:
        raise ValueError(f"Unknown extraction columns: {invalid}")
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [extraction_id]
    conn.execute(f"UPDATE extraction SET {sets} WHERE id = ?", values)


def get_extraction(conn: sqlite3.Connection, extraction_id: str) -> sqlite3.Row | None:
    """Fetch a single extraction row by ID."""
    return conn.execute("SELECT * FROM extraction WHERE id = ?", (extraction_id,)).fetchone()


def save_metadata(conn: sqlite3.Connection, extraction_id: str, data: dict) -> None:
    """Upsert contract metadata for an extraction."""
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
    """Fetch contract metadata for an extraction."""
    return conn.execute(
        "SELECT * FROM contract_metadata WHERE extraction_id = ?", (extraction_id,)
    ).fetchone()


def save_obligations(conn: sqlite3.Connection, extraction_id: str, obligations: list[dict]) -> None:
    """Bulk-insert obligations extracted from a contract."""
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
    """Fetch all obligations for an extraction, ordered by page and clause."""
    return conn.execute(
        "SELECT * FROM obligation WHERE extraction_id = ? ORDER BY source_page, source_clause",
        (extraction_id,),
    ).fetchall()


def update_obligation(conn: sqlite3.Connection, obligation_id: str, **kwargs) -> None:
    """Update fields on an obligation row. Only known columns are allowed."""
    if not kwargs:
        return
    invalid = set(kwargs) - _OBLIGATION_UPDATABLE
    if invalid:
        raise ValueError(f"Unknown obligation columns: {invalid}")
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [obligation_id]
    conn.execute(f"UPDATE obligation SET {sets} WHERE id = ?", values)


def delete_obligation(conn: sqlite3.Connection, obligation_id: str) -> None:
    """Delete a single obligation by ID."""
    conn.execute("DELETE FROM obligation WHERE id = ?", (obligation_id,))

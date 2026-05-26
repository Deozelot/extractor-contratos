import os
import json
import pdfplumber
from dataclasses import dataclass
from datetime import datetime, timezone
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

        if end <= start:
            break
        next_start = end - overlap_chars
        if next_start <= start:
            next_start = end
        start = next_start

    return chunks


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
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    except Exception as exc:
        with get_db() as conn:
            update_extraction(conn, extraction_id, status="failed")
        raise exc

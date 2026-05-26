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

        if end <= start:
            break
        next_start = end - overlap_chars
        if next_start <= start:
            next_start = end
        start = next_start

    return chunks

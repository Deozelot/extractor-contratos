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


def test_make_chunks_skips_whitespace_only_page_text():
    from extractor import extract_pages
    # We can't test extract_pages without a real PDF, but we can verify
    # that make_chunks handles PageText with empty text gracefully
    pages = [PageText(page_num=1, text="   \n  ")]
    # make_chunks strips text, empty chunk not added
    chunks = make_chunks(pages)
    assert all(c.text.strip() != "" for c in chunks)

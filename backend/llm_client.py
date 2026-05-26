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

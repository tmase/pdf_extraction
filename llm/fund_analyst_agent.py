"""
Step 2 -- Text Processing (LLM): the "Fund Analyst Agent".

Calls the real Anthropic API when ANTHROPIC_API_KEY is set, otherwise falls
back to a deterministic regex/keyword extractor so the whole pipeline still
runs end to end without any credentials. The system prompt is loaded from
the database (prompt_templates table) rather than a markdown file, so it can
be edited live from the dashboard -- and updated with SME feedback -- per
the proposal.
"""
import json
import re

import config
from db.models import PromptTemplate
from db.seed import DEFAULT_SYSTEM_PROMPT


def get_active_prompt(session) -> str:
    row = session.query(PromptTemplate).filter_by(name="fund_analyst_system_prompt").first()
    return row.content if row else DEFAULT_SYSTEM_PROMPT


def extract_fields(session, text: str) -> tuple[dict, str]:
    """
    Returns (fields, engine_used) where fields is
    {field_name: {"value":..., "source_context":..., "confidence":...}}
    """
    prompt = get_active_prompt(session)

    if config.USE_REAL_LLM:
        try:
            return _extract_with_claude(prompt, text), f"claude:{config.ANTHROPIC_MODEL}"
        except Exception as exc:
            fields = _extract_with_mock(text)
            note = f" [Claude call failed ({exc}); mock fallback used]"
            for f in fields.values():
                f["source_context"] = (f.get("source_context") or "") + note
            return fields, "mock (claude call failed)"

    return _extract_with_mock(text), "mock"


def _extract_with_claude(system_prompt: str, text: str) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=2000,
        system=system_prompt,
        messages=[{"role": "user", "content": f"Risk report text:\n\n{text[:15000]}"}],
    )
    raw = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    payload = json.loads(match.group(0) if match else raw)
    fields = payload.get("fields", payload)
    # Normalize so every configured field is always present.
    for name in config.FIELD_SCHEMA:
        fields.setdefault(name, {"value": None, "source_context": "", "confidence": 0.0})
    return fields


# ---------------------------------------------------------------------------
# Mock extractor. Deliberately simple/deterministic (regex over labeled
# text) -- it exists so this prototype demonstrably runs end to end without
# an API key, not to replace the real LLM's language understanding.
# ---------------------------------------------------------------------------

# Note: all the "gap" groups below (\D{0,N}?) are non-greedy. A greedy \D
# would happily eat past a leading '-' sign (breaking negative numbers) or
# consume the first few characters of a bare-word value like "Information
# Technology" before the capture group even starts. Lazy matching stops
# consuming as soon as the rest of the pattern can match, which keeps both
# the sign and the full value intact.
_PATTERNS = {
    "fund_name": [r"(?:Fund|Portfolio)(?: Name)?:\s*(.+)"],
    "reporting_date": [r"(?:Reporting Date|As of|Report Date):?\s*([A-Za-z0-9,\-/ ]+)"],
    "capital_base": [r"(?:Capital Base|NAV|Net Asset Value)\D{0,12}?\$?\s*([\d,\.]+)"],
    "monthly_net_return": [r"Monthly Net Return\D{0,10}?(-?[\d\.]+)\s*%"],
    "ytd_net_return": [r"YTD Net Return\D{0,10}?(-?[\d\.]+)\s*%"],
    "long_exposure": [r"Long Exposure\D{0,10}?(-?[\d\.]+)\s*%"],
    "short_exposure": [r"Short Exposure\D{0,10}?(-?[\d\.]+)\s*%"],
    "gross_exposure": [r"Gross Exposure\D{0,10}?(-?[\d\.]+)\s*%"],
    "net_exposure": [r"Net Exposure\D{0,10}?(-?[\d\.]+)\s*%"],
    "top_sector": [r"Top Sector\D{0,10}?([A-Za-z &/]+?)(?:\n|,|\.|$)"],
    "top_region": [r"Top Region\D{0,10}?([A-Za-z &/]+?)(?:\n|,|\.|$)"],
    "liquidity_30d": [r"Liquidity[^\n]{0,20}?30[^\n]{0,15}?(-?[\d\.]+)\s*%"],
}


def _find_context(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end].strip()


def _extract_with_mock(text: str) -> dict:
    results = {}
    for field_name, patterns in _PATTERNS.items():
        found = None
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                found = m
                break
        if found:
            value = found.group(1).strip().rstrip(".,")
            results[field_name] = {
                "value": value,
                "source_context": _find_context(text, found.start(), found.end()),
                # A clean regex hit against a labeled field, not true
                # language understanding -- kept intentionally shy of 1.0.
                "confidence": 0.9,
            }
        else:
            results[field_name] = {"value": None, "source_context": "", "confidence": 0.0}
    return results

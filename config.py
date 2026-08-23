"""
Central configuration for the pipeline.

Reads from a .env file (if present) with sensible local defaults, so the
prototype runs out of the box without any setup.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent

# --- Storage ---
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'storage' / 'app.db'}")
VECTOR_DB_PATH = str(BASE_DIR / os.getenv("VECTOR_DB_PATH", "storage/vector_db"))
UPLOAD_DIR = BASE_DIR / os.getenv("UPLOAD_DIR", "storage/uploaded_pdfs")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# --- LLM ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
USE_REAL_LLM = bool(ANTHROPIC_API_KEY)

# --- Pipeline behavior ---
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.75"))
SME_EMAIL = os.getenv("SME_EMAIL", "sme@uvimco.example")

# --- Fields the Fund Analyst Agent extracts (from the project proposal) ---
# name -> expected type: "string" | "date" | "percent" | "currency"
FIELD_SCHEMA = {
    "fund_name": "string",
    "reporting_date": "date",
    "capital_base": "currency",
    "monthly_net_return": "percent",
    "ytd_net_return": "percent",
    "long_exposure": "percent",
    "short_exposure": "percent",
    "gross_exposure": "percent",
    "net_exposure": "percent",
    "top_sector": "string",
    "top_region": "string",
    "liquidity_30d": "percent",
}

REVIEW_CLASSIFICATIONS = ["missing", "ambiguous", "conflicting", "incorrectly_scoped"]

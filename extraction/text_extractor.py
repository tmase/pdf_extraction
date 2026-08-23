"""
Step 1 -- Text Extraction (no LLM).

Per the proposal: pypdf (successor to PyPDF2) for text, Camelot for tables
(falling back to pdfplumber if Camelot/Ghostscript isn't available in this
environment), and Tesseract OCR for pages that come back with no
extractable text (i.e. scanned documents).
"""
import hashlib
from dataclasses import dataclass

import pdfplumber
import pypdf
import pytesseract

try:
    import camelot
except ImportError:  # pragma: no cover - optional dependency
    camelot = None

try:
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover - optional dependency
    pdfium = None


@dataclass
class ExtractionResult:
    text: str
    tables: list
    page_count: int
    method: str  # which techniques actually fired, for the audit log


def sha256_of_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_text_pypdf(filepath: str) -> list:
    reader = pypdf.PdfReader(filepath)
    return [(page.extract_text() or "") for page in reader.pages]


def _ocr_page(filepath: str, page_index: int) -> str:
    if pdfium is None:
        return ""
    try:
        pdf = pdfium.PdfDocument(filepath)
        page = pdf[page_index]
        bitmap = page.render(scale=2.0)
        pil_image = bitmap.to_pil()
        return pytesseract.image_to_string(pil_image)
    except Exception:
        return ""


def _extract_tables_camelot(filepath: str) -> list:
    tables_text = []
    if camelot is None:
        return tables_text
    for flavor in ("lattice", "stream"):
        try:
            tables = camelot.read_pdf(filepath, pages="all", flavor=flavor)
            for t in tables:
                csv_text = t.df.to_csv(index=False)
                if csv_text.strip():
                    tables_text.append(csv_text)
            if tables_text:
                break
        except Exception:
            continue
    return tables_text


def _extract_tables_pdfplumber(filepath: str) -> list:
    tables_text = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    rows = ["\t".join(c or "" for c in row) for row in table]
                    text = "\n".join(rows)
                    if text.strip():
                        tables_text.append(text)
    except Exception:
        pass
    return tables_text


def extract(filepath: str) -> ExtractionResult:
    methods_used = ["pypdf"]

    page_texts = _extract_text_pypdf(filepath)
    page_count = len(page_texts)

    for i, text in enumerate(page_texts):
        if len(text.strip()) < 20:
            ocr_text = _ocr_page(filepath, i)
            if ocr_text.strip():
                page_texts[i] = ocr_text
                if "tesseract_ocr" not in methods_used:
                    methods_used.append("tesseract_ocr")

    tables = _extract_tables_camelot(filepath)
    if tables:
        methods_used.append("camelot")
    else:
        tables = _extract_tables_pdfplumber(filepath)
        if tables:
            methods_used.append("pdfplumber_tables")

    full_text = "\n\n".join(page_texts)
    if tables:
        full_text += "\n\n" + "\n\n".join(f"[TABLE]\n{t}" for t in tables)

    return ExtractionResult(
        text=full_text,
        tables=tables,
        page_count=page_count,
        method="+".join(methods_used),
    )

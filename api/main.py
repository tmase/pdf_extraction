"""
Step 4 (API half) -- exposes the pipeline to downstream applications, per
goal #4 in the proposal ("store extracted data and make it available to
downstream applications").

Run with:  python cli.py serve-api      (or: uvicorn api.main:app --reload)
Docs at:   http://localhost:8000/docs
"""
import json
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel

import config
from db.database import init_db, session_scope
from db.models import AuditLogEntry, Document, ExtractedField, FieldStatus, PromptTemplate
from pipeline.orchestrator import append_feedback_to_prompt, process_document, record_review_decision
from vectorstore import store as vector_store

app = FastAPI(title="UVIMCO Risk Report Extraction Pipeline", version="0.1.0")


@app.on_event("startup")
def _startup():
    init_db()


class ReviewDecision(BaseModel):
    action: str  # approve | correct | reject
    reviewer: str
    corrected_value: Optional[str] = None
    feedback: Optional[str] = None


class PromptUpdate(BaseModel):
    content: str
    updated_by: str = "dashboard"


class PromptFeedback(BaseModel):
    feedback: str
    updated_by: str = "dashboard"


def _field_to_dict(f: ExtractedField) -> dict:
    return {
        "id": f.id,
        "document_id": f.document_id,
        "field_name": f.field_name,
        "value": f.value,
        "corrected_value": f.corrected_value,
        "source_context": f.source_context,
        "confidence": f.confidence,
        "status": f.status.value if hasattr(f.status, "value") else f.status,
        "review_classification": f.review_classification,
        "validation_notes": f.validation_notes,
        "reviewed_by": f.reviewed_by,
        "reviewer_feedback": f.reviewer_feedback,
    }


@app.get("/health")
def health():
    return {"status": "ok", "llm_mode": "claude" if config.USE_REAL_LLM else "mock"}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    dest = config.UPLOAD_DIR / file.filename
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    with session_scope() as session:
        summary = process_document(session, str(dest))
    return summary


@app.post("/ingest")
def ingest(path: str):
    """Run the pipeline on a server-local path (file or folder)."""
    target = Path(path)
    if not target.exists():
        raise HTTPException(404, f"path not found: {path}")
    files = sorted(target.glob("*.pdf")) if target.is_dir() else [target]
    results = []
    for filepath in files:
        with session_scope() as session:
            results.append(process_document(session, str(filepath)))
    return results


@app.get("/documents")
def list_documents():
    with session_scope() as session:
        docs = session.query(Document).all()
        return [
            {
                "id": d.id,
                "filename": d.filename,
                "status": d.status.value,
                "page_count": d.page_count,
                "extraction_method": d.extraction_method,
                "uploaded_at": d.uploaded_at,
                "processed_at": d.processed_at,
            }
            for d in docs
        ]


@app.get("/documents/{document_id}")
def get_document(document_id: int):
    with session_scope() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise HTTPException(404, "document not found")
        return {
            "id": doc.id,
            "filename": doc.filename,
            "status": doc.status.value,
            "page_count": doc.page_count,
            "extraction_method": doc.extraction_method,
            "fields": [_field_to_dict(f) for f in doc.fields],
        }


@app.get("/review-queue")
def review_queue():
    with session_scope() as session:
        rows = session.query(ExtractedField).filter_by(status=FieldStatus.NEEDS_REVIEW).all()
        return [_field_to_dict(r) for r in rows]


@app.post("/review/{field_id}")
def submit_review(field_id: int, decision: ReviewDecision):
    with session_scope() as session:
        try:
            field = record_review_decision(
                session, field_id, decision.action, decision.reviewer,
                corrected_value=decision.corrected_value, feedback=decision.feedback,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return _field_to_dict(field)


@app.get("/audit-log")
def audit_log(document_id: Optional[int] = None):
    with session_scope() as session:
        query = session.query(AuditLogEntry).order_by(AuditLogEntry.timestamp)
        if document_id:
            query = query.filter_by(document_id=document_id)
        return [
            {
                "id": e.id,
                "timestamp": e.timestamp,
                "actor": e.actor,
                "document_id": e.document_id,
                "step": e.step,
                "action": e.action,
                "result": e.result,
                "details": json.loads(e.details or "{}"),
            }
            for e in query.all()
        ]


@app.get("/query")
def query(q: str, k: int = 5):
    """Simple RAG-style retrieval over indexed document chunks."""
    return vector_store.query(q, k=k)


@app.get("/prompts")
def list_prompts():
    with session_scope() as session:
        rows = session.query(PromptTemplate).all()
        return [{"name": p.name, "content": p.content, "version": p.version} for p in rows]


@app.put("/prompts/{name}")
def update_prompt(name: str, update: PromptUpdate):
    with session_scope() as session:
        prompt = session.query(PromptTemplate).filter_by(name=name).first()
        if prompt is None:
            raise HTTPException(404, "prompt not found")
        prompt.content = update.content
        prompt.version += 1
        prompt.updated_by = update.updated_by
        session.flush()
        return {"name": prompt.name, "version": prompt.version}


@app.post("/prompts/{name}/feedback")
def add_prompt_feedback(name: str, payload: PromptFeedback):
    with session_scope() as session:
        try:
            prompt = append_feedback_to_prompt(session, payload.feedback, payload.updated_by, prompt_name=name)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return {"name": prompt.name, "version": prompt.version, "content": prompt.content}

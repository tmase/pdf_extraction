"""
Ties Steps 1-4 together for a single document, and implements the
human-review feedback loop (approve/correct/reject -> ground truth ->
prompt notes).
"""
import datetime as dt
from pathlib import Path

import config
from audit import log
from db.models import Document, DocumentStatus, ExtractedField, FieldStatus, GroundTruth, PromptTemplate
from extraction import text_extractor
from llm import fund_analyst_agent
from review.notifier import notify_sme
from validation import validators
from vectorstore import store as vector_store


def process_document(session, filepath: str) -> dict:
    filepath = str(filepath)
    filename = Path(filepath).name
    sha256 = text_extractor.sha256_of_file(filepath)

    document = Document(filename=filename, filepath=filepath, sha256=sha256, status=DocumentStatus.PROCESSING)
    session.add(document)
    session.flush()

    log(session, actor="system", step="ingestion", action="upload", document_id=document.id,
        details={"filename": filename, "sha256": sha256})

    # --- Step 1: Text extraction (no LLM) ---
    extraction = text_extractor.extract(filepath)
    document.raw_text = extraction.text
    document.page_count = extraction.page_count
    document.extraction_method = extraction.method
    session.flush()

    log(session, actor="system", step="extraction", action="extract_text",
        document_id=document.id,
        details={"method": extraction.method, "page_count": extraction.page_count,
                  "tables_found": len(extraction.tables), "chars_extracted": len(extraction.text)})

    # --- Step 2: Text processing (LLM / Fund Analyst Agent) ---
    fields, engine = fund_analyst_agent.extract_fields(session, extraction.text)
    log(session, actor="fund_analyst_agent", step="llm_extraction", action="extract_fields",
        document_id=document.id, details={"engine": engine, "fields_found": list(fields.keys())})

    field_rows = {}
    for field_name in config.FIELD_SCHEMA:
        info = fields.get(field_name, {"value": None, "source_context": "", "confidence": 0.0})
        value = info.get("value")
        confidence = float(info.get("confidence") or 0.0)
        has_value = value not in (None, "")

        row = ExtractedField(
            document_id=document.id,
            field_name=field_name,
            value=str(value) if has_value else None,
            source_context=info.get("source_context") or "",
            confidence=confidence,
        )

        if has_value and confidence >= config.CONFIDENCE_THRESHOLD:
            row.status = FieldStatus.AUTO_APPROVED
        else:
            row.status = FieldStatus.NEEDS_REVIEW
            row.review_classification = "missing" if not has_value else "ambiguous"

        session.add(row)
        session.flush()
        field_rows[field_name] = row

        if row.status == FieldStatus.NEEDS_REVIEW:
            reason = "no value was extracted" if not has_value else f"low LLM confidence ({confidence:.2f})"
            notify_sme(session, document_id=document.id, field=row,
                       classification=row.review_classification, reason=reason)

    # --- Step 3: Validation & reconciliation (no LLM) ---
    for field_name, row in field_rows.items():
        if row.value is None:
            continue  # already flagged as missing above
        result = validators.validate_field(session, field_name, row.value)
        if not result.passed:
            row.status = FieldStatus.NEEDS_REVIEW
            row.review_classification = result.classification
            row.validation_notes = result.reason
            session.flush()
            notify_sme(session, document_id=document.id, field=row,
                       classification=result.classification, reason=result.reason)

    log(session, actor="system", step="validation", action="validate_fields", document_id=document.id,
        details={"needs_review": [f for f, r in field_rows.items() if r.status == FieldStatus.NEEDS_REVIEW]})

    # --- Step 4: Loading & dissemination ---
    chunks_indexed = vector_store.index_document(document.id, filename, extraction.text)
    log(session, actor="system", step="indexing", action="index_vector_store", document_id=document.id,
        details={"chunks_indexed": chunks_indexed})

    any_needs_review = any(r.status == FieldStatus.NEEDS_REVIEW for r in field_rows.values())
    document.status = DocumentStatus.NEEDS_REVIEW if any_needs_review else DocumentStatus.PROCESSED
    document.processed_at = dt.datetime.utcnow()
    session.flush()

    log(session, actor="system", step="pipeline", action="complete", document_id=document.id,
        result="needs_review" if any_needs_review else "processed",
        details={"status": document.status.value})

    return {
        "document_id": document.id,
        "filename": filename,
        "status": document.status.value,
        "fields": {name: {"value": r.value, "status": r.status.value, "confidence": r.confidence}
                   for name, r in field_rows.items()},
    }


def record_review_decision(session, field_id: int, action: str, reviewer: str,
                            corrected_value: str | None = None, feedback: str | None = None):
    """action: 'approve' | 'correct' | 'reject'"""
    field = session.get(ExtractedField, field_id)
    if field is None:
        raise ValueError(f"no extracted field with id {field_id}")

    field.reviewed_by = reviewer
    field.reviewer_feedback = feedback
    field.reviewed_at = dt.datetime.utcnow()

    if action == "approve":
        field.status = FieldStatus.APPROVED
        final_value = field.value
    elif action == "correct":
        field.status = FieldStatus.CORRECTED
        field.corrected_value = corrected_value
        final_value = corrected_value
    elif action == "reject":
        field.status = FieldStatus.REJECTED
        final_value = None
    else:
        raise ValueError(f"unknown action: {action}")

    session.flush()

    # Feed human-verified values back into ground truth so future dynamic
    # range checks improve -- this is the "human-in-the-loop improvement"
    # goal from the proposal.
    if final_value is not None and config.FIELD_SCHEMA.get(field.field_name) in ("percent", "currency"):
        try:
            numeric_value = validators.cast_value(field.field_name, final_value)
            session.add(GroundTruth(field_name=field.field_name, value=numeric_value, source="learned"))
        except (ValueError, TypeError):
            pass

    log(session, actor=f"sme:{reviewer}", step="review", action=f"decision:{action}",
        document_id=field.document_id, result=action,
        details={"field": field.field_name, "corrected_value": corrected_value, "feedback": feedback})

    # Re-evaluate the parent document's status.
    document = session.get(Document, field.document_id)
    remaining = [f for f in document.fields if f.status == FieldStatus.NEEDS_REVIEW]
    document.status = DocumentStatus.NEEDS_REVIEW if remaining else DocumentStatus.PROCESSED
    session.flush()

    return field


def append_feedback_to_prompt(session, feedback_text: str, updated_by: str,
                               prompt_name: str = "fund_analyst_system_prompt"):
    """
    Appends an SME correction note to the active prompt so the Fund Analyst
    Agent's behavior improves over time, per the proposal's requirement that
    "the SME's feedback should be incorporated into the prompts."
    """
    prompt = session.query(PromptTemplate).filter_by(name=prompt_name).first()
    if prompt is None:
        raise ValueError(f"no prompt template named {prompt_name}")

    marker = "\n\n# Known correction notes (from SME review)\n"
    if marker not in prompt.content:
        prompt.content += marker
    timestamp = dt.datetime.utcnow().strftime("%Y-%m-%d")
    prompt.content += f"- [{timestamp}, {updated_by}] {feedback_text}\n"
    prompt.version += 1
    prompt.updated_by = updated_by
    prompt.updated_at = dt.datetime.utcnow()
    session.flush()

    log(session, actor=f"sme:{updated_by}", step="review", action="update_prompt",
        details={"prompt_name": prompt_name, "feedback": feedback_text, "new_version": prompt.version})
    return prompt

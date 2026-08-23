"""
SME-facing front end: upload/ingest PDFs, work the human review queue,
edit the Fund Analyst Agent's prompts/rules, and browse the audit log.

Run with:  streamlit run dashboard/app.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

import config
from db.database import init_db, session_scope
from db.models import AuditLogEntry, Document, ExtractedField, FieldStatus, PromptTemplate
from pipeline.orchestrator import append_feedback_to_prompt, process_document, record_review_decision

st.set_page_config(page_title="UVIMCO Risk Report Pipeline", layout="wide")
init_db()

st.title("Risk Report Data Extraction -- Prototype")
st.caption(
    f"LLM mode: **{'Claude API' if config.USE_REAL_LLM else 'mock extractor (no ANTHROPIC_API_KEY set)'}** "
    f"&nbsp;|&nbsp; confidence threshold: **{config.CONFIDENCE_THRESHOLD}**"
)

tab_docs, tab_review, tab_prompts, tab_audit = st.tabs(
    ["Documents & Ingest", "Review Queue", "Prompts & Rules", "Audit Log"]
)

# --------------------------------------------------------------------------
# Documents & Ingest
# --------------------------------------------------------------------------
with tab_docs:
    st.subheader("Upload a risk report PDF")
    uploaded = st.file_uploader("PDF file", type=["pdf"])
    if uploaded is not None and st.button("Run pipeline on this file"):
        dest = config.UPLOAD_DIR / uploaded.name
        dest.write_bytes(uploaded.getvalue())
        with st.spinner("Running Steps 1-4..."):
            with session_scope() as session:
                summary = process_document(session, str(dest))
        st.success(f"Processed {summary['filename']} -> status: {summary['status']}")
        st.json(summary["fields"])

    st.divider()
    st.subheader("Documents")
    with session_scope() as session:
        docs = session.query(Document).order_by(Document.uploaded_at.desc()).all()
        rows = [
            {
                "id": d.id,
                "filename": d.filename,
                "status": d.status.value,
                "pages": d.page_count,
                "extraction_method": d.extraction_method,
                "uploaded_at": d.uploaded_at,
                "processed_at": d.processed_at,
            }
            for d in docs
        ]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No documents ingested yet. Try `python cli.py generate-samples` then "
                "`python cli.py ingest sample_data/pdfs`, or upload one above.")

# --------------------------------------------------------------------------
# Review Queue
# --------------------------------------------------------------------------
with tab_review:
    st.subheader("Fields awaiting human review")
    with session_scope() as session:
        pending = (
            session.query(ExtractedField)
            .filter_by(status=FieldStatus.NEEDS_REVIEW)
            .join(Document)
            .order_by(ExtractedField.created_at)
            .all()
        )
        pending_data = [
            {
                "field_id": f.id,
                "document": f.document.filename,
                "field_name": f.field_name,
                "value": f.value,
                "confidence": round(f.confidence, 2),
                "classification": f.review_classification,
                "notes": f.validation_notes,
                "source_context": f.source_context,
            }
            for f in pending
        ]

    if not pending_data:
        st.success("Review queue is empty.")
    else:
        st.dataframe(pd.DataFrame(pending_data).drop(columns=["source_context"]),
                     use_container_width=True, hide_index=True)

        st.divider()
        options = {f"#{p['field_id']} - {p['document']} / {p['field_name']}": p["field_id"] for p in pending_data}
        choice = st.selectbox("Select a field to resolve", list(options.keys()))
        field_id = options[choice]
        selected = next(p for p in pending_data if p["field_id"] == field_id)

        st.markdown(f"**Extracted value:** `{selected['value']}`")
        st.markdown(f"**Classification:** `{selected['classification']}`  |  **Confidence:** {selected['confidence']}")
        if selected["notes"]:
            st.markdown(f"**Validation notes:** {selected['notes']}")
        st.text_area("Source context", selected["source_context"], height=80, disabled=True)

        reviewer = st.text_input("Your name/id", value="sme_analyst")
        corrected_value = st.text_input("Corrected value (only used for 'Approve with correction')")
        feedback = st.text_area("Feedback for the Fund Analyst Agent (optional)")

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Approve as-is", type="primary"):
                with session_scope() as session:
                    record_review_decision(session, field_id, "approve", reviewer, feedback=feedback or None)
                st.rerun()
        with col2:
            if st.button("Approve with correction"):
                with session_scope() as session:
                    record_review_decision(session, field_id, "correct", reviewer,
                                            corrected_value=corrected_value, feedback=feedback or None)
                st.rerun()
        with col3:
            if st.button("Reject"):
                with session_scope() as session:
                    record_review_decision(session, field_id, "reject", reviewer, feedback=feedback or None)
                st.rerun()

        if feedback:
            st.caption("Tip: use 'Send feedback to prompt' on the Prompts & Rules tab to make this "
                       "correction guide future extractions.")

# --------------------------------------------------------------------------
# Prompts & Rules
# --------------------------------------------------------------------------
with tab_prompts:
    st.subheader("Fund Analyst Agent -- prompt & rules configuration")
    with session_scope() as session:
        prompt = session.query(PromptTemplate).filter_by(name="fund_analyst_system_prompt").first()
        content = prompt.content if prompt else ""
        version = prompt.version if prompt else 0

    st.caption(f"Current version: {version}")
    edited = st.text_area("System prompt", content, height=400)
    if st.button("Save prompt"):
        with session_scope() as session:
            row = session.query(PromptTemplate).filter_by(name="fund_analyst_system_prompt").first()
            row.content = edited
            row.version += 1
            row.updated_by = "dashboard"
        st.success("Prompt saved.")
        st.rerun()

    st.divider()
    st.subheader("Append SME feedback directly")
    quick_feedback = st.text_input("Feedback note to append to the prompt")
    updated_by = st.text_input("Your name/id", value="sme_analyst", key="prompt_feedback_name")
    if st.button("Send feedback to prompt") and quick_feedback:
        with session_scope() as session:
            append_feedback_to_prompt(session, quick_feedback, updated_by)
        st.success("Feedback appended to the active prompt.")
        st.rerun()

# --------------------------------------------------------------------------
# Audit Log
# --------------------------------------------------------------------------
with tab_audit:
    st.subheader("Audit log (append-only)")
    with session_scope() as session:
        doc_ids = [d.id for d in session.query(Document.id).all()]
        doc_filter = st.selectbox("Filter by document", ["All"] + doc_ids)
        query = session.query(AuditLogEntry).order_by(AuditLogEntry.timestamp.desc())
        if doc_filter != "All":
            query = query.filter_by(document_id=doc_filter)
        entries = [
            {
                "timestamp": e.timestamp,
                "actor": e.actor,
                "step": e.step,
                "action": e.action,
                "result": e.result,
                "document_id": e.document_id,
                "details": json.loads(e.details or "{}"),
            }
            for e in query.all()
        ]
    if entries:
        st.dataframe(pd.DataFrame(entries), use_container_width=True, hide_index=True)
    else:
        st.info("No audit events yet.")

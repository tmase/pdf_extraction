"""
SQLAlchemy models.

This mirrors the "Structured Data Store (PostgreSQL)" box in the proposal's
architecture diagram, using SQLite for the local prototype. Every table here
maps 1:1 onto a Postgres table if/when this is promoted -- swap the
DATABASE_URL in config.py and nothing else needs to change.
"""
import datetime as dt
import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utcnow():
    return dt.datetime.utcnow()


class DocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    PROCESSED = "processed"
    ERROR = "error"


class FieldStatus(str, enum.Enum):
    AUTO_APPROVED = "auto_approved"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"       # human approved as-is
    CORRECTED = "corrected"     # human approved with a corrected value
    REJECTED = "rejected"


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    filename = Column(String, nullable=False)
    filepath = Column(String, nullable=False)
    sha256 = Column(String, index=True)
    page_count = Column(Integer, default=0)
    extraction_method = Column(String)  # e.g. "pypdf" / "pdfplumber+ocr"
    status = Column(Enum(DocumentStatus), default=DocumentStatus.PENDING)
    raw_text = Column(Text)
    uploaded_at = Column(DateTime, default=utcnow)
    processed_at = Column(DateTime)

    fields = relationship("ExtractedField", back_populates="document", cascade="all, delete-orphan")


class ExtractedField(Base):
    __tablename__ = "extracted_fields"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)

    field_name = Column(String, nullable=False)
    value = Column(String)                 # stored as string, cast by field type
    corrected_value = Column(String)
    source_context = Column(Text)          # sentence / table header the value came from
    confidence = Column(Float, default=0.0)

    status = Column(Enum(FieldStatus), default=FieldStatus.NEEDS_REVIEW)
    review_classification = Column(String)  # missing/ambiguous/conflicting/incorrectly_scoped
    validation_notes = Column(Text)

    reviewed_by = Column(String)
    reviewer_feedback = Column(Text)
    reviewed_at = Column(DateTime)

    created_at = Column(DateTime, default=utcnow)

    document = relationship("Document", back_populates="fields")


class AuditLogEntry(Base):
    """Append-only. No update/delete helper is exposed anywhere in the app."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=utcnow, index=True)
    actor = Column(String, nullable=False)       # e.g. "system", "fund_analyst_agent", "sme:jane"
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    step = Column(String, nullable=False)        # ingestion/extraction/llm_extraction/validation/review/indexing
    action = Column(String, nullable=False)
    result = Column(String)                      # success/failure/flagged
    details = Column(Text)                       # JSON blob


class GroundTruth(Base):
    """
    Historical, human-verified values used to build dynamic statistical
    ranges for validation (mean/std per field, optionally per fund), and to
    stand in for external reference data (e.g. SEC Form ADV) sources.
    """
    __tablename__ = "ground_truth"

    id = Column(Integer, primary_key=True)
    fund_name = Column(String, index=True)
    field_name = Column(String, index=True, nullable=False)
    value = Column(Float, nullable=False)
    reporting_date = Column(String)
    source = Column(String, default="human_verified")  # human_verified | form_adv | learned
    created_at = Column(DateTime, default=utcnow)


class PromptTemplate(Base):
    """
    Prompts/rules for the Fund Analyst Agent, editable via the front-end
    dashboard instead of living only in a markdown file, per the proposal.
    """
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    content = Column(Text, nullable=False)
    version = Column(Integer, default=1)
    updated_by = Column(String, default="system")
    updated_at = Column(DateTime, default=utcnow)


class EmailTask(Base):
    """Simulated 'email an SME' notification queue."""
    __tablename__ = "email_tasks"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utcnow)
    recipient = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    related_field_id = Column(Integer, ForeignKey("extracted_fields.id"), nullable=True)
    sent = Column(Boolean, default=False)

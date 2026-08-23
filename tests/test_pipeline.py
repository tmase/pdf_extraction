"""
Smoke + unit tests for the prototype. Run with:  pytest -q

Uses a throwaway sqlite file and vector store directory so it never touches
the interactive demo database in storage/.
"""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_DB_PATH = ROOT / "storage" / "test_app.db"
TEST_VECTOR_PATH = ROOT / "storage" / "test_vector_db"


@pytest.fixture(scope="module", autouse=True)
def isolated_storage():
    TEST_DB_PATH.unlink(missing_ok=True)
    shutil.rmtree(TEST_VECTOR_PATH, ignore_errors=True)

    import config
    config.DATABASE_URL = f"sqlite:///{TEST_DB_PATH}"
    config.VECTOR_DB_PATH = str(TEST_VECTOR_PATH)

    # Re-point the already-imported db.database module at the test DB.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import db.database as database
    database.engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False})
    database.SessionLocal = sessionmaker(bind=database.engine, autoflush=False, autocommit=False)

    database.init_db()
    yield

    TEST_DB_PATH.unlink(missing_ok=True)
    shutil.rmtree(TEST_VECTOR_PATH, ignore_errors=True)


@pytest.fixture(scope="module")
def sample_pdfs():
    from sample_data.generate_sample_pdfs import generate
    return generate()


def test_extraction_finds_text_and_tables(sample_pdfs):
    from extraction import text_extractor
    result = text_extractor.extract(str(sample_pdfs[0]))
    assert "Blue Ridge" in result.text
    assert result.page_count == 1
    assert len(result.tables) >= 1


def test_mock_agent_extracts_expected_fields(sample_pdfs):
    from db.database import session_scope
    from extraction import text_extractor
    from llm import fund_analyst_agent

    result = text_extractor.extract(str(sample_pdfs[0]))
    with session_scope() as session:
        fields, engine = fund_analyst_agent.extract_fields(session, result.text)

    assert engine == "mock"
    assert fields["fund_name"]["value"] == "Blue Ridge Long/Short Equity Fund"
    assert fields["short_exposure"]["value"] == "-58"
    assert fields["net_exposure"]["confidence"] > 0.5


def test_validators_flag_out_of_range_value():
    from db.database import session_scope
    from validation import validators

    with session_scope() as session:
        result = validators.validate_field(session, "net_exposure", "390")
    assert result.passed is False
    assert result.classification in ("incorrectly_scoped", "conflicting")


def test_validators_pass_reasonable_value():
    from db.database import session_scope
    from validation import validators

    with session_scope() as session:
        result = validators.validate_field(session, "net_exposure", "41")
    assert result.passed is True


def test_full_pipeline_clean_document_auto_approves(sample_pdfs):
    from db.database import session_scope
    from db.models import Document, DocumentStatus, FieldStatus
    from pipeline.orchestrator import process_document

    with session_scope() as session:
        summary = process_document(session, str(sample_pdfs[0]))

    assert summary["status"] == DocumentStatus.PROCESSED.value
    for name, info in summary["fields"].items():
        assert info["status"] == FieldStatus.AUTO_APPROVED.value, f"{name} unexpectedly needs review"


def test_full_pipeline_flags_planted_anomaly(sample_pdfs):
    from db.database import session_scope
    from db.models import DocumentStatus, FieldStatus
    from pipeline.orchestrator import process_document

    with session_scope() as session:
        summary = process_document(session, str(sample_pdfs[1]))

    assert summary["status"] == DocumentStatus.NEEDS_REVIEW.value
    assert summary["fields"]["net_exposure"]["status"] == FieldStatus.NEEDS_REVIEW.value


def test_review_decision_feeds_ground_truth_and_resolves_document():
    from db.database import session_scope
    from db.models import Document, DocumentStatus, ExtractedField, FieldStatus, GroundTruth
    from pipeline.orchestrator import record_review_decision

    with session_scope() as session:
        flagged_fields = session.query(ExtractedField).filter_by(status=FieldStatus.NEEDS_REVIEW).all()
        assert flagged_fields, "expected at least one field needing review from the anomalous sample"
        ids = [f.id for f in flagged_fields]
        doc_id = flagged_fields[0].document_id

    for field_id in ids:
        with session_scope() as session:
            record_review_decision(session, field_id, "correct", "test_reviewer",
                                    corrected_value="42", feedback="test correction")

    with session_scope() as session:
        doc = session.get(Document, doc_id)
        assert doc.status == DocumentStatus.PROCESSED
        learned = session.query(GroundTruth).filter_by(source="learned").count()
        assert learned >= 1


def test_prompt_feedback_is_appended():
    from db.database import session_scope
    from pipeline.orchestrator import append_feedback_to_prompt

    with session_scope() as session:
        prompt = append_feedback_to_prompt(session, "Always double-check share-class scope.", "test_reviewer")
        content = prompt.content  # read while still attached to the session
    assert "double-check share-class scope" in content


def test_vector_store_indexes_and_queries(sample_pdfs):
    # Deliberately off-topic vocabulary vs. the fund reports already indexed
    # by earlier tests, so the (intentionally simple, offline) hashing
    # embedder isn't asked to disambiguate near-identical financial jargon --
    # this test is checking store/retrieve plumbing, not embedding quality.
    from vectorstore import store as vector_store

    vector_store.index_document(999, "manual_test.pdf", "a wandering penguin colony migrating across Antarctic ice")
    hits = vector_store.query("penguin colony Antarctic", k=1)
    assert hits
    assert hits[0]["metadata"]["document_id"] == 999


def test_audit_log_is_populated_for_processed_document(sample_pdfs):
    from db.database import session_scope
    from db.models import AuditLogEntry

    with session_scope() as session:
        count = session.query(AuditLogEntry).filter_by(step="pipeline", action="complete").count()
    assert count >= 2  # both sample documents completed the pipeline

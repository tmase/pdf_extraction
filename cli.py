"""
Command-line entry point for the prototype.

Usage:
    python cli.py init-db                 # create tables + seed prompts/ground truth
    python cli.py generate-samples        # write demo PDFs to sample_data/pdfs/
    python cli.py ingest <file-or-folder> # run the full pipeline on one or more PDFs
    python cli.py review-queue            # print fields currently awaiting human review
    python cli.py audit-log [--doc N]     # print the audit trail
    python cli.py serve-api               # run the FastAPI app (uvicorn)
    python cli.py serve-dashboard         # print the command to launch the Streamlit dashboard
"""
import argparse
import json
import sys
from pathlib import Path

from db.database import init_db, session_scope
from db.models import AuditLogEntry, ExtractedField, FieldStatus
from pipeline.orchestrator import process_document


def cmd_init_db(_args):
    init_db()
    print("Database initialized (tables created, default prompt + ground truth seeded).")


def cmd_generate_samples(_args):
    from sample_data.generate_sample_pdfs import generate
    paths = generate()
    for p in paths:
        print(f"generated {p}")


def cmd_ingest(args):
    init_db()
    target = Path(args.path)
    if target.is_dir():
        files = sorted(target.glob("*.pdf"))
    else:
        files = [target]

    if not files:
        print(f"No PDF files found at {target}", file=sys.stderr)
        sys.exit(1)

    for filepath in files:
        with session_scope() as session:
            summary = process_document(session, str(filepath))
        print(f"\n=== {summary['filename']} (document #{summary['document_id']}) -> {summary['status']} ===")
        for field_name, info in summary["fields"].items():
            marker = "OK" if info["status"] == "auto_approved" else "REVIEW"
            print(f"  [{marker:6}] {field_name:22} = {info['value']!r} (confidence {info['confidence']:.2f})")


def cmd_review_queue(_args):
    with session_scope() as session:
        rows = session.query(ExtractedField).filter_by(status=FieldStatus.NEEDS_REVIEW).all()
        if not rows:
            print("Review queue is empty.")
            return
        for row in rows:
            print(f"#{row.id} doc={row.document_id} field={row.field_name} value={row.value!r} "
                  f"classification={row.review_classification} notes={row.validation_notes!r}")


def cmd_audit_log(args):
    with session_scope() as session:
        query = session.query(AuditLogEntry).order_by(AuditLogEntry.timestamp)
        if args.doc:
            query = query.filter_by(document_id=args.doc)
        for entry in query.all():
            details = json.loads(entry.details or "{}")
            print(f"[{entry.timestamp}] actor={entry.actor} step={entry.step} action={entry.action} "
                  f"result={entry.result} doc={entry.document_id} details={details}")


def cmd_serve_api(_args):
    import uvicorn
    init_db()
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)


def cmd_serve_dashboard(_args):
    print("Run this from a shell (Streamlit manages its own process):")
    print("    streamlit run dashboard/app.py")


def main():
    parser = argparse.ArgumentParser(description="UVIMCO PDF risk report extraction pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)
    sub.add_parser("generate-samples").set_defaults(func=cmd_generate_samples)

    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("path", help="A PDF file or a folder of PDFs")
    p_ingest.set_defaults(func=cmd_ingest)

    sub.add_parser("review-queue").set_defaults(func=cmd_review_queue)

    p_audit = sub.add_parser("audit-log")
    p_audit.add_argument("--doc", type=int, default=None, help="Filter to one document id")
    p_audit.set_defaults(func=cmd_audit_log)

    sub.add_parser("serve-api").set_defaults(func=cmd_serve_api)
    sub.add_parser("serve-dashboard").set_defaults(func=cmd_serve_dashboard)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

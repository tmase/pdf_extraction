"""
Human-in-the-loop notification.

Whenever the Fund Analyst Agent (Step 2) or the validators (Step 3) flag a
field, this creates a review task and "sends an email" to a subject matter
expert. There's no real mail server wired up in the prototype, so sending is
simulated (printed + stored in the email_tasks table); swap in SMTP/SES
by replacing the body of notify_sme's send step.
"""
import config
from audit import log
from db.models import EmailTask


def notify_sme(session, *, document_id: int, field, classification: str, reason: str):
    subject = f"[Review needed] {field.field_name} on document #{document_id} ({classification})"
    body = (
        f"The pipeline flagged '{field.field_name}' for review.\n\n"
        f"Reason: {reason}\n"
        f"Extracted value: {field.value!r}\n"
        f"Confidence: {field.confidence:.2f}\n"
        f"Source context: {field.source_context}\n\n"
        f"Open the review dashboard to approve, correct, or reject this value."
    )
    task = EmailTask(recipient=config.SME_EMAIL, subject=subject, body=body, related_field_id=field.id)
    session.add(task)
    session.flush()

    print(f"[SIMULATED EMAIL] to={config.SME_EMAIL} subject={subject!r}")

    log(
        session,
        actor="system",
        step="review",
        action="notify_sme",
        result="flagged",
        document_id=document_id,
        details={"field": field.field_name, "classification": classification, "reason": reason},
    )
    return task

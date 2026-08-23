"""
Append-only audit logging.

Every pipeline component calls log() at each step it performs, per the
proposal's requirement: "The entire workflow should include an audit log
with the actions taken at each step." There is deliberately no update/delete
function anywhere in this module or the app.
"""
import json

from db.models import AuditLogEntry


def log(session, *, actor: str, step: str, action: str, result: str = "success",
        document_id: int | None = None, details: dict | None = None):
    entry = AuditLogEntry(
        actor=actor,
        step=step,
        action=action,
        result=result,
        document_id=document_id,
        details=json.dumps(details or {}, default=str),
    )
    session.add(entry)
    session.flush()
    return entry

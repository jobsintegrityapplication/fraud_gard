"""Sanitized security audit events stored in the existing append-only audit table."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..db import conn
from ..domain.redact import sanitize_audit_detail


def record_event(actor: str, action: str, entity_type: str, entity_id: str | None = None, detail: Any = None) -> None:
    """Persist an audit event; callers must pass pseudonymous actor IDs."""
    c = conn()
    try:
        c.execute(
            'INSERT INTO audit(actor,action,entity_type,entity_id,detail,ts) VALUES(?,?,?,?,?,?)',
            (actor or 'system', action.upper(), entity_type, entity_id or '', sanitize_audit_detail(detail), datetime.now(timezone.utc).isoformat()),
        )
        c.commit()
    finally:
        c.close()

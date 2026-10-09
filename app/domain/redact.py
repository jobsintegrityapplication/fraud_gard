"""Redact sensitive identifiers before logging or writing audit detail."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from typing import Any

SENSITIVE_KEYS = frozenset({
    'imei', 'msisdn', 'imsi', 'ip', 'pan', 'card_number', 'ssn', 'national_id',
    'phone', 'mobile', 'device_id', 'iccid', 'customer_id', 'customer_identifier',
    'account_id', 'sim_id', 'ip_address', 'remote_ip', 'source_ip',
})
_MASK_KEY = os.getenv('PII_MASK_SECRET', '').encode('utf-8') or secrets.token_bytes(32)
SECRET_KEYS = frozenset({
    'password', 'passwd', 'token', 'access_token', 'refresh_token', 'id_token',
    'authorization', 'cookie', 'set_cookie', 'client_secret', 'secret', 'api_key',
    'private_key', 'credential', 'credentials', 'jwt', 'bearer',
})
_SENSITIVE_KEY_PARTS = ('imei', 'msisdn', 'imsi', 'ipv4', 'ipv6', 'ipaddress', 'ipaddr', 'remoteip', 'sourceip', 'clientip', 'publicip', 'privateip', 'srcip', 'dstip', 'xforwardedfor', 'customer', 'account', 'phone', 'mobile', 'deviceid', 'simid', 'iccid')
_SAFE_AUDIT_STRING_KEYS = frozenset({
    'role', 'roles', 'actor_role', 'decision', 'status', 'state', 'action', 'entity_type',
    'source', 'kind', 'version', 'model_version', 'model_id', 'report_version', 'case_id',
    'job_id', 'queue_id', 'gate', 'failed_gates', 'fields', 'dataset_version',
    'feature_version', 'algorithm', 'label', 'reason_code',
})
_SENSITIVE_TEXT = re.compile(
    r'(?i)(?:bearer\s+\S+|\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b|'
    r'\b(?:IMEI|IMSI|MSISDN|CUSTOMER|ACCOUNT|SIM|IP)\s*[:=]\s*[^\s,;]+)'
    r'|\b\d{8,18}\b|\b(?:\d{1,3}\.){3}\d{1,3}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}'
)


def fingerprint(value: Any) -> str:
    text = '' if value is None else str(value)
    digest = hmac.new(_MASK_KEY, text.encode('utf-8'), hashlib.sha256).hexdigest()[:12]
    return f'redacted:{digest}'


def _scrub_text(value: str, key: str) -> str:
    if _SENSITIVE_TEXT.search(value):
        return '[omitted]'
    if key in _SAFE_AUDIT_STRING_KEYS or (key in {'roles', 'fields', 'failed_gates'} and len(value) <= 48):
        return value[:160]
    # Omit all other strings: even short values could be a password or secret.
    return '[omitted]'


def _scrub(obj: Any, parent_key: str = '') -> Any:
    if isinstance(obj, dict):
        out = {}
        for key, val in obj.items():
            lk = str(key).lower()
            normalized = ''.join(ch for ch in lk if ch.isalnum())
            if lk in SECRET_KEYS or any(s in normalized for s in ('password', 'token', 'secret', 'credential', 'apikey', 'privatekey', 'jwt', 'bearer')):
                out[key] = '[omitted]'
            elif lk in SENSITIVE_KEYS or normalized == 'ip' or any(s in normalized for s in _SENSITIVE_KEY_PARTS):
                out[key] = fingerprint(val)
            elif lk in {'reason', 'notes', 'comment', 'message', 'detail', 'raw', 'value'} and isinstance(val, str):
                out[key] = '[omitted]'
            else:
                out[key] = _scrub(val, lk)
        return out
    if isinstance(obj, (list, tuple)):
        return [_scrub(v, parent_key) for v in obj]
    if isinstance(obj, str):
        return _scrub_text(obj, parent_key)
    if isinstance(obj, int) and not isinstance(obj, bool) and len(str(abs(obj))) >= 8:
        return '[omitted]'
    if isinstance(obj, float) and abs(obj) >= 10_000_000:
        return '[omitted]'
    return obj


def sanitize_audit_detail(detail: Any) -> str:
    if detail is None:
        return ''
    if isinstance(detail, str):
        try:
            parsed = json.loads(detail)
        except json.JSONDecodeError:
            # Free-form text cannot be reliably scrubbed for embedded credentials
            # or identifiers. Do not persist it in an audit event.
            return '[omitted]'
        return json.dumps(_scrub(parsed), default=str)[:8000]
    return json.dumps(_scrub(detail), default=str)[:8000]

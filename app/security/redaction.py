"""Role-aware response redaction for personal and device identifiers."""
from __future__ import annotations

import ipaddress
import re
from typing import Any

from ..domain.redact import fingerprint

FULL_ACCESS_ROLES = frozenset({'ADMIN', 'FRAUD_ANALYST', 'SENIOR_ANALYST'})
SENSITIVE_NAMES = (
    'imei', 'msisdn', 'phone', 'mobile', 'imsi', 'ip', 'customer_id',
    'customer_identifier', 'account_id', 'device_id', 'sim_id', 'iccid',
)


def _is_sensitive_key(key: Any) -> bool:
    name = re.sub(r'[^a-z0-9]', '', str(key).lower())
    if name in {'ip', 'ipv4', 'ipv6', 'xforwardedfor'} or name.endswith(('ipaddress', 'ipaddr', 'remoteip', 'sourceip', 'clientip', 'publicip', 'privateip', 'srcip', 'dstip')):
        return True
    return any(re.sub(r'[^a-z0-9]', '', part) in name for part in SENSITIVE_NAMES if part not in {'ip', 'customer_id', 'customer_identifier', 'account_id'}) or 'customer' in name or 'account' in name


def _mask_prefixed(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    match = re.match(r'^(IMEI|IMSI|MSISDN|SIM|IP|CUSTOMER|ACCOUNT):(.+)$', value, re.IGNORECASE)
    if not match:
        return value
    return match.group(1) + ':' + mask_identifier(match.group(2), match.group(1))


def mask_identifier(value: Any, key: Any) -> Any:
    if value is None or value == '':
        return value
    name = str(key).lower()
    text = str(value)
    if 'ip' in name or 'forwardedfor' in name:
        try:
            address = ipaddress.ip_address(text)
            if address.version == 4:
                return '.'.join(text.split('.')[:2] + ['×', '×'])
            return str(address).split(':')[0] + ':…'
        except ValueError:
            return '•••'
    # Stable pseudonyms allow analysts in restricted roles to correlate records
    # without revealing the source identifier.
    if 'customer' in name or 'account' in name:
        return 'ref:' + fingerprint(text).split(':', 1)[-1][:10]
    return ('•' * max(4, min(len(text) - 4, 12)) + text[-4:]) if len(text) > 4 else '••••'


def redact_for_roles(value: Any, roles: frozenset[str] | set[str]) -> Any:
    if set(roles).intersection(FULL_ACCESS_ROLES):
        return value
    if isinstance(value, dict):
        entity_type = str(value.get('type') or value.get('entity_type') or '').lower()
        field_name = str(value.get('field') or value.get('name') or value.get('kind') or '').lower()
        sensitive_entity = any(kind in entity_type for kind in ('imei', 'imsi', 'msisdn', 'sim', 'ip', 'customer', 'account', 'device'))
        sensitive_value = _is_sensitive_key(field_name)
        result = {}
        for key, item in value.items():
            if str(key).lower() in {'reason', 'notes', 'comment', 'detail', 'raw', 'title', 'description', 'message', 'display_name'} and isinstance(item, str):
                result[key] = '[omitted]'
            elif key in {'actor', 'reviewer', 'updated_by', 'created_by'} and isinstance(item, str):
                result[key] = item if item in {'system', 'anonymous'} or item.startswith(('user:', 'legacy:')) else 'user:' + fingerprint(item).split(':', 1)[-1]
            elif ((sensitive_value and key == 'value') or _is_sensitive_key(key) or (sensitive_entity and key in {'id', 'node_id', 'entity_id'})) and not isinstance(item, (dict, list)):
                result[key] = mask_identifier(item, key if _is_sensitive_key(key) else (entity_type or field_name))
            elif key == 'detail' and isinstance(item, str):
                result[key] = '[omitted]'
            elif key in {'member_ids', 'node_ids'} and isinstance(item, list):
                result[key] = [_mask_prefixed(v) for v in item]
            else:
                result[key] = redact_for_roles(item, roles)
        return result
    if isinstance(value, list):
        return [_mask_prefixed(redact_for_roles(item, roles)) for item in value]
    return _mask_prefixed(value)

from __future__ import annotations

FIREABLE_STATES = frozenset({'ACTIVE'})


def _as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clause_hits(tx: dict, signature: dict) -> tuple[int, int, list[str]]:
    sig = signature or {}
    clauses = []
    hits = []

    if 'imei_prefix' in sig:
        clauses.append('imei_prefix')
        if str(tx.get('imei') or '').startswith(str(sig['imei_prefix'])):
            hits.append('imei_prefix')
    if 'ip_prefix' in sig:
        clauses.append('ip_prefix')
        if str(tx.get('ip') or '').startswith(str(sig['ip_prefix'])):
            hits.append('ip_prefix')
    if 'merchant_id' in sig:
        clauses.append('merchant_id')
        if str(tx.get('merchant_id') or '') == str(sig['merchant_id']):
            hits.append('merchant_id')
    if 'location' in sig:
        clauses.append('location')
        loc = str(tx.get('location') or tx.get('location_code') or '')
        if loc == str(sig['location']):
            hits.append('location')
    if 'amount_min' in sig:
        clauses.append('amount_min')
        if _as_float(tx.get('amount')) >= _as_float(sig['amount_min']):
            hits.append('amount_min')
    if 'amount_max' in sig:
        clauses.append('amount_max')
        if _as_float(tx.get('amount')) <= _as_float(sig['amount_max']):
            hits.append('amount_max')
    extra = sig.get('equals') if isinstance(sig.get('equals'), dict) else {}
    for key, expected in extra.items():
        clauses.append(key)
        if str(tx.get(key) or '') == str(expected):
            hits.append(key)
    return len(hits), max(len(clauses), 1 if clauses else 0), hits


def pattern_can_fire(pattern: dict) -> bool:
    return str(pattern.get('state') or '').upper() in FIREABLE_STATES


def score_against_pattern(tx: dict, pattern: dict) -> tuple[float, str | None]:
    if not pattern_can_fire(pattern):
        return 0.0, None
    signature = pattern.get('signature') or {}
    hits, n_clauses, matched = clause_hits(tx, signature)
    if n_clauses == 0:
        return 0.0, None
    score = hits / n_clauses
    threshold = _as_float(pattern.get('threshold'), 1.0)
    if score + 1e-12 < threshold:
        return 0.0, None
    action = pattern.get('policy_action') or 'ALERT'
    return float(score), f"registry:{pattern.get('pattern_id')} {action} matched={','.join(matched)}"


def score_transaction(tx: dict, patterns: list[dict]) -> tuple[float, str | None]:
    best = 0.0
    why = None
    for pattern in patterns:
        score, reason = score_against_pattern(tx, pattern)
        if score > best:
            best = score
            why = reason
    return best, why

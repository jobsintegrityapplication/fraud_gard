from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .. import config
from .candidates import FraudPatternCandidate
from .lifecycle import apply_transition, validate_state


def _store_path() -> Path:
    dest = Path(config.MODEL_DIR) / 'discovery'
    dest.mkdir(parents=True, exist_ok=True)
    return dest / 'candidates.json'


def load_candidates() -> dict[str, dict]:
    path = _store_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    return {row['pattern_id']: row for row in data.get('candidates', [])}


def save_candidates(candidates: list[FraudPatternCandidate | dict]) -> Path:
    path = _store_path()
    rows = [c.to_dict() if isinstance(c, FraudPatternCandidate) else c for c in candidates]
    payload = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'candidates': rows,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
    return path


def upsert_candidates(candidates: list[FraudPatternCandidate]) -> list[dict]:
    existing = load_candidates()
    for cand in candidates:
        existing[cand.pattern_id] = cand.to_dict()
    save_candidates(list(existing.values()))
    return [existing[c.pattern_id] for c in candidates]


def transition_pattern(pattern_id: str, target_state: str) -> dict:
    existing = load_candidates()
    if pattern_id not in existing:
        raise KeyError(pattern_id)
    row = existing[pattern_id]
    row['state'] = apply_transition(row.get('state', 'DISCOVERED'), target_state)
    existing[pattern_id] = row
    save_candidates(list(existing.values()))
    return row


def get_pattern(pattern_id: str) -> dict:
    existing = load_candidates()
    if pattern_id not in existing:
        raise KeyError(pattern_id)
    validate_state(existing[pattern_id]['state'])
    return existing[pattern_id]

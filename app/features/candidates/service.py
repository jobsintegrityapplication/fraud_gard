from __future__ import annotations

from pathlib import Path

import pandas as pd

from ... import config
from ..registry import FeatureRegistry, production_registry
from ..windows import FEATURE_SET_VERSION, FeatureSpec, LLM_SOURCES
from .lifecycle import InvalidFeatureTransition, apply_transition
from .propose import generate_proposals
from .store import get_candidate, list_candidates, upsert_candidate
from .templates import TEMPLATE_KEYS, compute_candidate_column
from .validate import CandidateValidationGates, validate_candidate_feature


class CandidateFeatureError(ValueError):
    pass


def _assert_llm_blocked_from_production(source: str):
    src = (source or '').strip().lower()
    if src in LLM_SOURCES:
        raise PermissionError(
            'LLMs cannot modify production feature code or register production features; '
            'only a description suggestion is allowed'
        )


def suggest_description(candidate_id: str, description: str, *, source: str = 'llm') -> dict:
    """LLM/human may update description only — never template, params, or production flag."""
    src = (source or '').strip().lower()
    if src not in LLM_SOURCES and src not in {'human', 'rules', 'analyst'}:
        raise CandidateFeatureError(f'unsupported description source: {source}')
    row = get_candidate(candidate_id)
    if row is None:
        raise KeyError(candidate_id)
    allowed = set(row.get('llm_may_edit') or ['description'])
    if 'description' not in allowed:
        raise PermissionError('description edits are not allowed for this candidate')
    # Snapshot protected fields — LLM cannot change them even if payload tries.
    protected = {
        'name': row['name'],
        'template': row['template'],
        'params': row['params'],
        'status': row['status'],
        'production': False,
        'origin': row['origin'],
        'origin_ref': row['origin_ref'],
    }
    row['description'] = str(description or '').strip()
    row['description_source'] = src
    row.update(protected)
    return upsert_candidate(row)


def propose_candidates(
    *,
    patterns: list[dict] | None = None,
    persist: bool = True,
    **kwargs,
) -> list[dict]:
    if patterns is None:
        try:
            from ...registry.service import list_patterns
            patterns = list_patterns()
        except Exception:
            patterns = []
    proposals = generate_proposals(patterns=patterns, **kwargs)
    if not persist:
        return proposals
    saved = []
    for p in proposals:
        existing = [c for c in list_candidates() if c['name'] == p['name']]
        if existing and existing[0]['status'] in {'ACTIVE', 'ACCEPTED', 'VALIDATING'}:
            continue
        if existing:
            p['candidate_id'] = existing[0]['candidate_id']
        saved.append(upsert_candidate(p))
    return saved


def transition(candidate_id: str, target: str, *, actor: str = 'system') -> dict:
    row = get_candidate(candidate_id)
    if row is None:
        raise KeyError(candidate_id)
    row['status'] = apply_transition(row['status'], target)
    row['updated_by'] = actor
    return upsert_candidate(row)


def run_validation(candidate_id: str, raw: pd.DataFrame, gates: CandidateValidationGates | None = None) -> dict:
    row = get_candidate(candidate_id)
    if row is None:
        raise KeyError(candidate_id)
    if row['status'] == 'PROPOSED':
        row = transition(candidate_id, 'VALIDATING')
    elif row['status'] not in {'VALIDATING', 'ACCEPTED', 'ACTIVE'}:
        raise InvalidFeatureTransition(f'cannot validate from status {row["status"]}')
    decision = validate_candidate_feature(candidate=row, raw=raw, gates=gates)
    row = get_candidate(candidate_id)
    row['validation'] = decision
    if decision['passed']:
        row['status'] = apply_transition(row['status'], 'ACCEPTED') if row['status'] == 'VALIDATING' else row['status']
    else:
        if row['status'] == 'VALIDATING':
            row['status'] = apply_transition(row['status'], 'REJECTED')
    saved = upsert_candidate(row)
    return {'candidate': saved, 'validation': decision}


def accept_candidate(candidate_id: str) -> dict:
    row = get_candidate(candidate_id)
    if row is None:
        raise KeyError(candidate_id)
    if row['status'] == 'VALIDATING' and (row.get('validation') or {}).get('passed'):
        return transition(candidate_id, 'ACCEPTED')
    if row['status'] != 'ACCEPTED':
        raise InvalidFeatureTransition('candidate must pass validation before ACCEPTED')
    return row


def reject_candidate(candidate_id: str, reason: str = '') -> dict:
    row = get_candidate(candidate_id)
    if row is None:
        raise KeyError(candidate_id)
    if row['status'] == 'REJECTED':
        return row
    row['status'] = apply_transition(row['status'], 'REJECTED')
    validation = dict(row.get('validation') or {})
    validation['rejection_reason'] = reason
    row['validation'] = validation
    row['production'] = False
    return upsert_candidate(row)


def activate_candidate(candidate_id: str, *, source: str = 'reviewed_code') -> dict:
    """
    Move ACCEPTED → ACTIVE in the candidate sandbox registry.
    Never writes production feature code. production flag stays False.
    """
    src = (source or '').strip().lower()
    if src in LLM_SOURCES:
        raise PermissionError('LLM cannot activate features into any registry as production code')
    row = get_candidate(candidate_id)
    if row is None:
        raise KeyError(candidate_id)
    if row['status'] != 'ACCEPTED':
        raise InvalidFeatureTransition('only ACCEPTED candidates can become ACTIVE')
    if row.get('template') not in TEMPLATE_KEYS:
        raise PermissionError('ACTIVE requires a reviewed deterministic template')
    if row.get('production'):
        raise PermissionError('candidate records must not set production=True; promote via reviewed specs.py')

    # Sandbox registry only — production_registry() is never mutated here.
    sandbox = FeatureRegistry()
    for spec in production_registry()._specs.values():
        sandbox.register(spec, source='reviewed_code')
    cand_spec = FeatureSpec(
        name=row['name'],
        entity_level=row.get('entity_level') or 'TRANSACTION',
        version=FEATURE_SET_VERSION,
        description=row.get('description') or '',
        dtype='float',
        production=False,
        owner='candidate-pipeline',
    )
    sandbox.register(cand_spec, source='candidate_pipeline')

    # Persist activation marker (not production code).
    marker = Path(config.MODEL_DIR) / 'features' / 'candidates' / f"{row['name']}.active.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    import json
    marker.write_text(json.dumps({
        'candidate_id': row['candidate_id'],
        'name': row['name'],
        'template': row['template'],
        'params': row['params'],
        'production': False,
        'activated_at': datetime_now(),
        'note': 'ACTIVE candidate — deterministic template only; not production feature code',
    }, indent=2), encoding='utf-8')

    row['status'] = apply_transition(row['status'], 'ACTIVE')
    row['production'] = False
    return upsert_candidate(row)


def datetime_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def compute_active_candidates(raw: pd.DataFrame) -> pd.DataFrame:
    """Compute ACTIVE candidate columns beside production features (sandbox)."""
    from ..engine import compute_features
    base = compute_features(raw)
    out = base.copy()
    for cand in list_candidates(status='ACTIVE'):
        try:
            out[cand['name']] = compute_candidate_column(base, cand['template'], cand.get('params') or {})
        except Exception:
            out[cand['name']] = 0.0
    out.attrs['candidate_features'] = [c['name'] for c in list_candidates(status='ACTIVE')]
    return out


def refuse_llm_production_code_write(path: str | Path | None = None) -> None:
    """Guard used by tests/API: LLM must never write production feature modules."""
    target = Path(path) if path else Path(__file__).resolve().parents[1] / 'specs.py'
    raise PermissionError(
        f'LLM is forbidden from modifying production feature code at {target}; '
        'use candidate proposals with deterministic templates only'
    )

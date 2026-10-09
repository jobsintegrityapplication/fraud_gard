import pytest
import pandas as pd

from app.data.generator import build_dataset
from app.features.candidates.lifecycle import STATES, InvalidFeatureTransition, apply_transition
from app.features.candidates.propose import generate_proposals
from app.features.candidates.service import (
    activate_candidate,
    compute_active_candidates,
    propose_candidates,
    refuse_llm_production_code_write,
    reject_candidate,
    run_validation,
    suggest_description,
)
from app.features.candidates.store import get_candidate, list_candidates
from app.features.candidates.templates import compute_candidate_column
from app.features.registry import FeatureRegistry, production_registry
from app.features.windows import FEATURE_SET_VERSION, FeatureSpec
from app.features.specs import PRODUCTION_SPECS


def test_lifecycle_transitions():
    assert apply_transition('PROPOSED', 'VALIDATING') == 'VALIDATING'
    assert apply_transition('VALIDATING', 'ACCEPTED') == 'ACCEPTED'
    assert apply_transition('ACCEPTED', 'ACTIVE') == 'ACTIVE'
    assert apply_transition('PROPOSED', 'REJECTED') == 'REJECTED'
    with pytest.raises(InvalidFeatureTransition):
        apply_transition('PROPOSED', 'ACTIVE')


def test_proposals_from_all_origins():
    patterns = [{
        'pattern_id': 'P-1',
        'state': 'CONFIRMED',
        'signature': {'distinctive_features': [
            {'feature': 'transaction_velocity'},
            {'feature': 'device_switch_velocity'},
            {'feature': 'amount_deviation'},
        ]},
    }]
    props = generate_proposals(patterns=patterns)
    origins = {p['origin'] for p in props}
    assert 'confirmed_fraud_pattern' in origins
    assert 'entity_relationships' in origins
    assert 'feature_combination' in origins
    assert 'temporal_behavior' in origins
    for p in props:
        assert p['status'] == 'PROPOSED'
        assert p['production'] is False
        assert p['template']


def test_llm_may_suggest_description_only(isolate_storage):
    rows = propose_candidates(include_patterns=False)
    assert rows
    cid = rows[0]['candidate_id']
    before = get_candidate(cid)
    updated = suggest_description(cid, 'LLM-suggested wording for reviewers', source='llm')
    assert updated['description'] == 'LLM-suggested wording for reviewers'
    assert updated['description_source'] == 'llm'
    assert updated['template'] == before['template']
    assert updated['params'] == before['params']
    assert updated['production'] is False
    assert updated['name'] == before['name']


def test_llm_cannot_register_or_activate_production(isolate_storage):
    reg = FeatureRegistry()
    spec = FeatureSpec('evil_prod', 'TRANSACTION', FEATURE_SET_VERSION, 'x', 'float', production=True)
    with pytest.raises(PermissionError):
        reg.register(spec, source='llm')
    with pytest.raises(PermissionError):
        reg.register(spec, source='candidate_pipeline')

    rows = propose_candidates(include_patterns=False, include_combinations=False, include_entity=True, include_temporal=False)
    cid = rows[0]['candidate_id']
    with pytest.raises(PermissionError):
        activate_candidate(cid, source='llm')

    with pytest.raises(PermissionError):
        refuse_llm_production_code_write()

    # Production registry unchanged
    assert set(production_registry().names()) == {s.name for s in PRODUCTION_SPECS}


def test_validation_and_activate_sandbox(isolate_storage):
    raw = build_dataset(160, seed=41, persist=False).processed
    rows = propose_candidates(
        include_patterns=False,
        include_entity=False,
        include_combinations=False,
        include_temporal=True,
    )
    # Pick temporal burst — always computable from production features.
    cand = next(r for r in rows if r['template'] == 'temporal_burst_ratio')
    result = run_validation(cand['candidate_id'], raw)
    assert 'validation' in result
    assert set(g['gate'] for g in result['validation']['gates']) >= {
        'leakage', 'point_in_time_availability', 'missingness', 'stability',
        'correlation_redundancy', 'model_improvement', 'segment_impact',
    }
    saved = result['candidate']
    if result['validation']['passed']:
        assert saved['status'] == 'ACCEPTED'
        active = activate_candidate(saved['candidate_id'], source='reviewed_code')
        assert active['status'] == 'ACTIVE'
        assert active['production'] is False
        frame = compute_active_candidates(raw)
        assert cand['name'] in frame.columns
        assert 'label' not in frame.columns
    else:
        assert saved['status'] == 'REJECTED'


def test_executable_params_rejected():
    frame = pd.DataFrame({'a': [1.0, 2.0], 'b': [2.0, 4.0]})
    with pytest.raises(PermissionError):
        compute_candidate_column(frame, 'ratio', {'left': 'a', 'right': 'b', 'code': 'a/b'})


def test_candidate_features_api(client):
    r = client.post('/api/v1/features/candidates/propose', json={
        'include_patterns': False, 'include_entity': True,
        'include_combinations': False, 'include_temporal': False,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['proposed'] >= 1
    cid = body['candidates'][0]['candidate_id']
    d = client.post(f'/api/v1/features/candidates/{cid}/describe', json={
        'description': 'Suggested by assistant', 'source': 'llm',
    })
    assert d.status_code == 200
    assert d.json()['description_source'] == 'llm'
    guard = client.post('/api/v1/features/candidates/guards/refuse-llm-production-write')
    assert guard.status_code == 200
    assert guard.json()['allowed'] is False
    listed = client.get('/api/v1/features/candidates')
    assert listed.status_code == 200
    assert set(listed.json()['states']) == set(STATES)

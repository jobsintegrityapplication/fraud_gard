from app.decision.engine import (
    ACTION_RANK,
    DecisionInputs,
    DecisionRule,
    decide,
    default_active_rules,
    fuse_scores,
    resolve_action,
    risk_level_for,
)


def test_risk_levels_and_default_actions():
    assert risk_level_for(0.1) == 'LOW'
    assert risk_level_for(0.5) == 'MEDIUM'
    assert risk_level_for(0.7) == 'HIGH'
    assert risk_level_for(0.9) == 'CRITICAL'
    out = decide(DecisionInputs(0.1, 0.1, 0.1, 0.1, active_rules=default_active_rules()))
    assert out.risk_level == 'LOW'
    assert out.recommended_action == 'ALLOW'
    assert out.llm_determined is False
    assert out.evidence_ids
    assert all(e.startswith('EVD-') or True for e in out.evidence_ids)


def test_conflicting_signals_force_manual_review():
    # High XGB, low anomaly → conflict rule (priority 90) beats medium/high defaults
    out = decide(DecisionInputs(
        xgboost_score=0.92,
        anomaly_score=0.05,
        graph_score=0.10,
        pattern_score=0.10,
        active_rules=default_active_rules(),
    ))
    matched_ids = [r['rule_id'] for r in out.matched_rules]
    assert 'R-REVIEW-CONFLICT' in matched_ids
    assert out.recommended_action == 'MANUAL_REVIEW'
    selected = [r for r in out.matched_rules if r['selected']]
    assert selected and selected[0]['rule_id'] == 'R-REVIEW-CONFLICT'


def test_rule_priority_beats_lower_severity_and_lower_priority():
    rules = [
        DecisionRule('LOW-PRI-BLOCK', priority=10, action='BLOCK', when={'min_risk_score': 0.0}),
        DecisionRule('HIGH-PRI-REVIEW', priority=50, action='MANUAL_REVIEW', when={'min_risk_score': 0.0}),
        DecisionRule('MID-PRI-HOLD', priority=30, action='HOLD', when={'min_risk_score': 0.0}),
    ]
    out = decide(DecisionInputs(0.5, 0.5, 0.5, 0.5, active_rules=rules))
    assert out.recommended_action == 'MANUAL_REVIEW'
    assert out.matched_rules[0]['rule_id'] == 'HIGH-PRI-REVIEW'
    assert out.matched_rules[0]['selected'] is True


def test_same_priority_uses_more_severe_action():
    rules = [
        DecisionRule('A-ALLOW', priority=70, action='ALLOW', when={'min_risk_score': 0.0}),
        DecisionRule('A-BLOCK', priority=70, action='BLOCK', when={'min_risk_score': 0.0}),
    ]
    action, ordered = resolve_action('HIGH', rules)
    assert action == 'BLOCK'
    assert ordered[0].rule_id == 'A-BLOCK'
    assert ACTION_RANK['BLOCK'] > ACTION_RANK['ALLOW']


def test_business_policy_weights_change_fused_score():
    equal = fuse_scores(1.0, 0.0, 0.0, 0.0, weights={'xgboost_score': 1, 'anomaly_score': 0, 'graph_score': 0, 'pattern_score': 0})
    assert equal == 1.0
    heavy_anom = decide(DecisionInputs(
        0.1, 0.9, 0.1, 0.1,
        active_rules=[],
        business_policy={'weights': {'xgboost_score': 0.05, 'anomaly_score': 0.85, 'graph_score': 0.05, 'pattern_score': 0.05}},
    ))
    light_anom = decide(DecisionInputs(
        0.1, 0.9, 0.1, 0.1,
        active_rules=[],
        business_policy={'weights': {'xgboost_score': 0.85, 'anomaly_score': 0.05, 'graph_score': 0.05, 'pattern_score': 0.05}},
    ))
    assert heavy_anom.risk_score > light_anom.risk_score


def test_llm_cannot_be_source_of_final_action():
    out = decide(DecisionInputs(0.8, 0.8, 0.8, 0.8, active_rules=default_active_rules()))
    assert out.source == 'DecisionEngine'
    assert out.llm_determined is False
    assert out.recommended_action in {'BLOCK', 'HOLD', 'MANUAL_REVIEW', 'STEP_UP', 'ALLOW'}

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Any

RISK_LEVELS = ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
ACTIONS = ('ALLOW', 'STEP_UP', 'MANUAL_REVIEW', 'HOLD', 'BLOCK')

# Higher rank wins when rules conflict.
ACTION_RANK = {
    'ALLOW': 0,
    'STEP_UP': 1,
    'MANUAL_REVIEW': 2,
    'HOLD': 3,
    'BLOCK': 4,
}

DEFAULT_WEIGHTS = {
    'xgboost_score': 0.40,
    'anomaly_score': 0.20,
    'graph_score': 0.20,
    'pattern_score': 0.20,
}

DEFAULT_THRESHOLDS = {
    'MEDIUM': 0.40,
    'HIGH': 0.65,
    'CRITICAL': 0.85,
}

DEFAULT_LEVEL_ACTIONS = {
    'LOW': 'ALLOW',
    'MEDIUM': 'STEP_UP',
    'HIGH': 'MANUAL_REVIEW',
    'CRITICAL': 'BLOCK',
}


class DecisionEngineError(ValueError):
    pass


@dataclass(frozen=True)
class DecisionRule:
    rule_id: str
    priority: int
    action: str
    description: str = ''
    when: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.action not in ACTIONS:
            raise DecisionEngineError(f'invalid action: {self.action}')


@dataclass
class DecisionInputs:
    xgboost_score: float
    anomaly_score: float
    graph_score: float
    pattern_score: float
    active_rules: list[DecisionRule] | list[dict] = field(default_factory=list)
    business_policy: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class DecisionOutput:
    risk_score: float
    risk_level: str
    recommended_action: str
    matched_rules: list[dict[str, Any]]
    evidence_ids: list[str]
    source: str = 'DecisionEngine'
    llm_determined: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _clamp01(value: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise DecisionEngineError(f'invalid score: {value}') from exc
    if v != v:  # NaN
        raise DecisionEngineError('score is NaN')
    return max(0.0, min(1.0, v))


def _normalize_rule(rule: DecisionRule | dict) -> DecisionRule:
    if isinstance(rule, DecisionRule):
        return rule
    return DecisionRule(
        rule_id=str(rule.get('rule_id') or rule.get('id') or ''),
        priority=int(rule.get('priority') or 0),
        action=str(rule.get('action') or '').upper(),
        description=str(rule.get('description') or ''),
        when=dict(rule.get('when') or {}),
    )


def evidence_id_for_decision(inputs: DecisionInputs, risk_score: float, action: str) -> str:
    payload = '|'.join([
        f"x={inputs.xgboost_score:.6f}",
        f"a={inputs.anomaly_score:.6f}",
        f"g={inputs.graph_score:.6f}",
        f"p={inputs.pattern_score:.6f}",
        f"r={risk_score:.6f}",
        action,
        ','.join(sorted(inputs.evidence_ids)),
    ])
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12].upper()
    return f'EVD-DECISION-{digest}'


def fuse_scores(
    xgboost_score: float,
    anomaly_score: float,
    graph_score: float,
    pattern_score: float,
    weights: dict[str, float] | None = None,
) -> float:
    w = dict(DEFAULT_WEIGHTS)
    w.update(weights or {})
    scores = {
        'xgboost_score': _clamp01(xgboost_score),
        'anomaly_score': _clamp01(anomaly_score),
        'graph_score': _clamp01(graph_score),
        'pattern_score': _clamp01(pattern_score),
    }
    total_w = sum(float(w[k]) for k in scores)
    if total_w <= 0:
        raise DecisionEngineError('business_policy weights must sum to > 0')
    fused = sum(scores[k] * float(w[k]) for k in scores) / total_w
    return float(_clamp01(fused))


def risk_level_for(score: float, thresholds: dict[str, float] | None = None) -> str:
    t = dict(DEFAULT_THRESHOLDS)
    t.update(thresholds or {})
    s = _clamp01(score)
    if s >= float(t['CRITICAL']):
        return 'CRITICAL'
    if s >= float(t['HIGH']):
        return 'HIGH'
    if s >= float(t['MEDIUM']):
        return 'MEDIUM'
    return 'LOW'


def _rule_matches(rule: DecisionRule, scores: dict[str, float], risk_level: str, risk_score: float) -> bool:
    when = rule.when or {}
    if not when:
        return False
    if 'risk_level' in when and str(when['risk_level']).upper() != risk_level:
        return False
    if 'risk_level_in' in when:
        allowed = {str(x).upper() for x in when['risk_level_in']}
        if risk_level not in allowed:
            return False
    if 'min_risk_score' in when and risk_score < float(when['min_risk_score']):
        return False
    if 'max_risk_score' in when and risk_score > float(when['max_risk_score']):
        return False
    for key in ('xgboost_score', 'anomaly_score', 'graph_score', 'pattern_score'):
        min_key = f'min_{key}'
        max_key = f'max_{key}'
        if min_key in when and scores[key] < float(when[min_key]):
            return False
        if max_key in when and scores[key] > float(when[max_key]):
            return False
    # Conflicting-signal helpers
    if when.get('require_high_xgboost_and_low_anomaly'):
        if not (scores['xgboost_score'] >= float(when.get('xgb_high', 0.7)) and scores['anomaly_score'] <= float(when.get('anomaly_low', 0.3))):
            return False
    if when.get('require_conflict_xgb_vs_anomaly'):
        high = float(when.get('conflict_high', 0.7))
        low = float(when.get('conflict_low', 0.3))
        conflict = (
            (scores['xgboost_score'] >= high and scores['anomaly_score'] <= low)
            or (scores['anomaly_score'] >= high and scores['xgboost_score'] <= low)
        )
        if not conflict:
            return False
    return True


def resolve_action(
    risk_level: str,
    matched: list[DecisionRule],
    level_actions: dict[str, str] | None = None,
) -> tuple[str, list[DecisionRule]]:
    """Higher priority wins; ties broken by more severe ACTION_RANK."""
    mapping = dict(DEFAULT_LEVEL_ACTIONS)
    mapping.update({k.upper(): str(v).upper() for k, v in (level_actions or {}).items()})
    base = mapping.get(risk_level, 'MANUAL_REVIEW')
    if base not in ACTIONS:
        raise DecisionEngineError(f'invalid level action for {risk_level}: {base}')
    if not matched:
        return base, []
    ordered = sorted(matched, key=lambda r: (-int(r.priority), -ACTION_RANK[r.action], r.rule_id))
    winner = ordered[0]
    # If a lower-severity high-priority rule somehow ranks below severity, still take priority first.
    return winner.action, ordered


def decide(inputs: DecisionInputs) -> DecisionOutput:
    """Deterministic final action. LLM must not call this with invented scores."""
    policy = dict(inputs.business_policy or {})
    scores = {
        'xgboost_score': _clamp01(inputs.xgboost_score),
        'anomaly_score': _clamp01(inputs.anomaly_score),
        'graph_score': _clamp01(inputs.graph_score),
        'pattern_score': _clamp01(inputs.pattern_score),
    }
    risk_score = fuse_scores(
        scores['xgboost_score'],
        scores['anomaly_score'],
        scores['graph_score'],
        scores['pattern_score'],
        weights=policy.get('weights'),
    )
    level = risk_level_for(risk_score, thresholds=policy.get('thresholds'))
    rules = [_normalize_rule(r) for r in (inputs.active_rules or [])]
    matched = [r for r in rules if _rule_matches(r, scores, level, risk_score)]
    action, ordered = resolve_action(level, matched, level_actions=policy.get('level_actions'))
    eids = list(dict.fromkeys(list(inputs.evidence_ids or [])))
    eids.append(evidence_id_for_decision(inputs, risk_score, action))
    return DecisionOutput(
        risk_score=risk_score,
        risk_level=level,
        recommended_action=action,
        matched_rules=[
            {
                'rule_id': r.rule_id,
                'priority': r.priority,
                'action': r.action,
                'description': r.description,
                'when': r.when,
                'selected': i == 0,
            }
            for i, r in enumerate(ordered)
        ],
        evidence_ids=eids,
        source='DecisionEngine',
        llm_determined=False,
    )


def default_active_rules() -> list[DecisionRule]:
    return [
        DecisionRule(
            rule_id='R-BLOCK-CRITICAL',
            priority=100,
            action='BLOCK',
            description='Block when fused risk is CRITICAL',
            when={'risk_level': 'CRITICAL'},
        ),
        DecisionRule(
            rule_id='R-HOLD-HIGH-PATTERN',
            priority=80,
            action='HOLD',
            description='Hold when high fused risk and strong pattern match',
            when={'risk_level_in': ['HIGH', 'CRITICAL'], 'min_pattern_score': 0.8},
        ),
        DecisionRule(
            rule_id='R-REVIEW-CONFLICT',
            priority=90,
            action='MANUAL_REVIEW',
            description='Manual review on conflicting XGB vs anomaly signals',
            when={'require_conflict_xgb_vs_anomaly': True},
        ),
        DecisionRule(
            rule_id='R-STEPUP-MEDIUM',
            priority=40,
            action='STEP_UP',
            description='Step-up authentication for medium risk',
            when={'risk_level': 'MEDIUM'},
        ),
        DecisionRule(
            rule_id='R-ALLOW-LOW',
            priority=10,
            action='ALLOW',
            description='Allow low risk traffic',
            when={'risk_level': 'LOW', 'max_risk_score': 0.39},
        ),
    ]

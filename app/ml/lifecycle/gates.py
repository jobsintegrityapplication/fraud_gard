from __future__ import annotations

from dataclasses import asdict, dataclass
import os


@dataclass(frozen=True)
class PromotionGates:
    precision_min: float = 0.90
    recall_min: float = 0.90
    pr_auc_min: float = float(os.environ.get('PROMOTE_PR_AUC_MIN', '0.70'))
    fpr_max: float = float(os.environ.get('PROMOTE_FPR_MAX', '0.10'))
    fnr_max: float = float(os.environ.get('PROMOTE_FNR_MAX', '0.10'))
    min_test_rows: int = int(os.environ.get('PROMOTE_MIN_TEST_ROWS', '10'))
    min_positives: int = int(os.environ.get('PROMOTE_MIN_POSITIVES', '3'))
    segment_min_precision: float = float(os.environ.get('PROMOTE_SEGMENT_PRECISION', '0.50'))
    segment_min_n: int = int(os.environ.get('PROMOTE_SEGMENT_MIN_N', '8'))
    drift_max_mean_shift: float = float(os.environ.get('PROMOTE_DRIFT_MAX', '3.0'))
    require_governance: bool = True


REQUIRED_GOVERNANCE = (
    'model_id', 'version', 'algorithm', 'dataset_version', 'feature_version',
    'training_timestamp', 'metrics',
)


def _gate(name: str, passed: bool, detail: dict | None = None) -> dict:
    return {'gate': name, 'passed': bool(passed), 'detail': detail or {}}


def evaluate_promotion_gates(
    *,
    metrics: dict,
    split_metrics: dict | None = None,
    segment_metrics: dict | None = None,
    split_sizes: dict | None = None,
    record: dict | None = None,
    drift_report: dict | None = None,
    data_quality: dict | None = None,
    gates: PromotionGates | None = None,
) -> dict:
    """Deterministic promotion evaluation. All gates must pass to replace the champion."""
    gates = gates or PromotionGates()
    results = []

    precision = float(metrics.get('precision') or 0.0)
    recall = float(metrics.get('recall') or 0.0)
    pr_auc = float(metrics.get('pr_auc') or 0.0)
    fpr = float(metrics.get('false_positive_rate') or 0.0)
    fnr = float(metrics.get('false_negative_rate') or 0.0)

    results.append(_gate('precision', precision >= gates.precision_min, {
        'observed': precision, 'threshold': gates.precision_min,
    }))
    results.append(_gate('recall', recall >= gates.recall_min, {
        'observed': recall, 'threshold': gates.recall_min,
    }))
    results.append(_gate('pr_auc', pr_auc >= gates.pr_auc_min, {
        'observed': pr_auc, 'threshold': gates.pr_auc_min,
    }))
    results.append(_gate('fpr', fpr <= gates.fpr_max, {
        'observed': fpr, 'threshold': gates.fpr_max,
    }))
    results.append(_gate('fnr', fnr <= gates.fnr_max, {
        'observed': fnr, 'threshold': gates.fnr_max,
    }))

    sizes = split_sizes or {}
    temporal_ok = (
        int(sizes.get('train') or 0) > 0
        and int(sizes.get('valid') or 0) > 0
        and int(sizes.get('test') or 0) >= gates.min_test_rows
        and bool(split_metrics and 'train' in split_metrics and 'valid' in split_metrics and 'test' in split_metrics)
    )
    results.append(_gate('temporal_validation', temporal_ok, {'split_sizes': sizes}))

    seg_fail = []
    for col, groups in (segment_metrics or {}).items():
        for key, m in (groups or {}).items():
            if int(m.get('n') or 0) < gates.segment_min_n:
                continue
            if float(m.get('precision') or 0.0) < gates.segment_min_precision:
                seg_fail.append({'segment': col, 'value': key, 'precision': m.get('precision'), 'n': m.get('n')})
    results.append(_gate('segment_validation', not seg_fail, {'failures': seg_fail}))

    dq = data_quality or {
        'n_test': int(metrics.get('n') or sizes.get('test') or 0),
        'n_positive': int(metrics.get('n_positive') or 0),
    }
    dq_ok = int(dq.get('n_test') or 0) >= gates.min_test_rows and int(dq.get('n_positive') or 0) >= gates.min_positives
    results.append(_gate('data_quality', dq_ok, dq))

    drift = drift_report or {'ok': True, 'max_mean_shift': 0.0}
    drift_ok = bool(drift.get('ok', True)) and float(drift.get('max_mean_shift') or 0.0) <= gates.drift_max_mean_shift
    results.append(_gate('drift', drift_ok, drift))

    rec = record or {}
    missing = [k for k in REQUIRED_GOVERNANCE if not rec.get(k) and rec.get(k) != 0]
    if isinstance(rec.get('metrics'), dict) and not rec.get('metrics'):
        missing.append('metrics')
    gov_ok = (not missing) if gates.require_governance else True
    results.append(_gate('governance_completeness', gov_ok, {'missing': missing}))

    passed = all(r['passed'] for r in results)
    return {
        'promoted': passed,
        'gates': results,
        'failed_gates': [r['gate'] for r in results if not r['passed']],
        'thresholds': asdict(gates),
        'reason': 'all promotion gates passed' if passed else f"failed gates: {', '.join(r['gate'] for r in results if not r['passed'])}",
    }

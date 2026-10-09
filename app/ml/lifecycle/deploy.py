from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from ... import config
from .registry import (
    get_champion,
    get_model,
    set_champion,
    upsert_model_record,
)

# Deployment-facing states (distinct from training lifecycle TRAINING/VALIDATING/FAILED).
DEPLOYMENT_STATES = (
    'CHAMPION',
    'SHADOW',
    'CANARY',
    'PRODUCTION',
    'ROLLED_BACK',
)

DEFAULT_CANARY_PERCENT = float(os.environ.get('DEPLOY_CANARY_PERCENT', '5'))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _deploy_path() -> Path:
    dest = Path(config.MODEL_DIR) / 'lifecycle'
    dest.mkdir(parents=True, exist_ok=True)
    return dest / 'DEPLOYMENT.json'


@dataclass
class DeploymentConfig:
    """Local MVP deployment plan — traffic % is simulated, not Kubernetes."""

    champion_model_id: str | None = None
    production_model_id: str | None = None
    shadow_model_id: str | None = None
    canary_model_id: str | None = None
    canary_percent: float = DEFAULT_CANARY_PERCENT
    champion_history: list[str] = field(default_factory=list)
    updated_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def get_deployment() -> DeploymentConfig:
    path = _deploy_path()
    if not path.exists():
        champ = get_champion()
        mid = champ['model_id'] if champ else None
        return DeploymentConfig(
            champion_model_id=mid,
            production_model_id=mid,
            canary_percent=DEFAULT_CANARY_PERCENT,
        )
    data = json.loads(path.read_text(encoding='utf-8'))
    return DeploymentConfig(
        champion_model_id=data.get('champion_model_id'),
        production_model_id=data.get('production_model_id') or data.get('champion_model_id'),
        shadow_model_id=data.get('shadow_model_id'),
        canary_model_id=data.get('canary_model_id'),
        canary_percent=float(data.get('canary_percent', DEFAULT_CANARY_PERCENT)),
        champion_history=list(data.get('champion_history') or []),
        updated_at=data.get('updated_at'),
    )


def save_deployment(cfg: DeploymentConfig) -> DeploymentConfig:
    cfg.updated_at = _now()
    # Clamp canary percent for local simulation.
    cfg.canary_percent = max(0.0, min(100.0, float(cfg.canary_percent)))
    _deploy_path().write_text(json.dumps(cfg.to_dict(), indent=2), encoding='utf-8')
    return cfg


def traffic_bucket(transaction_id: str, *, buckets: int = 10000) -> int:
    """Deterministic sticky bucket in [0, buckets) from transaction id (no K8s)."""
    digest = hashlib.sha256(str(transaction_id).encode('utf-8')).hexdigest()
    return int(digest, 16) % buckets


def in_canary_traffic(transaction_id: str, canary_percent: float, *, buckets: int = 10000) -> bool:
    """Simulate canary % of traffic via hash sticky assignment."""
    pct = max(0.0, min(100.0, float(canary_percent)))
    if pct <= 0:
        return False
    if pct >= 100:
        return True
    threshold = int(pct / 100.0 * buckets)
    return traffic_bucket(transaction_id, buckets=buckets) < threshold


def _require_model(model_id: str) -> dict:
    rec = get_model(model_id)
    if rec is None:
        raise KeyError(f'model not found: {model_id}')
    return rec


def _set_status(model_id: str, status: str) -> dict:
    rec = _require_model(model_id)
    return upsert_model_record(
        model_id=rec['model_id'],
        version=rec['version'],
        algorithm=rec['algorithm'],
        dataset_version=rec['dataset_version'] or '',
        feature_version=rec['feature_version'] or '',
        training_timestamp=rec['training_timestamp'] or '',
        metrics=rec['metrics'] or {},
        status=status,
        artifact_uri=rec.get('artifact_uri'),
        gate_results=rec.get('gate_results'),
    )


def sync_from_champion() -> DeploymentConfig:
    """Align deployment production/champion pointers with registry champion."""
    cfg = get_deployment()
    champ = get_champion()
    if champ is None:
        return cfg
    mid = champ['model_id']
    if cfg.champion_model_id and cfg.champion_model_id != mid:
        hist = [h for h in cfg.champion_history if h != cfg.champion_model_id]
        hist.append(cfg.champion_model_id)
        cfg.champion_history = hist[-20:]
    cfg.champion_model_id = mid
    cfg.production_model_id = mid
    if cfg.canary_model_id == mid:
        cfg.canary_model_id = None
    if cfg.shadow_model_id == mid:
        cfg.shadow_model_id = None
    return save_deployment(cfg)


def deploy_shadow(model_id: str) -> dict:
    """Score traffic with this model but never affect production decisions."""
    rec = _require_model(model_id)
    cfg = get_deployment()
    if cfg.production_model_id == model_id or cfg.champion_model_id == model_id:
        raise ValueError('cannot deploy production/champion model as shadow')
    if cfg.canary_model_id == model_id:
        cfg.canary_model_id = None
    updated = _set_status(model_id, 'SHADOW')
    cfg.shadow_model_id = model_id
    save_deployment(cfg)
    return {
        'deployment_state': 'SHADOW',
        'model': updated,
        'affects_decision': False,
        'deployment': get_deployment().to_dict(),
    }


def deploy_canary(model_id: str, percent: float | None = None) -> dict:
    """Route a configurable simulated % of traffic to this model for decisions."""
    rec = _require_model(model_id)
    cfg = get_deployment()
    if cfg.champion_model_id == model_id or cfg.production_model_id == model_id:
        raise ValueError('model is already production/champion; use production deploy')
    pct = DEFAULT_CANARY_PERCENT if percent is None else float(percent)
    updated = _set_status(model_id, 'CANARY')
    if cfg.shadow_model_id == model_id:
        cfg.shadow_model_id = None
    cfg.canary_model_id = model_id
    cfg.canary_percent = pct
    save_deployment(cfg)
    return {
        'deployment_state': 'CANARY',
        'model': updated,
        'canary_percent': cfg.canary_percent,
        'affects_decision': True,
        'note': 'local MVP simulates traffic percentage via sticky hash buckets',
        'deployment': get_deployment().to_dict(),
    }


def deploy_production(model_id: str, *, as_champion: bool = True) -> dict:
    """Make model the active decision model (and champion when as_champion)."""
    _require_model(model_id)
    cfg = get_deployment()
    previous = cfg.production_model_id or cfg.champion_model_id
    if previous and previous != model_id:
        hist = [h for h in cfg.champion_history if h != previous]
        hist.append(previous)
        cfg.champion_history = hist[-20:]
        save_deployment(cfg)
    if as_champion:
        updated = set_champion(model_id, replace_previous=True)
    else:
        if previous and previous != model_id:
            try:
                _set_status(previous, 'ROLLED_BACK')
            except KeyError:
                pass
        updated = _set_status(model_id, 'PRODUCTION')
    cfg = get_deployment()
    cfg.production_model_id = model_id
    if as_champion:
        cfg.champion_model_id = model_id
    if cfg.canary_model_id == model_id:
        cfg.canary_model_id = None
    if cfg.shadow_model_id == model_id:
        cfg.shadow_model_id = None
    save_deployment(cfg)
    return {
        'deployment_state': 'PRODUCTION',
        'champion': as_champion,
        'model': updated,
        'affects_decision': True,
        'previous_production': previous,
        'deployment': get_deployment().to_dict(),
    }


def clear_shadow() -> dict:
    cfg = get_deployment()
    mid = cfg.shadow_model_id
    if mid:
        try:
            _set_status(mid, 'ROLLED_BACK')
        except KeyError:
            pass
    cfg.shadow_model_id = None
    save_deployment(cfg)
    return get_deployment().to_dict()


def clear_canary() -> dict:
    cfg = get_deployment()
    mid = cfg.canary_model_id
    if mid:
        try:
            _set_status(mid, 'ROLLED_BACK')
        except KeyError:
            pass
    cfg.canary_model_id = None
    save_deployment(cfg)
    return get_deployment().to_dict()


def rollback_to_last_known_good() -> dict:
    """Restore the last known-good champion as production; current production → ROLLED_BACK."""
    cfg = get_deployment()
    history = list(cfg.champion_history or [])
    target_id = None
    while history and target_id is None:
        candidate = history.pop()
        if candidate and candidate != cfg.production_model_id and get_model(candidate):
            target_id = candidate
    if target_id is None:
        raise RuntimeError('no last known-good champion available to roll back to')
    current = cfg.production_model_id or cfg.champion_model_id
    cfg.champion_history = history
    save_deployment(cfg)
    result = deploy_production(target_id, as_champion=True)
    result['rolled_back_from'] = current
    result['deployment_state'] = 'CHAMPION'
    result['note'] = 'rolled back to last known-good champion'
    return result


def resolve_decision_route(transaction_id: str, cfg: DeploymentConfig | None = None) -> dict:
    """
    Choose which model affects the production decision.
    Shadow is never returned here — it does not affect decisions.
    """
    cfg = cfg or get_deployment()
    if cfg.canary_model_id and in_canary_traffic(transaction_id, cfg.canary_percent):
        return {
            'role': 'CANARY',
            'model_id': cfg.canary_model_id,
            'affects_decision': True,
            'canary_percent': cfg.canary_percent,
            'bucket': traffic_bucket(transaction_id),
        }
    prod = cfg.production_model_id or cfg.champion_model_id
    return {
        'role': 'PRODUCTION' if prod else None,
        'model_id': prod,
        'champion_model_id': cfg.champion_model_id,
        'affects_decision': True,
        'canary_percent': cfg.canary_percent,
        'bucket': traffic_bucket(transaction_id),
    }


def _load_artifact_for_model(model_id: str):
    from ..supervised.model import load_artifact

    rec = _require_model(model_id)
    version = rec.get('version')
    if not version:
        raise FileNotFoundError(f'no version for model {model_id}')
    return load_artifact(version), rec


def score_with_deployment(
    history: pd.DataFrame,
    transaction_id: str,
    *,
    top_k: int = 5,
) -> dict:
    """
    Score using deployment routing.
    - Decision score comes from PRODUCTION or CANARY (affects decisions).
    - SHADOW is scored when configured but never used for decisions.
    """
    from ..supervised.model import score_one

    cfg = get_deployment()
    route = resolve_decision_route(transaction_id, cfg)
    if not route.get('model_id'):
        raise FileNotFoundError('no production/champion model deployed')

    art, rec = _load_artifact_for_model(route['model_id'])
    decision = score_one(history, transaction_id, art, top_k=top_k)
    decision['deployment'] = {
        'role': route['role'],
        'model_id': route['model_id'],
        'version': rec.get('version'),
        'affects_decision': True,
        'canary_percent': cfg.canary_percent,
        'bucket': route.get('bucket'),
        'champion_model_id': cfg.champion_model_id,
    }

    shadow_payload = None
    if cfg.shadow_model_id and cfg.shadow_model_id != route['model_id']:
        try:
            s_art, s_rec = _load_artifact_for_model(cfg.shadow_model_id)
            shadow_score = score_one(history, transaction_id, s_art, top_k=top_k)
            shadow_payload = {
                'role': 'SHADOW',
                'model_id': cfg.shadow_model_id,
                'version': s_rec.get('version'),
                'affects_decision': False,
                'risk_score': shadow_score['risk_score'],
                'risk_level': shadow_score['risk_level'],
                'model_version': shadow_score['model_version'],
                'top_features': shadow_score.get('top_features'),
                'note': 'shadow score is observational only; production decisions ignore it',
            }
        except (FileNotFoundError, KeyError, OSError) as exc:
            shadow_payload = {
                'role': 'SHADOW',
                'model_id': cfg.shadow_model_id,
                'affects_decision': False,
                'error': str(exc),
            }
    decision['shadow'] = shadow_payload
    return decision


def deployment_status() -> dict[str, Any]:
    cfg = get_deployment()
    models = {}
    for role, mid in (
        ('champion', cfg.champion_model_id),
        ('production', cfg.production_model_id),
        ('canary', cfg.canary_model_id),
        ('shadow', cfg.shadow_model_id),
    ):
        models[role] = get_model(mid) if mid else None
    return {
        'states': list(DEPLOYMENT_STATES),
        'config': cfg.to_dict(),
        'models': models,
        'routing': {
            'decision': 'CANARY if sticky bucket in canary_percent else PRODUCTION',
            'shadow': 'scored when configured; never affects decisions',
            'simulation': 'hash(transaction_id) sticky buckets — no Kubernetes required',
        },
    }

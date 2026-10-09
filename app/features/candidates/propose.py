from __future__ import annotations

import re
import uuid
from typing import Any

from ..registry import production_registry
from ..windows import LEAKAGE_COLUMNS
from .lifecycle import INITIAL_STATE
from .templates import TEMPLATE_KEYS


def new_candidate_id() -> str:
    return 'CF-' + uuid.uuid4().hex[:10].upper()


def _slug(text: str, prefix: str = 'cand') -> str:
    base = re.sub(r'[^a-z0-9]+', '_', str(text).lower()).strip('_')
    base = (base or prefix)[:40]
    return f'{prefix}_{base}'


def _base(
    *,
    name: str,
    description: str,
    origin: str,
    origin_ref: str,
    template: str,
    params: dict,
    entity_level: str = 'TRANSACTION',
    description_source: str = 'rules',
) -> dict[str, Any]:
    if template not in TEMPLATE_KEYS:
        raise ValueError(f'template not allowed: {template}')
    if name in LEAKAGE_COLUMNS or any(x in name.lower() for x in ('label', 'ground_truth', 'fraud_reason')):
        raise ValueError(f'refused leakage-like candidate name: {name}')
    return {
        'candidate_id': new_candidate_id(),
        'name': name,
        'description': description,
        'description_source': description_source,
        'status': INITIAL_STATE,
        'origin': origin,
        'origin_ref': origin_ref,
        'template': template,
        'params': params,
        'entity_level': entity_level,
        'validation': {},
        'production': False,
        'llm_may_edit': ['description'],
    }


def propose_from_patterns(patterns: list[dict] | None = None) -> list[dict]:
    """Confirmed / ACTIVE fraud patterns → signature feature mean candidates."""
    out = []
    for pat in patterns or []:
        state = str(pat.get('state') or '').upper()
        if state not in {'CONFIRMED', 'ACTIVE', 'MONITORED'}:
            continue
        sig = pat.get('signature') or {}
        distinctive = sig.get('distinctive_features') or sig.get('top_features') or []
        names = []
        for item in distinctive:
            if isinstance(item, dict):
                fname = item.get('feature') or item.get('name')
            else:
                fname = str(item)
            if fname and fname not in LEAKAGE_COLUMNS:
                names.append(fname)
        prod = set(production_registry().names())
        names = [n for n in names if n in prod][:6]
        if len(names) < 2:
            # Fall back to a stable pattern-related temporal/entity combo.
            names = ['transaction_velocity', 'device_switch_velocity']
        pid = pat.get('pattern_id') or pat.get('id') or 'pattern'
        out.append(_base(
            name=_slug(f"pat_{pid}", 'pat'),
            description=f"Mean of signature features for pattern {pid} (deterministic template)",
            origin='confirmed_fraud_pattern',
            origin_ref=str(pid),
            template='pattern_signature_mean',
            params={'features': names},
            entity_level='TRANSACTION',
        ))
    return out


def propose_from_entity_relationships() -> list[dict]:
    return [
        _base(
            name='cand_entity_link_strength',
            description='Product of connected_accounts and fraud_neighbor_count',
            origin='entity_relationships',
            origin_ref='connected_accounts*fraud_neighbor_count',
            template='entity_link_strength',
            params={'left': 'connected_accounts', 'right': 'fraud_neighbor_count'},
            entity_level='GRAPH',
        ),
        _base(
            name='cand_device_sim_overlap',
            description='Product of accounts_per_imei and devices_per_sim',
            origin='entity_relationships',
            origin_ref='accounts_per_imei*devices_per_sim',
            template='product',
            params={'left': 'accounts_per_imei', 'right': 'devices_per_sim'},
            entity_level='DEVICE',
        ),
    ]


def propose_from_feature_combinations(pairs: list[tuple[str, str]] | None = None) -> list[dict]:
    defaults = pairs or [
        ('purchases_last_1h', 'purchases_last_24h'),
        ('amount_deviation', 'transaction_velocity'),
        ('merchant_fraud_rate', 'customer_merchant_frequency'),
    ]
    prod = set(production_registry().names())
    out = []
    for left, right in defaults:
        if left not in prod or right not in prod:
            continue
        out.append(_base(
            name=_slug(f'{left}_over_{right}', 'combo'),
            description=f'Deterministic ratio of {left} / {right}',
            origin='feature_combination',
            origin_ref=f'{left}/{right}',
            template='ratio',
            params={'left': left, 'right': right},
        ))
        out.append(_base(
            name=_slug(f'{left}_x_{right}', 'combo'),
            description=f'Deterministic product of {left} * {right}',
            origin='feature_combination',
            origin_ref=f'{left}*{right}',
            template='product',
            params={'left': left, 'right': right},
        ))
    return out


def propose_from_temporal_behavior() -> list[dict]:
    return [
        _base(
            name='cand_temporal_burst_ratio',
            description='Short-window purchases over long-window purchases',
            origin='temporal_behavior',
            origin_ref='purchases_last_1h/purchases_last_24h',
            template='temporal_burst_ratio',
            params={'short': 'purchases_last_1h', 'long': 'purchases_last_24h'},
            entity_level='TEMPORAL',
        ),
        _base(
            name='cand_hour_amount_interaction',
            description='Amount interacted with unusual night-hour indicator',
            origin='temporal_behavior',
            origin_ref='hour_of_day*amount',
            template='hour_amount_interaction',
            params={'hour': 'hour_of_day', 'amount': 'amount'},
            entity_level='TEMPORAL',
        ),
    ]


def generate_proposals(
    *,
    patterns: list[dict] | None = None,
    include_patterns: bool = True,
    include_entity: bool = True,
    include_combinations: bool = True,
    include_temporal: bool = True,
) -> list[dict]:
    proposals: list[dict] = []
    if include_patterns:
        proposals.extend(propose_from_patterns(patterns))
    if include_entity:
        proposals.extend(propose_from_entity_relationships())
    if include_combinations:
        proposals.extend(propose_from_feature_combinations())
    if include_temporal:
        proposals.extend(propose_from_temporal_behavior())
    # Deduplicate by name (keep first).
    seen = set()
    unique = []
    for p in proposals:
        if p['name'] in seen:
            continue
        seen.add(p['name'])
        unique.append(p)
    return unique

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

NODE_TYPES = (
    'Customer', 'Account', 'SIM', 'IMEI', 'IP', 'Merchant', 'Location', 'Transaction', 'FraudCase',
)

REL_TYPES = (
    'OWNS', 'USES', 'PURCHASED', 'CONNECTED_FROM', 'VISITED', 'PAID_TO', 'LINKED_TO_FRAUD',
)

DISCLAIMER = (
    'Graph linkage is correlational. Shared identifiers and community membership are risk signals, '
    'not proof of fraud.'
)


def node_key(node_type: str, raw_id: str) -> str:
    return f'{node_type}:{raw_id}'


def split_key(key: str) -> tuple[str, str]:
    if ':' not in key:
        return '', key
    typ, raw = key.split(':', 1)
    return typ, raw


@dataclass
class GraphNode:
    id: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {'id': self.id, 'type': self.type, 'properties': self.properties}


@dataclass
class GraphEdge:
    source: str
    type: str
    target: str
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {'source': self.source, 'type': self.type, 'target': self.target, 'properties': self.properties}


@dataclass
class GraphSnapshot:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {'nodes': [n.to_dict() for n in self.nodes], 'edges': [e.to_dict() for e in self.edges]}

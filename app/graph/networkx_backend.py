from __future__ import annotations

from collections import deque

import networkx as nx

from .backend import GraphBackend
from .schema import GraphEdge, GraphNode, GraphSnapshot, node_key, split_key


class NetworkXGraphBackend(GraphBackend):
    name = 'networkx'

    def __init__(self):
        self.graph = nx.MultiDiGraph()

    def upsert_node(self, node_type: str, raw_id: str, properties: dict | None = None) -> str:
        if raw_id is None or str(raw_id).strip() == '' or str(raw_id).lower() == 'nan':
            return ''
        self.validate_types(node_type=node_type)
        ident = str(raw_id)
        key = node_key(node_type, ident)
        props = dict(properties or {})
        props.setdefault('raw_id', ident)
        if self.graph.has_node(key):
            self.graph.nodes[key].update(props)
            self.graph.nodes[key]['type'] = node_type
        else:
            self.graph.add_node(key, type=node_type, **props)
        return key

    def upsert_edge(self, source: str, rel_type: str, target: str, properties: dict | None = None) -> None:
        if not source or not target:
            return
        self.validate_types(rel_type=rel_type)
        if not self.graph.has_node(source) or not self.graph.has_node(target):
            return
        for _, _, data in self.graph.out_edges(source, data=True):
            if data.get('type') == rel_type and data.get('_target') == target:
                data.update(properties or {})
                return
        self.graph.add_edge(source, target, type=rel_type, _target=target, **(properties or {}))

    def get_node(self, node_id: str) -> GraphNode | None:
        if not self.graph.has_node(node_id):
            return None
        data = dict(self.graph.nodes[node_id])
        typ = data.pop('type', split_key(node_id)[0])
        return GraphNode(id=node_id, type=typ, properties=data)

    def resolve(self, entity_id: str) -> GraphNode | None:
        if not entity_id:
            return None
        raw = str(entity_id)
        direct = self.get_node(raw)
        if direct:
            return direct
        matches = [n for n in self.graph.nodes if n == raw or n.endswith(':' + raw) or split_key(n)[1] == raw]
        if not matches:
            return None
        priority = ['Transaction', 'Account', 'Customer', 'IMEI', 'SIM', 'IP', 'FraudCase', 'Merchant', 'Location']
        matches.sort(key=lambda k: priority.index(split_key(k)[0]) if split_key(k)[0] in priority else 99)
        return self.get_node(matches[0])

    def neighbors(self, node_id: str, rel_type: str | None = None, direction: str = 'both') -> list[tuple[GraphEdge, GraphNode]]:
        out: list[tuple[GraphEdge, GraphNode]] = []
        if not self.graph.has_node(node_id):
            return out

        def _take(src, dst, data):
            if rel_type and data.get('type') != rel_type:
                return
            edge = GraphEdge(source=src, type=data.get('type', ''), target=dst, properties={k: v for k, v in data.items() if k not in {'type', '_target'}})
            node = self.get_node(dst if src == node_id else src)
            if node:
                out.append((edge, node))

        if direction in {'out', 'both'}:
            for _, dst, data in self.graph.out_edges(node_id, data=True):
                _take(node_id, dst, data)
        if direction in {'in', 'both'}:
            for src, _, data in self.graph.in_edges(node_id, data=True):
                _take(src, node_id, data)
        return out

    def subgraph(self, node_id: str, depth: int = 2) -> GraphSnapshot:
        if not self.graph.has_node(node_id):
            return GraphSnapshot()
        seen = {node_id}
        q = deque([(node_id, 0)])
        while q:
            cur, d = q.popleft()
            if d >= depth:
                continue
            for edge, nbr in self.neighbors(cur):
                other = edge.target if edge.source == cur else edge.source
                if other not in seen:
                    seen.add(other)
                    q.append((other, d + 1))
        nodes = [self.get_node(n) for n in seen]
        edges = []
        for src, dst, data in self.graph.edges(data=True):
            if src in seen and dst in seen:
                edges.append(GraphEdge(
                    source=src, type=data.get('type', ''), target=dst,
                    properties={k: v for k, v in data.items() if k not in {'type', '_target'}},
                ))
        return GraphSnapshot(nodes=[n for n in nodes if n], edges=edges)

    def snapshot(self) -> GraphSnapshot:
        nodes = [self.get_node(n) for n in self.graph.nodes]
        edges = [
            GraphEdge(source=s, type=d.get('type', ''), target=t, properties={k: v for k, v in d.items() if k not in {'type', '_target'}})
            for s, t, d in self.graph.edges(data=True)
        ]
        return GraphSnapshot(nodes=[n for n in nodes if n], edges=edges)

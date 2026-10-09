from __future__ import annotations

from abc import ABC, abstractmethod

from .schema import GraphEdge, GraphNode, GraphSnapshot, NODE_TYPES, REL_TYPES


class GraphBackend(ABC):
    """Storage/query interface. NetworkX is the MVP; Neo4j can implement this later."""

    name: str = 'abstract'

    @abstractmethod
    def upsert_node(self, node_type: str, raw_id: str, properties: dict | None = None) -> str:
        raise NotImplementedError

    @abstractmethod
    def upsert_edge(self, source: str, rel_type: str, target: str, properties: dict | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_node(self, node_id: str) -> GraphNode | None:
        raise NotImplementedError

    @abstractmethod
    def resolve(self, entity_id: str) -> GraphNode | None:
        raise NotImplementedError

    @abstractmethod
    def neighbors(self, node_id: str, rel_type: str | None = None, direction: str = 'both') -> list[tuple[GraphEdge, GraphNode]]:
        raise NotImplementedError

    @abstractmethod
    def subgraph(self, node_id: str, depth: int = 2) -> GraphSnapshot:
        raise NotImplementedError

    @abstractmethod
    def snapshot(self) -> GraphSnapshot:
        raise NotImplementedError

    def validate_types(self, node_type: str | None = None, rel_type: str | None = None):
        if node_type and node_type not in NODE_TYPES:
            raise ValueError(f'unknown node type: {node_type}')
        if rel_type and rel_type not in REL_TYPES:
            raise ValueError(f'unknown relationship type: {rel_type}')


class GraphBackendNotConfigured(RuntimeError):
    pass
